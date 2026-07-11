from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.audit import record
from app.core.schemas import (
    Approval,
    ApprovalStatus,
    Plan,
    StepStatus,
    Task,
    TaskStatus,
    approval_is_expired,
    now_iso,
)
from app.orchestration.state_machine import safe_transition
from app.orchestration.step_phase import set_step_status
from app.policy.redaction import redact_public_text
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    create_native_confirmation_challenge,
    enforce_native_confirmation_challenge_rate_limit,
    require_native_confirmation,
)
from app.services.mobile_pairing_service import approve_approval as approve_mobile_approval
from app.services.mobile_pairing_service import (
    get_approval_detail,
    list_pending_approvals,
    mobile_approval_auth_context,
    raise_if_mobile_claims_disallowed,
    safe_approval_payload,
)
from app.services.mobile_pairing_service import reject_approval as reject_mobile_approval
from app.services.task_service import set_task_status

router = APIRouter()
_DEVELOPER_LENGRVIS_CODE_TOOL = "developer.lengrvis_code"


def _approval_decision_endpoint(approval_id: str, action: str) -> str:
    return f"/api/approvals/{approval_id}/{action}"


class NativeConfirmationChallengeRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    expected_preview_hmac: str = ""


def _approval_native_confirmation(
    approval_id: str,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
) -> dict[str, Any]:
    return require_native_confirmation(
        action="approve",
        endpoint=_approval_decision_endpoint(approval_id, "approve"),
        approval_id=approval_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
        preview_hmac=_approval_preview_hmac(approval_id),
    )


def _rejection_native_confirmation(
    approval_id: str,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
) -> dict[str, Any]:
    return require_native_confirmation(
        action="reject",
        endpoint=_approval_decision_endpoint(approval_id, "reject"),
        approval_id=approval_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
        preview_hmac=_approval_preview_hmac(approval_id),
    )


ApprovalNativeConfirmation = Annotated[dict[str, Any], Depends(_approval_native_confirmation)]
RejectionNativeConfirmation = Annotated[dict[str, Any], Depends(_rejection_native_confirmation)]


@router.get("/approvals/pending")
def pending():
    return list_pending_approvals()


@router.get("/approvals/{approval_id}")
def detail(approval_id: str):
    return get_approval_detail(approval_id)


@router.post("/approvals/{approval_id}/native-confirmation-challenge")
def native_confirmation_challenge(
    approval_id: str,
    payload: NativeConfirmationChallengeRequest,
    request: Request,
) -> dict[str, Any]:
    enforce_native_confirmation_challenge_rate_limit(_client_scope(request))
    db.require_sensitive_integrity_ok()
    approval = _approval_for_confirmation(approval_id)
    expected_preview_hmac = str(payload.expected_preview_hmac or "").strip()
    if expected_preview_hmac and not hmac.compare_digest(expected_preview_hmac, approval.preview_hmac):
        raise HTTPException(status_code=409, detail="Approval preview changed; refresh before confirming.")
    return create_native_confirmation_challenge(
        action=payload.action,
        endpoint=_approval_decision_endpoint(approval.id, payload.action),
        approval_id=approval.id,
        preview_hmac=approval.preview_hmac,
    )


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, native_confirmation: ApprovalNativeConfirmation):
    authorized_at, auth_context = _desktop_native_authorization(native_confirmation)
    approval = approval_for_execution(
        approval_id,
        authorized_at=authorized_at,
        auth_context=auth_context,
    )
    _record_desktop_native_confirmation(approval, "approve", native_confirmation)
    approval = await _execute_approved_step(approval)
    return approval_execution_response(approval)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, native_confirmation: RejectionNativeConfirmation):
    before = db.fetch_one("approvals", approval_id)
    authorized_at, auth_context = _desktop_native_authorization(native_confirmation)
    approval = reject_mobile_approval(
        approval_id,
        authorized_at=authorized_at,
        auth_context=auth_context,
    )
    _record_desktop_native_confirmation(
        Approval.model_validate(before) if before else approval, "reject", native_confirmation
    )
    _deny_rejected_step(approval)
    _reconcile_runs(approval.task_id)
    return safe_approval_payload(approval)


