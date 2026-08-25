from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import routes_ui_automation
from app.core import db
from app.observability import metrics
from app.perception import ui_automation_observability as observed
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import ui_automation_tools
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import ToolAbortedError


@pytest.fixture(autouse=True)
def _reset_metrics_registry():
    metrics.reset()
    yield
    metrics.reset()


def _counter_entries(name: str) -> list[dict[str, object]]:
    return [entry for entry in metrics.snapshot()["counters"] if entry["name"] == name]


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        ({"ok": True}, "success"),
        (None, "invalid_result"),
        ({"ok": False, "denied": True, "reasons": ["private"]}, "denied"),
        ({"ok": False, "approval_required": True}, "approval_required"),
        ({"ok": False, "available": False, "error": "private"}, "unavailable"),
        ({"ok": False, "error": "private"}, "error"),
        ({"unexpected": "private"}, "invalid_result"),
        ([{"name": "private"}], "invalid_result"),
        (SimpleNamespace(available=False, error="private"), "invalid_result"),
    ],
)
def test_action_results_use_a_closed_outcome_vocabulary(result: object, outcome: str) -> None:
    returned = observed.record_action_result("observe", result)

    assert returned is result
    assert _counter_entries("ui_automation_action_outcomes_total") == [
        {
            "name": "ui_automation_action_outcomes_total",
            "labels": {"action": "observe", "outcome": outcome},
            "value": 1.0,
        }
    ]


@pytest.mark.parametrize(
    ("terminal", "outcome"),
    [("timeout", "timeout"), ("aborted", "aborted"), ("exception", "error")],
)
def test_terminal_outcome_overrides_result_shape(terminal: str, outcome: str) -> None:
    observed.record_action_result("click", {"ok": True}, terminal=terminal)  # type: ignore[arg-type]

    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "click",
        "outcome": outcome,
    }


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "match_count": 0, "error": "UI element not found"},
        {"ok": False, "element": None},
        {"ok": False, "value": None},
        {"ok": False, "method": "vision", "not_found": True, "error": "private target"},
    ],
)
def test_structured_no_match_results_are_consistently_not_found(result: dict[str, object]) -> None:
    observed.record_action_result("find_element", result)

    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "find_element",
        "outcome": "not_found",
    }


def test_truncated_search_is_not_misreported_as_not_found() -> None:
    observed.record_action_result(
        "find_element",
        {"ok": False, "search_truncated": True, "match_count": 0, "error": "search limit reached"},
    )

    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "find_element",
        "outcome": "truncated",
    }


def test_action_vocabulary_and_metric_series_upper_bound_are_hard_contracts() -> None:
    assert observed._ACTIONS == frozenset(
        {
            "active_window",
            "observe",
            "find_element",
            "wait_for_element",
            "click",
            "click_preview",
            "type_text",
            "type_text_preview",
            "focus",
            "list_windows",
            "focus_window",
            "click_at",
            "click_at_preview",
            "drag",
            "drag_preview",
            "key_press",
            "key_press_preview",
            "hotkey",
            "hotkey_preview",
            "screenshot",
            "locate_on_screen",
            "get_property",
            "get_children",
        }
    )
    assert observed.MAX_UI_AUTOMATION_METRIC_SERIES == 306


def test_screenshot_failures_distinguish_direct_and_vision_sources() -> None:
    observed.record_screenshot_capture_result(
        "screenshot",
        {
            "ok": True,
            "image": "data:image/jpeg;base64,eA==",
            "width": 1,
            "height": 1,
            "app_context": {"available": False},
        },
    )
    observed.record_screenshot_capture_result("screenshot", {"ok": False, "error": "private path"})
    observed.record_screenshot_capture_result(
        "locate_on_screen.screenshot",
        {"ok": False, "error": "private window"},
        terminal="timeout",
    )

    assert _counter_entries("ui_automation_screenshot_capture_failures_total") == [
        {
            "name": "ui_automation_screenshot_capture_failures_total",
            "labels": {"reason": "error", "source": "direct"},
            "value": 1.0,
        },
        {
            "name": "ui_automation_screenshot_capture_failures_total",
            "labels": {"reason": "timeout", "source": "vision_fallback"},
            "value": 1.0,
        },
    ]


