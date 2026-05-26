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


def test_registry_search_prefers_deferred_matches_over_builtin_matches():
    registry = ToolRegistry()
    registry.register(_tool("app.calendar.open", "calendar schedule cleanup"))
    registry.register(_tool("skill.calendar.clean", "calendar schedule cleanup", defer=True))

    matches = registry.search("calendar schedule", max_results=1)

    assert [tool.name for tool in matches] == ["skill.calendar.clean"]


def test_registry_search_can_exclude_deferred_tools():
    registry = ToolRegistry()
    registry.register(_tool("skill.mail.export", "email archive mailbox", defer=True))
    registry.register(_tool("file.search_by_name", "mailbox filename lookup"))

    matches = registry.search("mailbox", max_results=3, include_deferred=False)

    assert [tool.name for tool in matches] == ["file.search_by_name"]


def test_registry_search_deferred_only_hides_builtin_matches():
    registry = ToolRegistry()
    registry.register(_tool("system.calendar.info", "calendar schedule cleanup"))

    matches = registry.search("calendar", max_results=3, deferred_only=True)

    assert matches == []


def test_tool_search_tool_uses_runtime_registry_context():
    registry = ToolRegistry()
    register_tool_search(registry)
    calendar_tool = _tool("skill.calendar.clean", "calendar schedule cleanup", defer=True)
    calendar_tool.effects = ["read"]
    calendar_tool.resource_kinds = ["calendar"]
    calendar_tool.trust_tier = "skill"
    registry.register(calendar_tool)
    tool = registry.get("tool.search")

    result = tool.execute({"query": "schedule"}, {"registry": registry})

    assert result["ok"] is True
    assert result["matches"][0]["name"] == "skill.calendar.clean"
    assert result["matches"][0]["effects"] == ["read"]
    assert result["matches"][0]["resource_kinds"] == ["calendar"]
    assert result["matches"][0]["trust_tier"] == "skill"
    assert result["matches"][0]["fast_path_eligible"] is False
    assert "input_schema" not in result["matches"][0]
    assert result["selected"] is False


def test_tool_search_prioritizes_deferred_tool_over_builtin_tool():
    registry = ToolRegistry()
    register_tool_search(registry)
    registry.register(_tool("app.calendar.open", "calendar schedule cleanup"))
    registry.register(_tool("skill.calendar.clean", "calendar schedule cleanup", defer=True))
    tool = registry.get("tool.search")

    result = tool.execute({"query": "calendar schedule", "max_results": 1}, {"registry": registry})

    assert result["matches"][0]["name"] == "skill.calendar.clean"


def test_tool_search_select_loads_full_deferred_tool_schema():
    registry = ToolRegistry()
    register_tool_search(registry)
    deferred = _tool("skill.calendar.clean", "calendar schedule cleanup", defer=True)
    deferred.input_schema = {"type": "object", "required": ["calendar_id"]}
    registry.register(deferred)
    tool = registry.get("tool.search")

    result = tool.execute({"query": "select:skill.calendar.clean"}, {"registry": registry})

    assert result["selected"] is True
    assert result["matches"][0]["name"] == "skill.calendar.clean"
    assert result["matches"][0]["input_schema"] == {"type": "object", "required": ["calendar_id"]}
    assert result["matches"][0]["defer_loading"] is True
    assert result["matches"][0]["sensitive_arg_keys"] == []


def test_tool_search_select_does_not_expand_non_deferred_tool_schema():
    registry = ToolRegistry()
    register_tool_search(registry)
    builtin = _tool("system.get_info", "system info")
    builtin.input_schema = {"type": "object", "required": ["secret"]}
    registry.register(builtin)
    tool = registry.get("tool.search")

    result = tool.execute({"query": "select:system.get_info"}, {"registry": registry})

    assert result["selected"] is False
    assert result["matches"] == []


def test_tool_search_regular_query_does_not_return_non_deferred_tool():
    registry = ToolRegistry()
    register_tool_search(registry)
    registry.register(_tool("system.get_info", "system info"))
    tool = registry.get("tool.search")

    result = tool.execute({"query": "system info"}, {"registry": registry})

    assert result["matches"] == []


def test_list_for_planning_keeps_tool_search_but_hides_deferred_tools():
    registry = ToolRegistry()
    register_tool_search(registry)
    registry.register(_tool("skill.hidden", "hidden deferred", defer=True))
    registry.register(_tool("file.visible", "visible"))

    names = {tool.name for tool in registry.list_for_planning()}

    assert "tool.search" in names
    assert "file.visible" in names
    assert "skill.hidden" not in names
