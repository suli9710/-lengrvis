from __future__ import annotations

import asyncio
import webbrowser
from typing import Any
from urllib.parse import quote_plus, urlparse

from app.core.audit import record
from app.llm.cua_provider import CUAProvider, resolve_cua_provider
from app.policy.privacy import can_use_browser_network, can_use_browser_writes
from app.policy.redaction import redact_text
from app.policy.risk import RiskLevel
from app.policy.sensitive_values import looks_sensitive_value
from app.services.browser_activity_runtime import BrowserActivityAdapter, BrowserActivityRuntime
from app.tools.schemas import ToolDefinition
from app.tools.tool_catalog import tool_description, tool_search_hint

SENSITIVE_SELECTOR_TOKENS = {"password", "pwd", "passwd", "credit", "card", "cvv", "cvc", "ssn", "支付", "密码"}

EXTRA_SENSITIVE_SELECTOR_TOKENS = {
    "payment",
    "pay",
    "order",
    "delete",
    "token",
    "cookie",
    "otp",
    "2fa",
    "passcode",
    "auth",
    "credential",
}

_BROWSER_ACTIVITY_RUNTIME: BrowserActivityRuntime | None = None


def get_browser_activity_runtime() -> BrowserActivityRuntime:
    global _BROWSER_ACTIVITY_RUNTIME
    if _BROWSER_ACTIVITY_RUNTIME is None:
        _BROWSER_ACTIVITY_RUNTIME = BrowserActivityRuntime()
    return _BROWSER_ACTIVITY_RUNTIME


def reset_browser_activity_runtime(adapter: BrowserActivityAdapter | None = None) -> BrowserActivityRuntime:
    global _BROWSER_ACTIVITY_RUNTIME
    _BROWSER_ACTIVITY_RUNTIME = BrowserActivityRuntime(adapter=adapter)
    return _BROWSER_ACTIVITY_RUNTIME


def _validate_url(url: str) -> str:
    # Shared validator: scheme allowlist plus loopback/private/link-local SSRF
    # blocking (see browser_activity_runtime._validate_url). This also covers
    # the use_system_browser path before webbrowser.open().
    from app.services.browser_activity_runtime import _validate_url as _runtime_validate_url

    return _runtime_validate_url(url)


def _settings(context: dict[str, Any]):
    return context["settings"]


def _network_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_network(_settings(context))
    return decision.allowed, decision.reason


def _redact_browser_preview_url(url: str) -> str:
    parsed = urlparse(redact_text(url))
    if parsed.query:
        return parsed._replace(query="***").geturl()
    return parsed.geturl()


def _redacted_legacy_dry_run_event(
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    kind: str,
    url: str,
    selector: str | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    action: dict[str, Any] = {"kind": kind, "url": url, "dry_run": True}
    if selector is not None:
        action["selector"] = selector
    if fields is not None:
        action["fields"] = fields
    get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": action,
            "dry_run": True,
        },
        context,
    )


def open_url(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "url": _redact_browser_preview_url(url)}
    runtime = get_browser_activity_runtime()
    started = runtime.session_start(
        {
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "url": url,
            "mode": args.get("mode") or "watch",
        },
        context,
    )
    if args.get("use_system_browser") is True:
        webbrowser.open(url, new=2)
    record("browser.open_url", "BrowserAgent", {"url": url, "use_system_browser": bool(args.get("use_system_browser"))})
    result = {"ok": True, "url": _redact_browser_preview_url(url), "opened": True, "isolated_session": True}
    if started.get("ok"):
        result["session_id"] = started["session"]["id"]
    return result


def read_page(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    runtime = get_browser_activity_runtime()
    data = runtime.observe(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "url": url,
            "max_chars": args.get("max_chars"),
        },
        context,
    )
    if not data.get("ok"):
        return data
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
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {
                "kind": "screenshot",
                "url": url,
                "width": args.get("width", 1280),
                "height": args.get("height", 800),
                "full_page": args.get("full_page", True),
            },
            "dry_run": False,
        },
        context,
    )
    if result.get("ok"):
        record(
            "browser.screenshot",
            "BrowserAgent",
            {"url": url, "path": result.get("path") or result.get("screenshot_url")},
        )
    return result


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
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS | EXTRA_SENSITIVE_SELECTOR_TOKENS)