def test_malformed_successful_screenshot_is_an_invalid_capture() -> None:
    payload = {"ok": True, "image": "", "width": 0, "height": 0}

    assert observed.record_screenshot_capture_result("screenshot", payload) is payload
    observed.record_action_result("screenshot", payload)

    assert _counter_entries("ui_automation_screenshot_capture_failures_total")[0]["labels"] == {
        "reason": "invalid_result",
        "source": "direct",
    }
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "screenshot",
        "outcome": "invalid_result",
    }


def test_target_denial_fans_out_to_approval_gate_metric() -> None:
    payload = {
        "ok": False,
        "denied": True,
        "selector": {"automation_id": "secret-control"},
        "error": "C:\\private\\window.txt",
    }

    observed.record_action_result("ui_automation.click", payload)

    assert _counter_entries("ui_automation_approval_gate_outcomes_total") == [
        {
            "name": "ui_automation_approval_gate_outcomes_total",
            "labels": {"action": "click", "decision": "denied", "stage": "target_gate"},
            "value": 1.0,
        }
    ]
    rendered = metrics.render_prometheus()
    assert "secret-control" not in rendered
    assert "private" not in rendered
    assert set(_counter_entries("ui_automation_action_outcomes_total")[0]["labels"]) == {"action", "outcome"}


def test_unknown_action_and_approval_labels_collapse_to_fixed_other_value() -> None:
    observed.record_action_result("user-controlled-secret-operation", {"ok": True})
    observed.record_approval_gate(
        "ui_automation.user-controlled-secret-operation",
        decision="required",
        stage="tool_guard",
    )

    rendered = metrics.render_prometheus()
    assert 'action="other"' in rendered
    assert "user-controlled-secret-operation" not in rendered


def test_metrics_failure_never_changes_operation_result(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {"ok": True, "value": 42}

    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)

    assert observed.record_action_result("observe", result) is result


def test_result_classification_failure_never_changes_operation_result(monkeypatch: pytest.MonkeyPatch) -> None:
    result = {"ok": True, "value": 42}

    def fail_classification(value: object) -> str:  # noqa: ARG001
        raise RuntimeError("unexpected result adapter")

    monkeypatch.setattr(observed, "_classify_result", fail_classification)

    assert observed.record_action_result("observe", result) is result
    assert _counter_entries("ui_automation_action_outcomes_total") == []


def test_counter_and_recovery_logger_failure_cannot_cover_tool_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    def fail_recovery_log(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("logger unavailable")

    @ui_automation_tools._observed_tool_action("hotkey")
    def aborting_action(args: dict[str, object], context: dict[str, object]) -> dict[str, object]:  # noqa: ARG001
        raise ToolAbortedError("stop")

    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)
    monkeypatch.setattr(observed, "log_best_effort_failure", fail_recovery_log)

    with pytest.raises(ToolAbortedError):
        aborting_action({"dry_run": False}, {})


def test_tool_bridge_records_only_screenshot_capture_failures() -> None:
    async def succeed() -> dict[str, object]:
        return {"ok": True}

    async def fail() -> dict[str, object]:
        raise OSError("private adapter detail")

    async def wait() -> dict[str, object]:
        await asyncio.sleep(1)
        return {"ok": True}

    assert ui_automation_tools._run_ui_automation(succeed(), "focus")["ok"] is True
    assert ui_automation_tools._run_ui_automation(fail(), "click")["ok"] is False
    screenshot_result = ui_automation_tools._run_ui_automation(wait(), "screenshot", timeout_seconds=0.01)
    assert screenshot_result["ok"] is False
    assert screenshot_result["error_code"] == "ui_automation_timeout"

    abort = threading.Event()
    abort.set()
    with pytest.raises(ToolAbortedError):
        ui_automation_tools._run_ui_automation(
            wait(),
            "hotkey",
            abort_context={"_tool_abort_event": abort},
        )

    assert _counter_entries("ui_automation_action_outcomes_total") == []
    assert _counter_entries("ui_automation_screenshot_capture_failures_total")[0]["labels"] == {
        "reason": "timeout",
        "source": "direct",
    }


def test_public_action_wrapper_records_early_failures_and_composite_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda **kwargs: SimpleNamespace(),  # noqa: ARG005
    )

    assert ui_automation_tools.key_press({"key": ""}, {})["ok"] is False
    assert ui_automation_tools.get_property({"prop": ""}, {})["ok"] is False
    assert ui_automation_tools.locate_on_screen({}, {})["ok"] is False

    outcomes = {
        (entry["labels"]["action"], entry["labels"]["outcome"]): entry["value"]
        for entry in _counter_entries("ui_automation_action_outcomes_total")
    }
    assert outcomes == {
        ("get_property", "error"): 1.0,
        ("key_press_preview", "error"): 1.0,
        ("locate_on_screen", "error"): 1.0,
    }


