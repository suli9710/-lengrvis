from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.routes_approvals import _execute_approved_step
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
    await _execute_approved_step(approval)
    return {"ok": True, "approval_id": approval_id, "task_id": approval.task_id}
