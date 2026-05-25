from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def query(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from app.tools.browser_tools import search_web_via_provider

    return search_web_via_provider(args, context)


def fetch_result(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from app.tools.browser_tools import read_page

    url = str(args.get("url", "")).strip()
    if not url:
        return {"ok": False, "content": "", "error": "Missing url."}
    page = read_page({"url": url, "max_chars": args.get("max_chars", 12000)}, context)
    return {
        "ok": page.get("ok", False),
        "url": page.get("url", url),
        "title": page.get("title", ""),
        "content": page.get("text", ""),
        "links": page.get("links", []),
        "error": page.get("error", ""),
    }


def summarize_results(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    result = query(args, context)
    if not result.get("ok"):
        return {"ok": False, "summary": "", "error": result.get("error", "Search failed.")}
    titles = [str(item.get("title") or item.get("url")) for item in result.get("results", [])[:5]]
    return {"ok": True, "summary": "\n".join(f"- {title}" for title in titles), "results": result.get("results", [])}


def register(registry) -> None:
    defs = [
        ("search.query", query),
        ("search.fetch_result", fetch_result),
        ("search.summarize_results", summarize_results),
    ]
    for name, fn in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
                output_schema={},
                risk_level=RiskLevel.R0_READ_ONLY,
                agent_owner="SearchAgent",
                supports_dry_run=False,
                requires_authorized_path=False,
                execute=fn,
            )
        )
