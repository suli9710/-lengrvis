from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Plan, StepStatus, Task, TaskStatus
from app.orchestration.step_phase import set_step_status
from app.orchestration.state_machine import safe_transition
from app.services.mobile_pairing_service import approve_approval as approve_mobile_approval
from app.services.mobile_pairing_service import safe_approval_payload
from app.services.mobile_pairing_service import list_pending_approvals
from app.services.mobile_pairing_service import raise_if_mobile_claims_disallowed
from app.services.mobile_pairing_service import reject_approval as reject_mobile_approval
from app.services.task_service import set_task_status


router = APIRouter()


@router.get("/approvals/pending")
def pending():
    return list_pending_approvals()


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str):
    approval = approval_for_execution(approval_id)
    approval = await _execute_approved_step(approval)
    return approval_execution_response(approval)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str):
    approval = reject_mobile_approval(approval_id)
    _deny_rejected_step(approval)
    _reconcile_runs(approval.task_id)
    return safe_approval_payload(approval)


async def _execute_approved_step(approval: Approval) -> Approval:
    try:
        await OrchestratorAgent().execute_approved_step(approval)
    except Exception as exc:
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
                "error": str(exc),
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


def approval_for_execution(approval_id: str, claims: dict | None = None) -> Approval:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = Approval.model_validate(data)
    raise_if_mobile_claims_disallowed(approval, claims)
    if approval.status == ApprovalStatus.APPROVED:
        if approval.consumed_at:
            raise HTTPException(status_code=409, detail="Approval has already been consumed.")
        return approval
    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {approval.status}.")
    try:
        return approve_mobile_approval(approval_id, claims)
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
    except Exception:
        return


def _deny_rejected_step(approval: Approval) -> None:
    task_data = db.fetch_one("tasks", approval.task_id)
    if not task_data:
        return
    task = Task.model_validate(task_data)
    plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)
    if plans:
        plan = Plan.model_validate(plans[0])
        for step in plan.steps:
            if step.id == approval.step_id:
                step.status = StepStatus.DENIED
                break
        db.upsert_model("plans", plan)
    task.final_summary = "Approval was rejected by the user."
    db.upsert_model("tasks", task)
    set_task_status(task.id, TaskStatus.CANCELLED)


def _resume_runs_after_approval(task_id: str) -> None:
    try:
        from app.core.schemas import Plan, StepStatus, Task, TaskStatus
        from app.services.run_service import resume_runs_for_task

        task_data = db.fetch_one("tasks", task_id)
        if not task_data:
            return
        task = Task.model_validate(task_data)
        if task.status not in {TaskStatus.EXECUTING_STEP, TaskStatus.EXECUTION}:
            return
        plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
        if not plans:
            return
        plan = Plan.model_validate(plans[0])
        if not any(step.status == StepStatus.PENDING for step in plan.steps):
            return
        resume_runs_for_task(task_id, include_approval_continuations=True)
    except Exception:
        return