def _client_scope(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return (host or "unknown").strip().lower() or "unknown"


def _approval_for_confirmation(approval_id: str) -> Approval:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = Approval.model_validate(data)
    if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}.")
    if approval.consumed_at:
        raise HTTPException(status_code=409, detail="Approval has already been consumed.")
    if approval_is_expired(approval):
        db.expire_approval_if_unconsumed(approval.id, now_iso(), "Approval authorization expired.")
        raise HTTPException(status_code=409, detail="Approval authorization expired.")
    return approval


def _approval_preview_hmac(approval_id: str) -> str:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        return ""
    return str(data.get("preview_hmac") or "")


def _record_desktop_native_confirmation(
    approval: Approval,
    decision: str,
    native_confirmation: dict[str, Any],
) -> None:
    record(
        "approval.desktop_native_confirmed",
        "DesktopMain",
        {
            "approval_id": approval.id,
            "task_id": approval.task_id,
            "step_id": approval.step_id,
            "decision": decision,
            "desktop_native_confirmed": True,
            "desktop_native_confirmed_at": now_iso(),
            "desktop_native_confirmation_id": native_confirmation.get("confirmation_id"),
            "tool_name": approval.tool_name,
            "risk_level": approval.risk_level,
            "dry_run_summary_present": bool(approval.dry_run_summary),
            "confirmation_evidence": {
                "approval_id": approval.id,
                "task_id": approval.task_id,
                "step_id": approval.step_id,
                "tool_name": approval.tool_name,
                "risk_level": approval.risk_level,
                "dry_run_summary": redact_public_text(approval.dry_run_summary or ""),
            },
        },
        task_id=approval.task_id,
    )


def _desktop_native_authorization(native_confirmation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        confirmed_at_epoch = int(native_confirmation.get("confirmed_at_epoch"))
        authorized_at = datetime.fromtimestamp(confirmed_at_epoch, UTC).isoformat()
    except (TypeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=403, detail="Native confirmation timestamp is invalid.") from exc
    confirmation_id = str(native_confirmation.get("confirmation_id") or "").strip()
    if not confirmation_id:
        raise HTTPException(status_code=403, detail="Native confirmation id is missing.")
    context: dict[str, Any] = {
        "channel": "desktop_native",
        "confirmation_id": confirmation_id,
        "confirmed_at_epoch": confirmed_at_epoch,
    }
    if native_confirmation.get("legacy_hmac"):
        context["proof_type"] = "legacy_hmac"
        context["legacy_hmac_fingerprint"] = str(
            native_confirmation.get("legacy_hmac_fingerprint") or ""
        ).strip()
    else:
        context["proof_type"] = "ed25519"
        try:
            challenge_expires_at_epoch = int(native_confirmation.get("challenge_expires_at_epoch") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=403, detail="Native confirmation expiry is invalid.") from exc
        context["challenge_expires_at_epoch"] = challenge_expires_at_epoch
        context["public_key_fingerprint"] = str(native_confirmation.get("public_key_fingerprint") or "").strip()
    fingerprint_key = "legacy_hmac_fingerprint" if context["proof_type"] == "legacy_hmac" else "public_key_fingerprint"
    if not context.get(fingerprint_key):
        raise HTTPException(status_code=403, detail="Native confirmation key binding is missing.")
    return authorized_at, context


async def _execute_approved_step(approval: Approval) -> Approval:
    db.require_sensitive_integrity_ok()
    if approval.tool_name == _DEVELOPER_LENGRVIS_CODE_TOOL:
        try:
            scheduled = _resume_runs_after_approval(approval.task_id, strict=True)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: fail closed instead of reporting a false schedule.
            latest = latest_approval(approval)
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Developer write approval was accepted, but the run could not be resumed.",
                    "error": redact_public_text(str(exc)),
                    "approval": latest_approval_payload(latest),
                },
            ) from exc
        latest = latest_approval(approval)
        if not scheduled and not latest.consumed_at:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Developer write approval was accepted, but no resumable run was found.",
                    "approval": latest_approval_payload(latest),
                },
            )
        return latest
    try:
        await OrchestratorAgent().execute_approved_step(approval)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        latest = latest_approval(approval)
        if latest.status == ApprovalStatus.APPROVED and not latest.consumed_at:
            _restore_retryable_approval_state(latest)
        else:
            task_data = db.fetch_one("tasks", approval.task_id)
            if task_data:
                task = Task.model_validate(task_data)
                task.final_summary = "审批已收到，但继续执行时失败。请查看任务时间线或授权工作区设置。"
                db.upsert_model("tasks", task)
                set_task_status(task.id, TaskStatus.FAILED)
        _reconcile_runs(approval.task_id)
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Approved operation did not complete execution.",
                "error": redact_public_text(str(exc)),
                "approval": latest_approval_payload(latest),
            },
        ) from exc
    _reconcile_runs(approval.task_id)
    _resume_runs_after_approval(approval.task_id)
    return latest_approval(approval)


