from __future__ import annotations

import hashlib
import re
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.audit import record
from app.policy.privacy import can_use_browser_network, can_use_browser_writes
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


SENSITIVE_SELECTOR_TOKENS = {"password", "pwd", "passwd", "credit", "card", "cvv", "cvc", "ssn", "支付", "密码"}


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    return url


def _settings(context: dict[str, Any]):
    return context["settings"]


def _network_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_network(_settings(context))
    return decision.allowed, decision.reason


def _extract_page(html: str, url: str, max_chars: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:max_chars]
    links = []
    for anchor in soup.find_all("a", href=True)[:80]:
        label = anchor.get_text(" ", strip=True)[:120]
        href = urljoin(url, str(anchor.get("href")))
        if href.startswith(("http://", "https://")):
            links.append({"title": label or href, "url": href})
    return {"ok": True, "url": url, "title": title, "text": text, "links": links, "truncated": len(text) >= max_chars}


def open_url(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "url": url}
    webbrowser.open(url, new=2)
    record("browser.open_url", "BrowserAgent", {"url": url})
    return {"ok": True, "url": url, "opened": True}


def read_page(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    max_chars = int(args.get("max_chars") or getattr(_settings(context), "browser_max_page_bytes", 250000))
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            html = page.content()
            final_url = page.url
            browser.close()
        data = _extract_page(html, final_url, max_chars)
        data["adapter"] = "playwright"
    except Exception as exc:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "MarvisAgent/0.1"})
            response.raise_for_status()
            html = response.text
        data = _extract_page(html, str(response.url), max_chars)
        data["adapter"] = "httpx"
        data["playwright_error"] = str(exc)
    record("browser.read_page", "BrowserAgent", {"url": url, "title": data.get("title", "")})
    return data