def test_public_action_wrapper_records_unavailable_active_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.perception.ui_automation import UnavailableUIAutomationTarget

    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda **kwargs: UnavailableUIAutomationTarget("provider missing"),  # noqa: ARG005
    )

    result = ui_automation_tools.active_window({}, {})

    assert result["ok"] is False
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "active_window",
        "outcome": "unavailable",
    }


@pytest.mark.parametrize(
    ("inspection", "expected_outcome"),
    [
        ({"ok": False, "available": False, "match_count": 0, "error": "provider missing"}, "unavailable"),
        (
            {"ok": False, "search_truncated": True, "match_count": 0, "error": "search limit reached"},
            "truncated",
        ),
        (TimeoutError(), "timeout"),
        (OSError("adapter failed"), "error"),
    ],
)
def test_semantic_preview_preserves_structured_failure_classification(
    monkeypatch: pytest.MonkeyPatch,
    inspection: object,
    expected_outcome: str,
) -> None:
    class PreviewTarget:
        async def inspect_selector(self, selector, *, max_candidates):  # noqa: ANN001, ARG002
            if isinstance(inspection, BaseException):
                raise inspection
            return inspection

    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda **kwargs: PreviewTarget(),  # noqa: ARG005
    )

    result = ui_automation_tools.click({"name": "Save"}, {})

    assert result["ok"] is False
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "click_preview",
        "outcome": expected_outcome,
    }


@pytest.mark.parametrize(
    ("tool", "args", "action"),
    [
        (ui_automation_tools.wait_for_element, {"name": "Save"}, "wait_for_element"),
        (ui_automation_tools.list_windows, {}, "list_windows"),
        (ui_automation_tools.get_property, {"name": "Save", "prop": "Name"}, "get_property"),
        (ui_automation_tools.get_children, {"name": "Save"}, "get_children"),
        (ui_automation_tools.locate_on_screen, {"target": "Save button"}, "locate_on_screen"),
    ],
)
def test_public_actions_preserve_unavailable_provider_state(
    monkeypatch: pytest.MonkeyPatch,
    tool,
    args: dict[str, object],
    action: str,
) -> None:
    from app.perception.ui_automation import UnavailableUIAutomationTarget

    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda **kwargs: UnavailableUIAutomationTarget("provider missing"),  # noqa: ARG005
    )

    result = tool(args, {})

    assert result["ok"] is False
    assert result["available"] is False
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": action,
        "outcome": "unavailable",
    }
    if action == "locate_on_screen":
        assert _counter_entries("ui_automation_screenshot_capture_failures_total")[0]["labels"] == {
            "reason": "unavailable",
            "source": "vision_fallback",
        }


@pytest.mark.parametrize(
    ("dry_run", "expected_action"),
    [
        (True, "click_preview"),
        (1, "click_preview"),
        ("false", "click_preview"),
        (None, "click_preview"),
        (False, "click"),
    ],
)
def test_dry_run_branch_and_metric_bucket_share_fail_safe_parser(dry_run: object, expected_action: str) -> None:
    @ui_automation_tools._observed_tool_action("click")
    def fake_click(args: dict[str, object], context: dict[str, object]) -> dict[str, object]:  # noqa: ARG001
        return {"ok": True, "dry_run": ui_automation_tools.is_dry_run(args)}

    result = fake_click({"dry_run": dry_run}, {})

    assert result["dry_run"] is (expected_action == "click_preview")
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": expected_action,
        "outcome": "success",
    }


@pytest.mark.parametrize(
    ("result", "decision", "outcome"),
    [
        ({"ok": False, "denied": True}, "denied", "denied"),
        ({"ok": False, "approval_required": True}, "required", "approval_required"),
    ],
)
def test_public_action_wrapper_fans_out_target_gate_once(
    result: dict[str, object],
    decision: str,
    outcome: str,
) -> None:
    @ui_automation_tools._observed_tool_action("click")
    def fake_click(args: dict[str, object], context: dict[str, object]) -> dict[str, object]:  # noqa: ARG001
        return result

    assert fake_click({"dry_run": False}, {}) is result
    assert _counter_entries("ui_automation_action_outcomes_total") == [
        {
            "name": "ui_automation_action_outcomes_total",
            "labels": {"action": "click", "outcome": outcome},
            "value": 1.0,
        }
    ]
    assert _counter_entries("ui_automation_approval_gate_outcomes_total") == [
        {
            "name": "ui_automation_approval_gate_outcomes_total",
            "labels": {"action": "click", "decision": decision, "stage": "target_gate"},
            "value": 1.0,
        }
    ]