def latest_approval(approval: Approval) -> Approval:
    data = db.fetch_one("approvals", approval.id)
    return Approval.model_validate(data) if data else approval


def latest_approval_payload(approval: Approval) -> dict:
    data = db.fetch_one("approvals", approval.id)
    return safe_approval_payload(data or approval)


def _restore_retryable_approval_state(approval: Approval) -> None:
    if approval_is_expired(approval):
        db.expire_approval_if_unconsumed(approval.id, now_iso(), "Approval authorization expired.")
        return
    task_data = db.fetch_one("tasks", approval.task_id)
    if not task_data:
        return
    task = Task.model_validate(task_data)
    plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)
    if plans:
        plan = Plan.model_validate(plans[0])
        step = next((item for item in plan.steps if item.id == approval.step_id), None)
        if step is not None:
            set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor="ApprovalRoute")
            db.upsert_model("plans", plan)
    safe_transition(task, TaskStatus.WAITING_USER_APPROVAL, actor="ApprovalRoute", strict=False)
    task.final_summary = "审批已收到，但执行时发生临时错误；审批仍可重试。"
    db.upsert_model("tasks", task)


def approval_for_execution(
    approval_id: str,
    claims: dict | None = None,
    *,
    authorized_at: str | None = None,
    auth_context: dict[str, Any] | None = None,
) -> Approval:
    db.require_sensitive_integrity_ok()
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = Approval.model_validate(data)
    raise_if_mobile_claims_disallowed(approval, claims)
    if approval_is_expired(approval):
        db.expire_approval_if_unconsumed(approval.id, now_iso(), "Approval authorization expired.")
        raise HTTPException(status_code=409, detail="Approval authorization expired.")
    if approval.status == ApprovalStatus.APPROVED:
        if approval.consumed_at:
            raise HTTPException(status_code=409, detail="Approval has already been consumed.")
        if claims is not None and authorized_at is None and auth_context is None:
            authorized_at = now_iso()
            auth_context = mobile_approval_auth_context(claims)
            if (
                not auth_context["device_id"]
                or not auth_context["token_family_id"]
                or not auth_context["credential_id"]
            ):
                raise HTTPException(status_code=401, detail="Mobile approval requires a device-bound session")
        if authorized_at is not None or auth_context is not None:
            refreshed = db.reauthorize_approval_atomically(
                approval.id,
                authorized_at or "",
                auth_context or {},
            )
            if not refreshed or refreshed.get("status") != ApprovalStatus.APPROVED.value:
                raise HTTPException(status_code=409, detail="Approval is no longer executable.")
            return Approval.model_validate(refreshed)
        return approval
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}.")
    try:
        return approve_mobile_approval(
            approval_id,
            claims,
            authorized_at=authorized_at,
            auth_context=auth_context,
        )
    except HTTPException as exc:
        refreshed = Approval.model_validate(db.fetch_one("approvals", approval_id) or data)
        if exc.status_code == 409 and refreshed.status == ApprovalStatus.APPROVED and not refreshed.consumed_at:
            return refreshed
        raise