def summarize_page(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    page = read_page(args, context)
    if not page.get("ok"):
        return page
    return {"summary": page.get("text", "")[:800]}


def screenshot(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    out_dir = Path(getattr(_settings(context), "browser_screenshot_dir", "") or Path.cwd() / ".marvis_data" / "browser_screenshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".png"
    out_path = out_dir / filename
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": int(args.get("width", 1280)), "height": int(args.get("height", 800))})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.screenshot(path=str(out_path), full_page=bool(args.get("full_page", True)))
            title = page.title()
            final_url = page.url
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"Playwright screenshot failed: {exc}"}
    record("browser.screenshot", "BrowserAgent", {"url": url, "path": str(out_path)})
    return {"ok": True, "url": final_url, "title": title, "path": str(out_path)}


def search_web_via_provider(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"ok": False, "error": "Missing query."}
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    page = read_page({"url": url, "max_chars": 8000}, context)
    if not page.get("ok"):
        return page
    results = []
    for link in page.get("links", []):
        href = str(link.get("url", ""))
        if "bing.com" in urlparse(href).netloc:
            continue
        results.append(link)
        if len(results) >= 10:
            break
    return {"ok": True, "query": query, "results": results, "source": "browser_search"}


def extract_links(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    page = read_page(args, context)
    if not page.get("ok"):
        return page
    return {"ok": True, "url": page.get("url"), "title": page.get("title"), "links": page.get("links", [])}


def _check_write_permission(context: dict[str, Any]) -> tuple[bool, str]:
    settings = _settings(context)
    decision = can_use_browser_writes(settings)
    return decision.allowed, decision.reason


def _sensitive_selector(selector: str) -> bool:
    lowered = (selector or "").lower()
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS)


def navigate(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _check_write_permission(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    if args.get("dry_run", True):
        return {"ok": True, "dry_run": True, "url": url, "diff_preview": [{"action": "navigate", "url": url}]}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            final_url = page.url
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"navigate failed: {exc}"}
    record("browser.navigate", "BrowserAgent", {"url": url})
    return {"ok": True, "url": final_url, "title": title}


def click_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _check_write_permission(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    selector = str(args.get("selector", ""))
    if not selector:
        return {"ok": False, "error": "selector is required"}
    if _sensitive_selector(selector):
        return {"ok": False, "error": f"selector '{selector}' looks sensitive; user must click manually."}
    if args.get("dry_run", True):
        return {
            "ok": True,
            "dry_run": True,
            "diff_preview": [{"action": "click", "selector": selector, "url": url}],
        }
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.click(selector, timeout=8000)
            final_url = page.url
            title = page.title()
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"click failed: {exc}"}
    record("browser.click_element", "BrowserAgent", {"selector": selector, "url": url})
    return {
        "ok": True,
        "url": final_url,
        "title": title,
        "changed_paths": [],
        "rollback_info": {},
    }


def fill_form(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _check_write_permission(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    fields = args.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return {"ok": False, "error": "fields dict is required"}
    for name in fields.keys():
        if _sensitive_selector(name):
            return {"ok": False, "error": f"field '{name}' is sensitive; user must fill manually."}
    if args.get("dry_run", True):
        preview = [{"action": "fill", "field_name": key, "value": "***"} for key in fields.keys()]
        return {"ok": True, "dry_run": True, "diff_preview": preview, "url": url}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for selector, value in fields.items():
                page.fill(selector, str(value), timeout=8000)
            final_url = page.url
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"fill failed: {exc}"}
    record("browser.fill_form", "BrowserAgent", {"url": url, "fields": list(fields.keys())})
    return {"ok": True, "url": final_url, "changed_paths": [], "rollback_info": {}}


def submit_form(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _check_write_permission(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    selector = str(args.get("selector", "form"))
    if _sensitive_selector(selector):
        return {"ok": False, "error": f"selector '{selector}' looks sensitive; user must submit manually."}
    if args.get("dry_run", True):
        return {"ok": True, "dry_run": True, "diff_preview": [{"action": "submit", "selector": selector, "url": url}]}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.evaluate("(sel) => { const el = document.querySelector(sel); if (el && el.submit) el.submit(); }", selector)
            final_url = page.url
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"submit failed: {exc}"}
    record("browser.submit_form", "BrowserAgent", {"url": url, "selector": selector})
    return {"ok": True, "url": final_url, "changed_paths": [], "rollback_info": {}}


def wait_for_selector(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    selector = str(args.get("selector", ""))
    timeout = int(args.get("timeout_ms") or 10000)
    if not selector:
        return {"ok": False, "error": "selector is required"}
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(selector, timeout=timeout)
            present = True
            browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"wait_for failed: {exc}"}
    return {"ok": True, "url": url, "selector": selector, "present": present}


def register(registry) -> None:
    defs = [
        ("browser.open_url", open_url, RiskLevel.R1_OPEN_ONLY, True),
        ("browser.read_page", read_page, RiskLevel.R0_READ_ONLY, False),
        ("browser.summarize_page", summarize_page, RiskLevel.R0_READ_ONLY, False),
        ("browser.screenshot", screenshot, RiskLevel.R0_READ_ONLY, False),
        ("browser.search_web_via_provider", search_web_via_provider, RiskLevel.R0_READ_ONLY, False),
        ("browser.extract_links", extract_links, RiskLevel.R0_READ_ONLY, False),
        ("browser.navigate", navigate, RiskLevel.R1_OPEN_ONLY, True),
        ("browser.wait_for_selector", wait_for_selector, RiskLevel.R0_READ_ONLY, False),
        ("browser.click_element", click_element, RiskLevel.R2_REVERSIBLE_MODIFY, True),
        ("browser.fill_form", fill_form, RiskLevel.R2_REVERSIBLE_MODIFY, True),
        ("browser.submit_form", submit_form, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
    ]
    for name, fn, risk, dry_run in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
                output_schema={},
                risk_level=risk,
                agent_owner="BrowserAgent",
                supports_dry_run=dry_run,
                requires_authorized_path=False,
                execute=fn,
            )
        )
