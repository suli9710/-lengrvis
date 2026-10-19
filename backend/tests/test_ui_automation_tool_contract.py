from __future__ import annotations

import json

import pytest

from app.tools import ui_automation_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def desktop_tools():
    registry = ToolRegistry()
    ui_automation_tools.register(registry)
    return registry


def test_model_can_construct_scoped_text_entry_from_tool_schema(desktop_tools):
    tool = desktop_tools.get("ui_automation.type_text")
    schema = tool.to_public_dict(include_schema=True)["input_schema"]

    assert {"automation_id", "process_id", "name", "control_type", "text", "dry_run"} <= set(schema["properties"])
    tool.validate_input({"automation_id": "editor", "process_id": 123, "text": "draft"}, {})
    with pytest.raises(ValueError):
        tool.validate_input({"automation_id": "editor"}, {})
    with pytest.raises(ValueError):
        tool.validate_input({"text": "unscoped"}, {})


def test_model_can_bind_a_save_hotkey_to_a_document(desktop_tools):
    tool = desktop_tools.get("ui_automation.hotkey")
    tool.validate_input({"keys": ["ctrl", "s"], "process_id": 123, "control_type": "Document"}, {})
    # Existing explicit foreground hotkeys remain a separately approved action.
    tool.validate_input({"keys": ["ctrl", "s"]}, {})


def test_scoped_hotkey_preview_binds_the_document_identity(monkeypatch):
    inspected = []
    state = {"kind": "ui_automation_element", "target_window": {"identity_hmac": "owned-document"}}

    class Target:
        async def inspect_selector(self, selector, **kwargs):
            inspected.append(selector)
            return {"ok": True, "resource_state": state}

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", lambda **_: Target())
    preview = ui_automation_tools.hotkey({"keys": ["ctrl", "s"], "process_id": 123, "control_type": "Document"}, {})
    assert preview["ok"], preview
    assert inspected[0]["process_id"] == 123
    assert preview["_resource_state"] == [state]
    assert preview["diff_preview"][0]["keys"] == ["ctrl", "s"]


def test_native_text_result_budget_preserves_supported_values_for_bindings(desktop_tools):
    payload = {"ok": True, "property": "text", "value": "\x01" * 65536, "verified": True}
    assert (
        len(json.dumps(payload, ensure_ascii=False)) < desktop_tools.get("ui_automation.get_property").max_result_size
    )


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("click_at", {"y": 22}),
        ("drag", {"start_x": 0, "start_y": 0}),
        ("hotkey", {"keys": []}),
        ("screenshot", {"quality": 500}),
        ("observe", {"max_depth": -1}),
        ("focus_window", {}),
        ("wait_for_element", {"name": "dialog", "timeout_seconds": 1000000}),
        ("get_property", {"name": "editor", "prop": "GetCurrentPattern"}),
    ],
)
def test_invalid_ui_parameters_are_rejected_before_adapter_creation(monkeypatch, action, args):
    def unexpected_adapter(**_):
        raise AssertionError("Invalid arguments must not reach the desktop adapter")

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", unexpected_adapter)
    result = getattr(ui_automation_tools, action)(args, {})

    assert result["ok"] is False
    assert result["error_code"] == "invalid_tool_arguments"
    assert result["next_actions"]


def test_runtime_validation_accepts_existing_aliases_without_mutating_approval_input(desktop_tools):
    args = {"automationId": "editor", "processId": 123, "text": "", "dry_run": True}
    original = dict(args)
    tool = desktop_tools.get("ui_automation.type_text")

    assert tool.validate_input is not None
    tool.validate_input(args, {})
    assert args == original


def test_zero_depth_observation_is_preserved(monkeypatch):
    depths = []

    class Target:
        async def observe(self, selector, *, max_depth, max_elements):
            depths.append(max_depth)
            return {"ok": True, "elements": []}

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", lambda **_: Target())
    assert ui_automation_tools.observe({"max_depth": 0}, {})["ok"] is True
    assert depths == [0]


