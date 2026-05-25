from __future__ import annotations

from fastapi import APIRouter

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, now_iso
from app.services.mobile_pairing_service import approve_approval as approve_mobile_approval
from app.services.mobile_pairing_service import reject_approval as reject_mobile_approval


router = APIRouter()


@router.get("/approvals/pending")
def pending():
    return db.fetch_many("approvals", "status = ?", ("pending",))


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str):
    approval = approve_mobile_approval(approval_id)
    await _execute_approved_step(approval)
    return approval


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str):
    return reject_mobile_approval(approval_id)


async def _execute_approved_step(approval: Approval) -> None:
    try:
        await OrchestratorAgent().execute_approved_step(approval)
    except Exception:
        task_data = db.fetch_one("tasks", approval.task_id)
        if not task_data:
            return
        from app.core.schemas import Task, TaskStatus

        task = Task.model_validate(task_data)
        task.status = TaskStatus.FAILED
        task.final_summary = "审批已收到，但继续执行时失败。请查看任务时间线或授权工作区设置。"
        task.updated_at = now_iso()
        db.upsert_model("tasks", task)
