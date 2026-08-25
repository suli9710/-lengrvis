"""Privacy-safe, bounded-cardinality UIAutomation operational metrics.

The public tool interface is the action-attempt seam; the adapter bridge owns
only screenshot-capture failures.  Keep result classification and labels here
so Windows adapters stay independent of observability and callers cannot label
metrics with selectors, text, paths, windows, processes, or error messages.
"""

from __future__ import annotations

import logging
from typing import Literal, TypeVar

from app.observability import metrics
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)

TerminalOutcome = Literal["timeout", "aborted", "exception"]
ApprovalDecision = Literal["required", "denied"]
ApprovalStage = Literal["route_review", "route_claim", "tool_guard", "target_gate"]

_T = TypeVar("_T")

_ACTIONS = frozenset(
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
_APPROVAL_ACTIONS = frozenset({"click", "type_text", "click_at", "drag", "key_press", "hotkey"})
_OUTCOMES = frozenset(
    {
        "success",
        "not_found",
        "denied",
        "approval_required",
        "unavailable",
        "timeout",
        "aborted",
        "truncated",
        "error",
        "invalid_result",
    }
)
_SCREENSHOT_FAILURE_REASONS = frozenset({"unavailable", "timeout", "aborted", "error", "invalid_result"})
MAX_UI_AUTOMATION_METRIC_SERIES = (
    (len(_ACTIONS) + 1) * len(_OUTCOMES) + 2 * len(_SCREENSHOT_FAILURE_REASONS) + (len(_APPROVAL_ACTIONS) + 1) * 2 * 4
)


def record_action_result(
    action: str,
    result: _T,
    *,
    terminal: TerminalOutcome | None = None,
) -> _T:
    """Record one public tool-attempt outcome and return ``result`` unchanged."""

    try:
        normalized_action = _normalize_action(action)
        if terminal is not None:
            outcome = _terminal_outcome(terminal)
        elif normalized_action == "screenshot":
            outcome = _classify_screenshot_result(result)
        else:
            outcome = _classify_result(result)
        _increment(
            "ui_automation_action_outcomes_total",
            labels={"action": normalized_action, "outcome": outcome},
        )

        if outcome in {"denied", "approval_required"}:
            record_approval_gate(
                normalized_action,
                decision="denied" if outcome == "denied" else "required",
                stage=_approval_stage(result),
            )
    except Exception:  # noqa: BLE001, S110 - broad-exception-boundary: metrics cannot alter UI behavior.
        pass
    return result


def record_screenshot_capture_result(
    operation: str,
    result: _T,
    *,
    terminal: TerminalOutcome | None = None,
) -> _T:
    """Record only failed screenshot captures, separate from composite actions."""

    try:
        source = _screenshot_source(operation)
        if source is None:
            return result
        outcome = _terminal_outcome(terminal) if terminal is not None else _classify_screenshot_result(result)
        if outcome != "success":
            reason = outcome if outcome in _SCREENSHOT_FAILURE_REASONS else "error"
            _increment(
                "ui_automation_screenshot_capture_failures_total",
                labels={"source": source, "reason": reason},
            )
    except Exception:  # noqa: BLE001, S110 - broad-exception-boundary: metrics cannot alter UI behavior.
        pass
    return result


def record_approval_gate(
    action: str,
    *,
    decision: ApprovalDecision,
    stage: ApprovalStage,
) -> None:
    """Record a fixed-shape approval gate decision without caller-provided detail."""

    try:
        normalized_action = _normalize_approval_action(action)
        normalized_decision: ApprovalDecision = "denied" if decision == "denied" else "required"
        normalized_stage: ApprovalStage = (
            stage
            if stage
            in {
                "route_review",
                "route_claim",
                "tool_guard",
                "target_gate",
            }
            else "tool_guard"
        )
        _increment(
            "ui_automation_approval_gate_outcomes_total",
            labels={
                "action": normalized_action,
                "decision": normalized_decision,
                "stage": normalized_stage,
            },
        )
    except Exception:  # noqa: BLE001, S110 - broad-exception-boundary: metrics cannot alter UI behavior.
        pass


def _classify_result(result: object) -> str:
    if result is None:
        return "invalid_result"
    if not isinstance(result, dict):
        return "invalid_result"
    if result.get("denied") is True:
        return "denied"
    if result.get("approval_required") is True:
        return "approval_required"
    if result.get("available") is False:
        return "unavailable"
    if result.get("error_code") == "ui_automation_timeout":
        return "timeout"
    if result.get("search_truncated") is True:
        return "truncated"
    if result.get("not_found") is True:
        return "not_found"
    if result.get("match_count") == 0:
        return "not_found"
    if result.get("ok") is False and not result.get("error"):
        if ("element" in result and result.get("element") is None) or (
            "value" in result and result.get("value") is None
        ):
            return "not_found"
    if result.get("ok") is True:
        return "success"
    app_context = result.get("app_context")
    if isinstance(app_context, dict) and app_context.get("available") is False:
        return "unavailable"
    if result.get("ok") is False or "error" in result:
        return "error"
    return "invalid_result"


def _approval_stage(result: object) -> ApprovalStage:
    if isinstance(result, dict) and result.get("_approval_gate_stage") == "tool_guard":
        return "tool_guard"
    return "target_gate"


def _classify_screenshot_result(result: object) -> str:
    outcome = _classify_result(result)
    if outcome != "success":
        return outcome
    if not isinstance(result, dict):
        return "invalid_result"
    image = str(result.get("image") or "")
    try:
        width = int(result.get("width") or 0)
        height = int(result.get("height") or 0)
    except (TypeError, ValueError):
        return "invalid_result"
    if not image.startswith("data:image/") or "," not in image or width <= 0 or height <= 0:
        return "invalid_result"
    return "success"


def _terminal_outcome(terminal: TerminalOutcome) -> str:
    if terminal == "timeout":
        return "timeout"
    if terminal == "aborted":
        return "aborted"
    return "error"


def _normalize_action(operation: str) -> str:
    value = str(operation or "").strip().casefold()
    if value.startswith("ui_automation."):
        value = value.removeprefix("ui_automation.")
    return value if value in _ACTIONS else "other"


def _normalize_approval_action(action: str) -> str:
    normalized = _normalize_action(action)
    return normalized if normalized in _APPROVAL_ACTIONS else "other"


def _screenshot_source(operation: str) -> str | None:
    value = str(operation or "").strip().casefold()
    if value in {"screenshot", "ui_automation.screenshot"}:
        return "direct"
    if value in {"locate_on_screen.screenshot", "ui_automation.locate_on_screen.screenshot"}:
        return "vision_fallback"
    return None


def _increment(name: str, *, labels: dict[str, str]) -> None:
    try:
        metrics.increment_counter(name, labels=labels)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics must never break UI actions.
        log_best_effort_failure(logger, "record UIAutomation metric", exc, metric=name)
