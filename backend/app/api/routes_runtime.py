from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.routes_approvals import _execute_approved_step, approval_execution_response, latest_approval_payload
from app.core.schemas import ApprovalStatus
from app.core import db
from app.core.schemas import Approval
from app.services import run_service


router = APIRouter()


@router.get("/runtime/status")
def runtime_status() -> dict:
    return {
        "mode": "full",
        "status": "running",
        **run_service.runtime_status(),
    }


@router.post("/runtime/foreground")
def runtime_foreground() -> dict:
    return {"mode": "full", "status": "foreground_ready", **run_service.enter_foreground_runtime()}


@router.post("/runtime/background")
async def runtime_background() -> dict:
    return {
        "mode": "full",
        "status": "background_ready",
        **await run_service.prepare_for_background(),
    }


@router.post("/runtime/approvals/{approval_id}/continue")
async def continue_approved_step(approval_id: str) -> dict:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise HTTPException(status_code=404, detail="Approval not found")
    approval = Approval.model_validate(data)
    if approval.status != ApprovalStatus.APPROVED or approval.consumed_at:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Approval is no longer executable.",
                "approval": latest_approval_payload(approval),
            },
        )
    approval = await _execute_approved_step(approval)
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id) or approval)
    if refreshed.status != ApprovalStatus.APPROVED or not refreshed.consumed_at:
        raise HTTPException(
            status_code=503 if refreshed.status == ApprovalStatus.APPROVED else 409,
            detail={
                "message": "Approval is no longer executable.",
                "approval": latest_approval_payload(refreshed),
            },
        )
    approval_execution_response(refreshed)
    return {"ok": True, "approval_id": approval_id, "task_id": refreshed.task_id, "status": refreshed.status.value}
