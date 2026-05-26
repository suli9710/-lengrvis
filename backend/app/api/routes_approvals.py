from __future__ import annotations

from fastapi import APIRouter

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, Plan, StepStatus, Task, TaskStatus
from app.services.mobile_pairing_service import approve_approval as approve_mobile_approval
from app.services.mobile_pairing_service import safe_approval_payload
from app.services.mobile_pairing_service import list_pending_approvals
from app.services.mobile_pairing_service import reject_approval as reject_mobile_approval
from app.services.task_service import set_task_status


router = APIRouter()


@router.get("/approvals/pending")
def pending():
    return list_pending_approvals()


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str):
    approval = approve_mobile_approval(approval_id)
    await _execute_approved_step(approval)
    return safe_approval_payload(approval)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str):
    approval = reject_mobile_approval(approval_id)
    _deny_rejected_step(approval)
    _reconcile_runs(approval.task_id)
    return safe_approval_payload(approval)


async def _execute_approved_step(approval: Approval) -> None:
    try:
        await OrchestratorAgent().execute_approved_step(approval)
        _reconcile_runs(approval.task_id)
        _resume_runs_after_approval(approval.task_id)
    except Exception:
        task_data = db.fetch_one("tasks", approval.task_id)
        if not task_data:
            return
        from app.core.schemas import Task, TaskStatus

        task = Task.model_validate(task_data)
        task.final_summary = "审批已收到，但继续执行时失败。请查看任务时间线或授权工作区设置。"
        db.upsert_model("tasks", task)
        set_task_status(task.id, TaskStatus.FAILED)
        _reconcile_runs(approval.task_id)


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