def test_missing_tool_approval_records_tool_guard() -> None:
    result = ui_automation_tools.drag({"dry_run": False}, {})

    assert result["ok"] is False
    assert result["approval_required"] is True
    assert _counter_entries("ui_automation_action_outcomes_total")[0]["labels"] == {
        "action": "drag",
        "outcome": "approval_required",
    }
    assert _counter_entries("ui_automation_approval_gate_outcomes_total")[0]["labels"] == {
        "action": "drag",
        "decision": "required",
        "stage": "tool_guard",
    }


def _route_review(verdict: SafetyVerdict):
    return SimpleNamespace(
        verdict=verdict,
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        declared_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        reasons=["fixed policy result"],
        safe_alternative="",
        model_dump=lambda **kwargs: {"verdict": verdict.value},  # noqa: ARG005
    )


def _install_route_tool(monkeypatch: pytest.MonkeyPatch, *, verdict: SafetyVerdict) -> None:
    tool = ToolDefinition(
        name="ui_automation.click",
        description="UI automation observability test action",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="ComputerAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        read_only=False,
        concurrency_safe=False,
        effects=["write"],
        resource_kinds=["desktop_ui"],
        trust_tier="builtin",
        execute=lambda payload, context: {  # noqa: ARG005
            "ok": True,
            "dry_run": True,
            "diff_preview": [{"action": "click"}],
        },
    )
    monkeypatch.setattr(routes_ui_automation, "_resolve_action_tool", lambda payload: "ui_automation.click")
    monkeypatch.setattr(routes_ui_automation, "_context", lambda: {})
    monkeypatch.setattr(routes_ui_automation, "_tool_definition", lambda name: tool)
    monkeypatch.setattr(routes_ui_automation, "_review_tool_call", lambda *args: _route_review(verdict))


def test_route_review_denial_records_approval_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_route_tool(monkeypatch, verdict=SafetyVerdict.DENY)

    result = routes_ui_automation.action({"action": "click"})

    assert result["status"] == "denied"
    assert _counter_entries("ui_automation_approval_gate_outcomes_total")[0]["labels"] == {
        "action": "click",
        "decision": "denied",
        "stage": "route_review",
    }


def test_route_review_and_claim_required_use_distinct_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db(force=True)
    _install_route_tool(monkeypatch, verdict=SafetyVerdict.NEEDS_USER_APPROVAL)
    monkeypatch.setattr(
        routes_ui_automation,
        "_create_action_approval",
        lambda *args: SimpleNamespace(task_id="task", id="approval"),
    )

    preview_result = routes_ui_automation.action({"action": "click"})
    assert preview_result["status"] == "requires_approval"

    monkeypatch.setattr(
        routes_ui_automation,
        "_claim_valid_gui_approval",
        lambda *args: {"ok": False, "status": "requires_approval", "requires_approval": True},
    )
    claim_result = routes_ui_automation.action({"action": "click", "dry_run": False})
    assert claim_result["status"] == "requires_approval"

    entries = _counter_entries("ui_automation_approval_gate_outcomes_total")
    assert all(entry["value"] == 1.0 for entry in entries)
    assert {tuple(sorted(entry["labels"].items())) for entry in entries} == {
        (("action", "click"), ("decision", "required"), ("stage", "route_claim")),
        (("action", "click"), ("decision", "required"), ("stage", "route_review")),
    }


def test_route_claim_denial_records_denied_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_route_tool(monkeypatch, verdict=SafetyVerdict.ALLOW)
    monkeypatch.setattr(
        routes_ui_automation,
        "_claim_valid_gui_approval",
        lambda *args: {"ok": False, "status": "denied", "error": "binding mismatch"},
    )

    result = routes_ui_automation.action({"action": "click", "dry_run": False})

    assert result["status"] == "denied"
    assert _counter_entries("ui_automation_approval_gate_outcomes_total") == [
        {
            "name": "ui_automation_approval_gate_outcomes_total",
            "labels": {"action": "click", "decision": "denied", "stage": "route_claim"},
            "value": 1.0,
        }
    ]