def _sensitive_value(value: Any) -> bool:
    lowered = str(value or "").lower()
    return any(
        token in lowered for token in SENSITIVE_SELECTOR_TOKENS | EXTRA_SENSITIVE_SELECTOR_TOKENS
    ) or looks_sensitive_value(value)


def _has_approval(args: dict[str, Any]) -> bool:
    return bool(args.get("approved") and args.get("approval_id"))


def _approval_error(action: str) -> dict[str, Any]:
    return {"ok": False, "error": f"browser.{action} requires an approved approval_id after dry-run preview."}


def session_start(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().session_start(args, context)


def session_close(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().session_close(args, context)


def session_info(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().session_info(args, context)


def sessions(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().sessions(args, context)


def session_events(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().events(args, context)


def observe(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().observe(args, context)


def act(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().act(args, context)


async def cua_run_async(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    instruction = str(args.get("instruction") or args.get("text") or "").strip()
    if not instruction:
        return {"ok": False, "error": "instruction is required"}
    if args.get("dry_run", True):
        safe_url = _redact_browser_preview_url(str(args.get("url") or "https://example.com")) if args.get("url") else ""
        return {
            "ok": True,
            "dry_run": True,
            "risk_level": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
            "verdict": "needs_user_approval",
            "diff_preview": [{"action": "cua", "instruction": "***", "url": safe_url}],
        }
    if not _has_approval(args):
        return _approval_error("cua_run")
    provider_or_error = await resolve_cua_provider(_settings(context), mode=str(args.get("provider_mode") or "auto"))
    if not isinstance(provider_or_error, CUAProvider):
        return provider_or_error
    result = await provider_or_error.run_step(
        instruction=instruction,
        screenshot=args.get("screenshot"),
        previous_response_id=args.get("previous_response_id"),
        acknowledged_safety_checks=args.get("acknowledged_safety_checks"),
        environment=str(args.get("environment") or "browser"),
    )
    if result.get("status") == "requires_approval":
        return {**result, "requires_approval": True}
    activity = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {
                "kind": "cua",
                "url": args.get("url"),
                "text": instruction,
                "provider": result.get("provider"),
                "model": result.get("model"),
                "response_id": result.get("response_id"),
            },
            "dry_run": True,
        },
        context,
    )
    return {**result, "activity": activity}


def cua_run(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    return asyncio.run(cua_run_async(args, context))


def replay_export(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return get_browser_activity_runtime().replay_export(args, context)


def navigate(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    if args.get("dry_run", True):
        safe_url = _redact_browser_preview_url(url)
        _redacted_legacy_dry_run_event(args, context, kind="navigate", url=url)
        return {"ok": True, "dry_run": True, "url": safe_url, "diff_preview": [{"action": "navigate", "url": safe_url}]}
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {"kind": "navigate", "url": url},
            "dry_run": False,
        },
        context,
    )
    if not result.get("ok"):
        return result
    record("browser.navigate", "BrowserAgent", {"url": result.get("url")})
    result["url"] = redact_text(str(result.get("url") or ""))
    return result


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
        safe_url = _redact_browser_preview_url(url)
        _redacted_legacy_dry_run_event(args, context, kind="click", url=url, selector=selector)
        return {
            "ok": True,
            "dry_run": True,
            "diff_preview": [{"action": "click", "selector": "***", "url": safe_url}],
        }
    if not _has_approval(args):
        return _approval_error("click_element")
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {"kind": "click", "url": url, "selector": selector},
            "dry_run": False,
            "approved": args.get("approved"),
            "approval_id": args.get("approval_id"),
        },
        context,
    )
    if not result.get("ok"):
        return result
    record("browser.click_element", "BrowserAgent", {"selector": "***", "url": url})
    return {
        "ok": True,
        "url": result.get("url", url),
        "title": result.get("title", ""),
        "changed_paths": [],
        "rollback_info": {},
        "event": result.get("event"),
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
    for value in fields.values():
        if _sensitive_value(value):
            return {"ok": False, "error": "field value looks sensitive; user must fill manually."}
    if args.get("dry_run", True):
        safe_url = _redact_browser_preview_url(url)
        preview = [{"action": "fill", "field_name": "***", "value": "***"} for _key in fields.keys()]
        _redacted_legacy_dry_run_event(args, context, kind="fill", url=url, fields=fields)
        return {"ok": True, "dry_run": True, "diff_preview": preview, "url": safe_url}
    if not _has_approval(args):
        return _approval_error("fill_form")
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {"kind": "fill", "url": url, "fields": fields},
            "dry_run": False,
            "approved": args.get("approved"),
            "approval_id": args.get("approval_id"),
        },
        context,
    )
    if not result.get("ok"):
        return result
    record("browser.fill_form", "BrowserAgent", {"url": url, "fields": "***"})
    return {
        "ok": True,
        "url": result.get("url", url),
        "changed_paths": [],
        "rollback_info": {},
        "event": result.get("event"),
    }


def submit_form(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _check_write_permission(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    selector = str(args.get("selector", "form"))
    if _sensitive_selector(selector):
        return {"ok": False, "error": f"selector '{selector}' looks sensitive; user must submit manually."}
    if args.get("dry_run", True):
        safe_url = _redact_browser_preview_url(url)
        _redacted_legacy_dry_run_event(args, context, kind="submit", url=url, selector=selector)
        return {"ok": True, "dry_run": True, "diff_preview": [{"action": "submit", "selector": "***", "url": safe_url}]}
    if not _has_approval(args):
        return _approval_error("submit_form")
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {"kind": "submit", "url": url, "selector": selector},
            "dry_run": False,
            "approved": args.get("approved"),
            "approval_id": args.get("approval_id"),
        },
        context,
    )
    if not result.get("ok"):
        return result
    record("browser.submit_form", "BrowserAgent", {"url": url, "selector": "***"})
    return {
        "ok": True,
        "url": result.get("url", url),
        "changed_paths": [],
        "rollback_info": {},
        "event": result.get("event"),
    }


def wait_for_selector(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = _network_allowed(context)
    if not allowed:
        return {"ok": False, "error": reason}
    url = _validate_url(str(args.get("url", "")))
    selector = str(args.get("selector", ""))
    timeout = int(args.get("timeout_ms") or 10000)
    if not selector:
        return {"ok": False, "error": "selector is required"}
    result = get_browser_activity_runtime().act(
        {
            "session_id": args.get("session_id"),
            "task_id": args.get("task_id") or context.get("task_id"),
            "step_id": args.get("step_id") or context.get("step_id"),
            "action": {"kind": "wait", "url": url, "selector": selector, "timeout_ms": timeout},
            "dry_run": False,
        },
        context,
    )
    if not result.get("ok"):
        return result
    result.setdefault("selector", selector)
    result.setdefault("present", True)
    return result


def register(registry) -> None:
    defs = [
        ("browser.session_start", session_start, RiskLevel.R1_OPEN_ONLY, False),
        ("browser.session_close", session_close, RiskLevel.R0_READ_ONLY, False),
        ("browser.session_info", session_info, RiskLevel.R0_READ_ONLY, False),
        ("browser.sessions", sessions, RiskLevel.R0_READ_ONLY, False),
        ("browser.events", session_events, RiskLevel.R0_READ_ONLY, False),
        ("browser.observe", observe, RiskLevel.R0_READ_ONLY, False),
        ("browser.act", act, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
        ("browser.cua_run", cua_run, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
        ("browser.cua", cua_run, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
        ("browser.replay_export", replay_export, RiskLevel.R0_READ_ONLY, False),
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
        browser_write = name in {
            "browser.click_element",
            "browser.fill_form",
            "browser.submit_form",
            "browser.act",
            "browser.cua",
            "browser.cua_run",
        }
        read_like = risk in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY} and not browser_write
        effects = ["browser_write"] if browser_write else ["read", "observe"]
        if name in {"browser.navigate", "browser.session_start"}:
            effects = ["navigate"]
        if name == "browser.open_url":
            effects = ["open"]
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema={},
                output_schema={},
                risk_level=risk,
                agent_owner="BrowserAgent",
                supports_dry_run=dry_run,
                requires_authorized_path=False,
                execute=fn,
                capabilities=["browser"],
                effects=effects if not read_like else effects,
                resource_kinds=["url", "web_page"],
                fast_path_eligible=False,
                trust_tier="builtin",
                external_network=True,
                sensitive_arg_keys=["selector", "fields", "value"],
            )
        )
