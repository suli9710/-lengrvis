from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from app.agents.safety_review_agent import SafetyReviewAgent
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task, TaskStatus, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.direct_tool_execution import execute_direct_tool_journaled, finalize_unjournaled_direct_result
from app.orchestration.state_machine import safe_transition
from app.orchestration.step_phase import set_step_status
from app.orchestration.task_phase import TaskPhase
from app.perception.ui_automation_observability import record_approval_gate
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    redacted_preview,
    settings_fingerprint,
)
from app.policy.effective_risk_binding import (
    approval_risk_binding,
    build_effective_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
    risk_revalidation_context,
)
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionStore
from app.policy.redaction import redact_value
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.tools import ui_automation_tools
from app.tools.registry import register_all_tools
from app.tools.registry import registry as tool_registry

router = APIRouter()

_GUI_ACTOR = "UIAutomation"
_ACTION_TO_TOOL = {
    "focus": "ui_automation.focus",
    "focus_window": "ui_automation.focus_window",
    "focus-window": "ui_automation.focus_window",
    "click": "ui_automation.click",
    "type_text": "ui_automation.type_text",
    "type-text": "ui_automation.type_text",
    "click_at": "ui_automation.click_at",
    "click-at": "ui_automation.click_at",
    "drag": "ui_automation.drag",
    "key_press": "ui_automation.key_press",
    "key-press": "ui_automation.key_press",
    "hotkey": "ui_automation.hotkey",
}


def _context() -> dict[str, Any]:
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def _tool_definition(tool_name: str):
    if not tool_registry.list():
        register_all_tools()
    return tool_registry.get(tool_name)


def _execute_reviewed_ui_adapter(
    tool_name: str,
    payload: dict[str, Any],
    executor: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    context = _context()
    review = _review_tool_call(tool_name, payload, context)
    if review.verdict == SafetyVerdict.DENY:
        return _blocked_response(review)
    if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "error": "Current effective risk requires a fresh approval preview.",
            "review": review.model_dump(mode="json"),
        }
    return finalize_unjournaled_direct_result(
        _tool_definition(tool_name),
        executor(payload, context),
        context,
        risk_level=review.risk_level,
        task_id="direct_ui_automation_api",
    )


@router.get("/ui-automation/active-window")
def active_window():
    return _execute_reviewed_ui_adapter(
        "ui_automation.active_window",
        {},
        ui_automation_tools.active_window,
    )


@router.post("/ui-automation/observe")
def observe(payload: dict | None = None):
    return _execute_reviewed_ui_adapter("ui_automation.observe", payload or {}, ui_automation_tools.observe)


@router.post("/ui-automation/find-element")
def find_element(payload: dict | None = None):
    return _execute_reviewed_ui_adapter(
        "ui_automation.find_element",
        payload or {},
        ui_automation_tools.find_element,
    )


@router.post("/ui-automation/wait-for-element")
def wait_for_element(payload: dict | None = None):
    return _execute_reviewed_ui_adapter(
        "ui_automation.wait_for_element",
        payload or {},
        ui_automation_tools.wait_for_element,
    )


@router.get("/ui-automation/windows")
def list_windows():
    return _execute_reviewed_ui_adapter(
        "ui_automation.list_windows",
        {},
        ui_automation_tools.list_windows,
    )


@router.post("/ui-automation/screenshot")
def screenshot(payload: dict | None = None):
    return _execute_reviewed_ui_adapter(
        "ui_automation.screenshot",
        payload or {},
        ui_automation_tools.screenshot,
    )


@router.post("/ui-automation/property")
def get_property(payload: dict | None = None):
    return _execute_reviewed_ui_adapter(
        "ui_automation.get_property",
        payload or {},
        ui_automation_tools.get_property,
    )


@router.post("/ui-automation/children")
def get_children(payload: dict | None = None):
    return _execute_reviewed_ui_adapter(
        "ui_automation.get_children",
        payload or {},
        ui_automation_tools.get_children,
    )


