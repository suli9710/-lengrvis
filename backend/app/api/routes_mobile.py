from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.routes_approvals import approve as approve_approval
from app.api.routes_approvals import reject as reject_approval
from app.core import db
from app.core.schemas import Approval
from app.security.mobile_jwt import decode_mobile_token, require_mobile_token
from app.services.approval_event_service import get_approval_event_bus


router = APIRouter()
ws_router = APIRouter()


class MobileApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|denied)$")
    note: str = ""


@router.get("/mobile/approvals/pending")
def pending_mobile_approvals(_token: dict = Depends(require_mobile_token)) -> list[dict]:
    return db.fetch_many("approvals", "status = ?", ("pending",))


@router.post("/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: MobileApprovalDecision,
    _token: dict = Depends(require_mobile_token),
) -> Approval:
    if request.decision == "approved":
        return await approve_approval(approval_id)
    return reject_approval(approval_id)


@ws_router.websocket("/ws/mobile/approvals")
async def mobile_approval_events(websocket: WebSocket, token: str = ""):
    try:
        claims = decode_mobile_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = get_approval_event_bus().subscribe()
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "device_id": claims.get("device_id"),
                "pending": db.fetch_many("approvals", "status = ?", ("pending",)),
            }
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        get_approval_event_bus().unsubscribe(queue)
