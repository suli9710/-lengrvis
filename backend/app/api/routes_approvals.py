from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.services.approval_event_service import publish_approval_decided


router = APIRouter()


@router.get("/approvals/pending")
def pending():
    return db.fetch_many("approvals", "status = ?", ("pending",))


def _decide(approval_id: str, status: ApprovalStatus):
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = Approval.model_validate(data)
    approval.status = status
    approval.decided_at = now_iso()
    db.upsert_model("approvals", approval, status=status)
    publish_approval_decided(approval)
    return approval


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str):
    approval = _decide(approval_id, ApprovalStatus.APPROVED)
    await _execute_approved_step(approval)
    return approval


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str):
    return _decide(approval_id, ApprovalStatus.REJECTED)


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
