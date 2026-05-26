from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def _noop(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    return {"ok": True}


def test_tool_definition_public_serialization_exposes_capability_metadata() -> None:
    tool = ToolDefinition(
        name="dev.grep",
        description="Search workspace text",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="ComputerAgent",
        supports_dry_run=False,
        requires_authorized_path=True,
        execute=_noop,
        permission_mode="auto_readonly",
        read_only=True,
        concurrency_safe=True,
        progress_schema={"type": "object", "properties": {"matches": {"type": "integer"}}},
        ui_summary="Search workspace files",
        hooks={"pre_execute": ["audit"], "post_execute": ["summarize"]},
        origin="builtin",
        trust_tier="builtin",
        feature_flag="developer_tools",
        capabilities=["filesystem", "developer_search"],
        effects=["read", "search"],
        resource_kinds=["workspace", "repository"],
        fast_path_eligible=True,
        sensitive_arg_keys=["token"],
        app_target={"surface": "desktop"},
        workflow={"kind": "read_only_search"},
    )

    public = tool.to_public_dict()

    assert public["name"] == "dev.grep"
    assert public["risk_level"] == "R0_READ_ONLY"
    assert public["permission_mode"] == "auto_readonly"
    assert public["read_only"] is True
    assert public["concurrency_safe"] is True
    assert public["progress_schema"]["properties"]["matches"]["type"] == "integer"
    assert public["ui_summary"] == "Search workspace files"
    assert public["hooks"] == {"pre_execute": ["audit"], "post_execute": ["summarize"]}
    assert public["origin"] == "builtin"
    assert public["trust_tier"] == "builtin"
    assert public["feature_flag"] == "developer_tools"
    assert public["capabilities"] == ["filesystem", "developer_search"]
    assert public["effects"] == ["read", "search"]
    assert public["resource_kinds"] == ["workspace", "repository"]
    assert public["fast_path_eligible"] is True
    assert "input_schema" not in public
    assert "output_schema" not in public
    assert "sensitive_arg_keys" not in public
    assert "app_target" not in public
    assert "workflow" not in public

    with_schema = tool.to_public_dict(include_schema=True)

    assert with_schema["input_schema"]["required"] == ["query"]
    assert with_schema["output_schema"] == {"type": "object"}
    assert with_schema["supports_dry_run"] is False
    assert with_schema["requires_authorized_path"] is True
    assert with_schema["sensitive_arg_keys"] == ["token"]
    assert with_schema["app_target"] == {"surface": "desktop"}
    assert with_schema["workflow"] == {"kind": "read_only_search"}


def test_tool_definition_progress_event_uses_public_schema_and_summary() -> None:
    tool = ToolDefinition(
        name="dev.shell_readonly",
        description="Run a read-only shell command",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="ComputerAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_noop,
        progress_schema={"type": "object", "properties": {"line": {"type": "string"}}},
        ui_summary="Read-only shell inspection",
    )

    event = tool.progress_event(
        "running",
        task_id="task_1",
        step_id="step_1",
        tool_call_id="tool_1",
        payload={"line": "git status"},
    )

    assert event == {
        "kind": "tool_progress",
        "status": "running",
        "task_id": "task_1",
        "step_id": "step_1",
        "tool_call_id": "tool_1",
        "tool_name": "dev.shell_readonly",
        "detail": "Read-only shell inspection",
        "schema": {"type": "object", "properties": {"line": {"type": "string"}}},
        "payload": {"line": "git status"},
    }


def test_tool_definition_infers_readonly_and_concurrency_defaults() -> None:
    read_tool = ToolDefinition(
        name="test.read",
        description="read",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_noop,
    )
    keyed_tool = ToolDefinition(
        name="test.keyed",
        description="keyed",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_noop,
        concurrency_key="workspace-index",
    )
    destructive_tool = ToolDefinition(
        name="test.destructive",
        description="destructive",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_noop,
        destructive=True,
    )

    assert read_tool.is_read_only() is True
    assert read_tool.is_concurrency_safe() is True
    assert keyed_tool.is_concurrency_safe() is False
    assert destructive_tool.is_concurrency_safe() is False