def test_property_dictionary_is_wrapped_as_value(monkeypatch):
    class Target:
        async def get_property(self, selector, prop):
            return {"x": 10, "y": 20, "width": 30, "height": 40}

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", lambda **_: Target())
    result = ui_automation_tools.get_property({"automation_id": "editor", "prop": "bounding_box"}, {})

    assert result["ok"] is True
    assert result["value"]["x"] == 10


@pytest.mark.parametrize(
    ("observed", "expected", "verified"),
    [
        ("copied text", "copied text", True),
        ("", "", True),
        ("stale text", "copied text", False),
        ("line\r\nbreak", "line\nbreak", False),
        (True, 1, False),
        ({"x": 10, "y": 20}, {"y": 20, "x": 10}, True),
    ],
)
def test_readback_checks_expected_value_without_claiming_a_mismatched_write(monkeypatch, observed, expected, verified):
    class Target:
        async def get_property(self, selector, prop):
            return observed

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", lambda **_: Target())
    result = ui_automation_tools.get_property(
        {"automation_id": "editor", "prop": "text", "expected_value": expected}, {}
    )

    assert result["ok"] is verified
    assert result["verified"] is verified
    assert result["value"] == observed
    assert "expected_value" not in result
    if not verified:
        assert result["error_code"] == "ui_automation_postcondition_failed"
        assert result["next_actions"]


def test_readback_expectation_is_part_of_the_model_contract_and_sensitive_arguments(desktop_tools):
    tool = desktop_tools.get("ui_automation.get_property")
    assert "expected_value" in tool.input_schema["properties"]
    assert "expected_value" in tool.sensitive_arg_keys
    tool.validate_input({"process_id": 123, "prop": "text", "expected_value": ""}, {})


@pytest.mark.parametrize(
    ("observed", "verified"),
    [
        ("中文😀\rsecond line", True),
        ("中文😀\nsecond line", True),
        ("中文😀\r\nsecond line", True),
        ("中文😀\rsecond line ", False),
        ("中文😀\rwrong line", False),
        ("中文😀second line", False),
    ],
)
def test_explicit_newline_comparison_preserves_raw_observation(monkeypatch, observed, verified):
    class Target:
        async def get_property(self, selector, prop):
            return observed

    monkeypatch.setattr(ui_automation_tools, "create_ui_automation_target", lambda **_: Target())
    result = ui_automation_tools.get_property(
        {
            "process_id": 123,
            "prop": "text",
            "expected_value": "中文😀\r\nsecond line",
            "text_comparison": "normalize_newlines",
        },
        {},
    )
    assert result["ok"] is verified
    assert result["verified"] is verified
    assert result["value"] == observed
    assert result["text_comparison"] == "normalize_newlines"


@pytest.mark.parametrize(
    "args",
    [
        {"prop": "name", "expected_value": "name"},
        {"prop": "text", "expected_value": 123},
        {"prop": "text"},
    ],
)
def test_newline_comparison_requires_a_text_postcondition(desktop_tools, args):
    with pytest.raises(ValueError):
        desktop_tools.get("ui_automation.get_property").validate_input(
            {
                "process_id": 123,
                "text_comparison": "normalize_newlines",
                **args,
            },
            {},
        )


def test_planner_and_worker_prompts_expose_desktop_call_contract(desktop_tools):
    from app.agents.base import AgentContext, _act_user_prompt
    from app.core.schemas import PlanStep
    from app.orchestration.handlers.planning_handler import _planner_tool_spec

    tool = desktop_tools.get("ui_automation.type_text")
    spec = _planner_tool_spec(tool)
    assert "process_id?:integer" in spec
    assert "text:string" in spec
    assert "at least one:" in spec
    step = PlanStep(agent_name="ComputerAgent", tool_name=tool.name, description="Enter source text into target editor")
    context = AgentContext(task_id="task", mode="efficiency", allowed_directories=[], registry=desktop_tools)
    prompt = _act_user_prompt(step, None, [tool.name], context)
    assert "Tool input contract" in prompt
    assert '"automation_id"' in prompt
    assert "verify by reading text" in prompt