@router.post("/ui-automation/action")
def action(payload: dict | None = None):
    payload = dict(payload or {})
    tool_name = _resolve_action_tool(payload)
    if not tool_name:
        return {
            "ok": False,
            "status": "denied",
            "error": "Unknown GUI automation action. Supported actions: "
            + ", ".join(sorted(key for key in _ACTION_TO_TOOL if not key.startswith("ui_automation."))),
        }
    context = _context()
    tool = _tool_definition(tool_name)
    review = _review_tool_call(tool_name, payload, context)
    if review.verdict == SafetyVerdict.DENY:
        record_approval_gate(tool_name, decision="denied", stage="route_review")
        return _blocked_response(review)
    if tool.risk_level in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY}:
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            record_approval_gate(tool_name, decision="required", stage="route_review")
            return {
                "ok": False,
                "status": "requires_approval",
                "requires_approval": True,
                "paused": True,
                "error": "Current effective risk requires a fresh approval preview.",
                "review": review.model_dump(mode="json"),
            }
        return finalize_unjournaled_direct_result(
            tool,
            tool.execute(payload, context),
            context,
            risk_level=review.risk_level,
            task_id="direct_ui_automation_api",
        )
    if ui_automation_tools.is_dry_run(payload):
        preview = finalize_unjournaled_direct_result(
            tool,
            tool.execute({**payload, "dry_run": True}, context),
            context,
            risk_level=review.risk_level,
            task_id="direct_ui_automation_api",
        )
        if preview.get("withheld") is True:
            return preview
        if not preview.get("ok") or preview.get("dry_run") is not True:
            return {
                "ok": False,
                "status": "preview_failed",
                "error": preview.get("error") or "GUI automation dry-run preview failed.",
                "preview": redacted_preview(binding_preview(preview)),
            }
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            record_approval_gate(tool_name, decision="required", stage="route_review")
            approval = _create_action_approval(tool_name, payload, preview, review, context)
            return {
                "ok": False,
                "status": "requires_approval",
                "requires_approval": True,
                "paused": True,
                "task_id": approval.task_id,
                "approval_id": approval.id,
                "review": review.model_dump(mode="json"),
                "preview": redacted_preview(binding_preview(preview)),
            }
        return preview

    approval_error = _claim_valid_gui_approval(tool_name, payload, context)
    if approval_error is not None:
        decision = "required" if approval_error.get("requires_approval") is True else "denied"
        record_approval_gate(tool_name, decision=decision, stage="route_claim")
        return approval_error
    mark_execution_approved(context)
    return execute_direct_tool_journaled(
        tool,
        payload,
        context,
        approval_id=str(payload.get("approval_id") or ""),
    )


def _resolve_action_tool(payload: dict[str, Any]) -> str:
    raw = str(payload.get("action") or payload.get("kind") or payload.get("tool") or "").strip().casefold()
    tool_name = _ACTION_TO_TOOL.get(raw)
    if not tool_name and raw.startswith("ui_automation."):
        tool_name = raw
    if not tool_name or tool_name not in set(_ACTION_TO_TOOL.values()):
        return ""
    return tool_name


def _review_tool_call(tool_name: str, payload: dict[str, Any], context: dict[str, Any]):
    tool = _tool_definition(tool_name)
    return SafetyReviewAgent(settings=context.get("settings")).review_tool_call(
        "direct_ui_automation_api",
        None,
        tool_name,
        payload,
        tool.risk_level,
        context=context,
        tool_definition=tool,
    )


def _blocked_response(review) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "denied",
        "error": "; ".join(review.reasons) or review.safe_alternative or "GUI automation action denied.",
        "review": review.model_dump(mode="json"),
    }


def _create_action_approval(
    tool_name: str,
    payload: dict[str, Any],
    preview: dict[str, Any],
    review,
    context: dict[str, Any],
) -> Approval:
    settings = context["settings"]
    tool = _tool_definition(tool_name)
    task = Task(
        user_goal=f"GUI automation action: {tool_name}",
        status=TaskStatus.REVIEWING_TOOL_CALL,
        mode=settings.mode,
    )
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="ComputerAgent",
        tool_name=tool_name,
        description=f"GUI automation action {tool_name}",
        args=_approval_args(payload),
        expected_observation=f"{tool_name} completed.",
        risk_level=tool.risk_level,
        requires_approval=True,
    )
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[step],
        global_risk_level=tool.risk_level,
        requires_user_approval=True,
    )
    db.upsert_model("plans", plan)
    safe_preview = binding_preview(preview)
    risk_binding = build_effective_risk_binding(review.declared_risk_level or tool.risk_level, [review])
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message=review.user_confirmation_message or f"Approve GUI automation action {tool_name}?",
        diff_preview=safe_preview,
        tool_name=tool_name,
        risk_level=risk_binding["effective_risk_level"],
        args_binding_hmac=args_binding_hmac(tool_name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(safe_preview),
        settings_fingerprint=settings_fingerprint(settings, allowed_directories=settings.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version=getattr(tool, "tool_version", "1"),
        engineering_boundary={"risk_provenance": risk_binding},
    )
    db.upsert_model("approvals", approval)
    publish_approval_created(approval)
    set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor=_GUI_ACTOR)
    task.status = TaskPhase.EXECUTION
    task.phase = TaskPhase.EXECUTION
    safe_transition(task, TaskStatus.WAITING_USER_APPROVAL, actor=_GUI_ACTOR)
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    record(
        "ui_automation.approval_requested",
        _GUI_ACTOR,
        {"tool_name": tool_name, "approval_id": approval.id},
        task_id=task.id,
    )
    return approval


