from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.routes_approvals import approve as approve_desktop_approval
from app.core.schemas import Approval
from app.security.mobile_jwt import decode_mobile_token, require_mobile_token
from app.services import mobile_pairing_service
from app.services.approval_event_service import get_approval_event_bus


router = APIRouter()
ws_router = APIRouter()


class MobileApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|denied)$")
    note: str = ""


@router.get("/mobile/approvals/pending")
def pending_mobile_approvals(_token: dict = Depends(require_mobile_token)) -> list[dict]:
    return mobile_pairing_service.list_pending_approvals()


@router.get("/mobile/approvals/{approval_id}")
def mobile_approval_detail(approval_id: str, _token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.get_approval_detail(approval_id)


@router.post("/mobile/approvals/{approval_id}/approve")
async def approve_mobile_approval(approval_id: str, _token: dict = Depends(require_mobile_token)) -> Approval:
    return await approve_desktop_approval(approval_id)


@router.post("/mobile/approvals/{approval_id}/reject")
def reject_mobile_approval(approval_id: str, _token: dict = Depends(require_mobile_token)) -> Approval:
    return mobile_pairing_service.reject_approval(approval_id)


@router.post("/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: MobileApprovalDecision,
    _token: dict = Depends(require_mobile_token),
) -> Approval:
    if request.decision == "approved":
        return await approve_desktop_approval(approval_id)
    return mobile_pairing_service.reject_approval(approval_id)


@router.get("/mobile/devices")
def list_mobile_devices(_token: dict = Depends(require_mobile_token)) -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices()}


@ws_router.websocket("/ws/mobile/notifications")
async def mobile_notifications(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token, notification_alias=True)


@ws_router.websocket("/ws/mobile/approvals")
async def mobile_approval_events_legacy(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token)


async def _mobile_notifications(websocket: WebSocket, token: str = "", *, notification_alias: bool = False):
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
                "pending": mobile_pairing_service.list_pending_approvals(),
            }
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                if notification_alias and event.get("type") == "approval_created":
                    await websocket.send_json({"type": "approval_notification", "approval": event.get("approval")})
                else:
                    await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        get_approval_event_bus().unsubscribe(queue)
