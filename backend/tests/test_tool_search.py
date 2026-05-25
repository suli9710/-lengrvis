from __future__ import annotations

from app.policy.risk import RiskLevel
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolDefinition
from app.tools.tool_search import register as register_tool_search


def _tool(name: str, hint: str = "", *, defer: bool = False) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name.replace(".", " "),
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="SearchAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
        search_hint=hint,
        defer_loading=defer,
    )


def test_registry_search_finds_deferred_tool_by_hint():
    registry = ToolRegistry()
    registry.register(_tool("skill.mail.export", "email archive mailbox", defer=True))
    registry.register(_tool("file.search_by_name", "filename lookup"))

    matches = registry.search("mailbox", max_results=3)

    assert [tool.name for tool in matches] == ["skill.mail.export"]


def test_tool_search_tool_uses_runtime_registry_context():
    registry = ToolRegistry()
    register_tool_search(registry)
    registry.register(_tool("skill.calendar.clean", "calendar schedule cleanup", defer=True))
    tool = registry.get("tool.search")

    result = tool.execute({"query": "schedule"}, {"registry": registry})

    assert result["ok"] is True
    assert result["matches"][0]["name"] == "skill.calendar.clean"


def test_list_for_planning_keeps_tool_search_but_hides_deferred_tools():
    registry = ToolRegistry()
    register_tool_search(registry)
    registry.register(_tool("skill.hidden", "hidden deferred", defer=True))
    registry.register(_tool("file.visible", "visible"))

    names = {tool.name for tool in registry.list_for_planning()}

    assert "tool.search" in names
    assert "file.visible" in names
    assert "skill.hidden" not in names
