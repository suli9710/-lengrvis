from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel
from app.tools.registry import ToolRegistry
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
    assert public["idempotency_scope"] == "local_execution_key"
    assert public["supports_reconciliation"] is False
    assert public["compensation_strength"] == "none"
    assert public["safe_to_retry_errors"] == []
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


def test_registry_hides_tools_missing_model_visible_contract() -> None:
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="test.incomplete_contract",
        description="missing contract metadata",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="TestAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_noop,
        origin="test_extension",
    )

    registry.register(tool)

    assert registry.get(tool.name) is tool
    assert tool.contract_errors()
    assert registry.list_for_planning() == []
    assert registry.search("incomplete contract") == []


def test_tool_definition_rejects_invalid_transaction_contract_values() -> None:
    tool = ToolDefinition(
        name="test.invalid_transaction_contract",
        description="invalid transaction contract",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="TestAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=_noop,
        read_only=False,
        concurrency_safe=False,
        effects=["write"],
        resource_kinds=["test_resource"],
        trust_tier="builtin",
        idempotency_scope="magic",
        compensation_strength="perfect",
        safe_to_retry_errors=[""],
    )

    errors = tool.contract_errors()

    assert "idempotency_scope must be authoritative" in errors
    assert "compensation_strength must be authoritative" in errors
    assert "safe_to_retry_errors must contain non-empty error codes" in errors


def test_high_risk_tool_cannot_disable_idempotency_scope() -> None:
    tool = ToolDefinition(
        name="test.no_high_risk_idempotency",
        description="missing high-risk idempotency",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="TestAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=_noop,
        read_only=False,
        concurrency_safe=False,
        effects=["write"],
        resource_kinds=["test_resource"],
        trust_tier="builtin",
        idempotency_scope="none",
    )

    assert "R2/R3 tools must declare an idempotency scope" in tool.contract_errors()


def test_fast_path_eligible_tools_declare_explicit_object_schema() -> None:
    from app.agents.base import _has_explicit_object_schema
    from app.llm.registry import get_effective_settings
    from app.tools.registry import register_all_tools

    registry = register_all_tools(settings=get_effective_settings())
    offenders = [
        tool.name
        for tool in registry.list()
        if getattr(tool, "fast_path_eligible", False)
        and not _has_explicit_object_schema(getattr(tool, "input_schema", {}) or {})
    ]
    assert offenders == [], (
        f"fast_path_eligible tools without an explicit object input_schema fall back to an extra LLM hop: {offenders}"
    )
