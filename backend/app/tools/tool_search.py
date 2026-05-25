from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def search_tools(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    registry = context.get("registry")
    query = str(args.get("query") or args.get("q") or "").strip()
    max_results = int(args.get("max_results") or 5)
    if registry is None:
        from app.tools.registry import registry as default_registry

        registry = default_registry
    matches = registry.search(query, max_results=max_results)
    return {
        "ok": True,
        "query": query,
        "matches": [
            {
                "name": tool.name,
                "description": tool.description,
                "agent_owner": tool.agent_owner,
                "risk_level": tool.risk_level.value,
                "search_hint": tool.search_hint,
                "defer_loading": tool.defer_loading,
            }
            for tool in matches
        ],
        "total": len(matches),
    }


def register(registry) -> None:
    registry.register(
        ToolDefinition(
            name="tool.search",
            description="Search available deferred tools by name, owner, or capability hint.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "matches": {"type": "array"},
                    "total": {"type": "integer"},
                },
            },
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="SearchAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=search_tools,
            search_hint="discover tools capabilities deferred skills mcp",
            read_only=True,
            max_result_size=10000,
        )
    )