def _claim_valid_gui_approval(
    tool_name: str, payload: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any] | None:
    if payload.get("approved") is not True:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "error": f"{tool_name} live execution requires approved=true and a valid approved approval_id.",
        }
    approval_id = str(payload.get("approval_id") or "").strip()
    if not approval_id:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "error": f"{tool_name} live execution requires a valid approved approval_id.",
        }
    data = db.fetch_one("approvals", approval_id)
    if not data:
        return {"ok": False, "status": "denied", "error": "Approval id was not found in the approval database."}
    approval = Approval.model_validate(data)
    binding_error = _approval_binding_error(approval, tool_name, payload, context, allow_consumed=False)
    if binding_error:
        db.expire_approval_if_unconsumed(approval.id, now_iso(), binding_error)
        return {"ok": False, "status": "denied", "error": binding_error}
    resource_error = _approval_resource_state_error(approval, tool_name, payload, context)
    if resource_error:
        db.expire_approval_if_unconsumed(approval.id, now_iso(), resource_error)
        return {"ok": False, "status": "denied", "error": resource_error}
    claimed = db.claim_approval_for_execution(approval.id, now_iso())
    if not claimed:
        return {
            "ok": False,
            "status": "denied",
            "error": "Approval has already been consumed or is no longer approved.",
        }
    claimed_approval = Approval.model_validate(claimed)
    binding_error = _approval_binding_error(claimed_approval, tool_name, payload, context, allow_consumed=True)
    if binding_error:
        return {"ok": False, "status": "denied", "error": binding_error}
    expected_state = (claimed_approval.diff_preview or {}).get("_resource_state")
    context["_expected_resource_state"] = expected_state if isinstance(expected_state, list) else []
    context["effective_risk_binding"] = dict(approval_risk_binding(claimed_approval) or {})
    return None


def _approval_resource_state_error(
    approval: Approval,
    tool_name: str,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> str:
    if tool_name not in {"ui_automation.click", "ui_automation.type_text"}:
        return ""
    approved_state = (approval.diff_preview or {}).get("_resource_state")
    if not approved_state:
        return "GUI automation approval is missing the reviewed target state."
    tool = _tool_definition(tool_name)
    try:
        current_preview = tool.execute({**payload, "dry_run": True}, context)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: fail closed on tool-adapter failures.
        return f"Could not refresh the approved GUI target state: {redact_value(str(exc))}"
    current_state = binding_preview(current_preview).get("_resource_state")
    if current_state != approved_state:
        return "Approved GUI target state no longer matches the current UI."
    return ""


def _approval_binding_error(
    approval: Approval,
    tool_name: str,
    payload: dict[str, Any],
    context: dict[str, Any],
    *,
    allow_consumed: bool,
) -> str:
    if approval.approval_type != "tool_call":
        return "GUI automation approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"GUI automation approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "GUI automation approval has already been consumed."
    tool = _tool_definition(tool_name)
    missing = [
        key
        for key, value in {
            "tool_name": approval.tool_name,
            "args_binding_hmac": approval.args_binding_hmac,
            "preview_hmac": approval.preview_hmac,
            "settings_fingerprint": approval.settings_fingerprint,
            "permission_policy_version": approval.permission_policy_version,
            "tool_version": approval.tool_version,
        }.items()
        if not value
    ]
    if missing:
        return f"GUI automation approval lacks binding metadata: {', '.join(missing)}."
    if approval.tool_name != tool_name:
        return "GUI automation approval tool name does not match this route."
    current_context = risk_revalidation_context(context, task_id=approval.task_id)
    current_review = _review_tool_call(tool_name, _approval_args(payload), current_context)
    current_declared = current_review.declared_risk_level or tool.risk_level
    risk_binding = approval_risk_binding(approval)
    risk_error = effective_risk_binding_error(
        risk_binding,
        current_declared_risk=current_declared,
        approval_risk_level=approval.risk_level,
    )
    if risk_error:
        return risk_error
    refreshed_error = refreshed_effective_risk_error(risk_binding, current_review)
    if refreshed_error:
        return refreshed_error
    if approval.tool_version != getattr(tool, "tool_version", "1"):
        return "GUI automation approval tool version does not match this tool."
    expected_args = args_binding_hmac(
        tool_name, _approval_args(payload), task_id=approval.task_id, step_id=approval.step_id
    )
    if not hmac.compare_digest(str(approval.args_binding_hmac or ""), str(expected_args or "")):
        return "GUI automation approval arguments do not match this request."
    expected_preview = preview_hmac(approval.diff_preview)
    if not hmac.compare_digest(str(approval.preview_hmac or ""), str(expected_preview or "")):
        return "GUI automation approval preview was modified after review."
    expected_settings = settings_fingerprint(
        context.get("settings"),
        allowed_directories=list(context.get("allowed_directories") or []),
    )
    if not hmac.compare_digest(str(approval.settings_fingerprint or ""), str(expected_settings or "")):
        return "GUI automation runtime settings changed after approval preview."
    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not hmac.compare_digest(str(approval.permission_policy_version or ""), str(expected_policy or "")):
        return "GUI automation permission policy changed after approval preview."
    return ""


def _approval_args(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"approved", "approval_id", "dry_run"}}