def approval_execution_response(approval: Approval) -> dict:
    payload = latest_approval_payload(approval)
    if payload.get("status") != ApprovalStatus.APPROVED.value:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Approval is no longer executable.",
                "approval": payload,
            },
        )
    execution_error = _approval_execution_error(approval)
    if execution_error:
        raise HTTPException(
            status_code=503,
            detail={
                "message": execution_error,
                "approval": payload,
            },
        )
    if not payload.get("consumed_at"):
        if payload.get("tool_name") == _DEVELOPER_LENGRVIS_CODE_TOOL:
            return {
                **payload,
                "execution_scheduled": True,
                "message": "Developer write approval was accepted; the developer run will resume and consume it.",
            }
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Approved operation did not complete execution.",
                "approval": payload,
            },
        )
    return payload


def _approval_execution_error(approval: Approval) -> str:
    task_data = db.fetch_one("tasks", approval.task_id)
    if task_data:
        task = Task.model_validate(task_data)
        if task.status == TaskStatus.FAILED:
            return task.final_summary or "Approved operation failed during execution."
        if task.status in {TaskStatus.CANCELLED, TaskStatus.DENIED}:
            return task.final_summary or "Approved operation did not complete execution."
    plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)
    if plans:
        plan = Plan.model_validate(plans[0])
        step = next((item for item in plan.steps if item.id == approval.step_id), None)
        if step is not None and step.status in {StepStatus.FAILED, StepStatus.DENIED}:
            return "Approved operation failed during execution."
    return ""


def _reconcile_runs(task_id: str) -> None:
    try:
        from app.services.run_service import reconcile_task_runs

        reconcile_task_runs(task_id)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: run reconciliation is best-effort after approval changes.
        return


def _deny_rejected_step(approval: Approval) -> None:
    task_data = db.fetch_one("tasks", approval.task_id)
    if not task_data:
        return
    task = Task.model_validate(task_data)
    plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)
    if plans:
        plan = Plan.model_validate(plans[0])
        target_step = None
        for step in plan.steps:
            if step.id == approval.step_id:
                target_step = step
                break
        if target_step is None:
            waiting_steps = [
                step
                for step in plan.steps
                if step.status == StepStatus.WAITING_USER_APPROVAL and step.requires_approval
            ]
            if len(waiting_steps) == 1:
                target_step = waiting_steps[0]
        if target_step is not None:
            set_step_status(target_step, StepStatus.DENIED, actor="routes_approvals.reject")
        db.upsert_model("plans", plan)
    task.final_summary = "Approval was rejected by the user."
    db.upsert_model("tasks", task)
    set_task_status(task.id, TaskStatus.CANCELLED)


def _resume_runs_after_approval(task_id: str, *, strict: bool = False) -> bool:
    try:
        from app.core.schemas import Plan, StepStatus, Task, TaskStatus
        from app.services.run_service import resume_runs_for_task

        task_data = db.fetch_one("tasks", task_id)
        if not task_data:
            return False
        task = Task.model_validate(task_data)
        if task.status not in {TaskStatus.EXECUTING_STEP, TaskStatus.EXECUTION, TaskStatus.WAITING_USER_APPROVAL}:
            return False
        plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
        if not plans:
            return False
        plan = Plan.model_validate(plans[0])
        if not any(step.status in {StepStatus.PENDING, StepStatus.WAITING_USER_APPROVAL} for step in plan.steps):
            return False
        return bool(resume_runs_for_task(task_id, include_approval_continuations=True))
    except Exception:  # noqa: BLE001 - broad-exception-boundary: non-strict callers reconcile best-effort.
        if strict:
            raise
        return False
