from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api import routes_approvals
from app.security.mobile_jwt import (
    TOKEN_SCOPE,
    decode_mobile_token,
    mobile_token_from_websocket,
    require_mobile_or_remote_input_token,
    require_mobile_token,
    validate_mobile_claims_active,
)
from app.services import mobile_pairing_service
from app.services.approval_event_service import get_approval_event_bus


router = APIRouter()
ws_router = APIRouter()


class MobileApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|denied)$")
    note: str = ""


@router.get("/mobile/approvals/pending")
def pending_mobile_approvals(token: dict = Depends(require_mobile_token)) -> list[dict]:
    return mobile_pairing_service.list_pending_approvals(token)


@router.get("/mobile/approvals/{approval_id}")
def mobile_approval_detail(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.get_approval_detail(approval_id, token)


@router.post("/mobile/approvals/{approval_id}/approve")
async def approve_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = routes_approvals.approval_for_execution(approval_id, token)
    approval = await routes_approvals._execute_approved_step(approval)
    return routes_approvals.approval_execution_response(approval)


@router.post("/mobile/approvals/{approval_id}/reject")
def reject_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = mobile_pairing_service.reject_approval(approval_id, token)
    routes_approvals._deny_rejected_step(approval)
    routes_approvals._reconcile_runs(approval.task_id)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.post("/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: MobileApprovalDecision,
    token: dict = Depends(require_mobile_or_remote_input_token),
) -> dict:
    if request.decision == "approved":
        return await approve_mobile_approval(approval_id, token)
    return reject_mobile_approval(approval_id, token)


@router.get("/mobile/devices")
def list_mobile_devices(token: dict = Depends(require_mobile_token)) -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices(token)}


@router.delete("/mobile/devices/{device_id}")
def revoke_mobile_device(device_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.revoke_own_mobile_device(device_id, token)


@router.post("/mobile/remote-input-grants/{grant_id}/token")
def claim_remote_input_grant_token(grant_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.claim_remote_input_grant_token(grant_id, token)


@ws_router.websocket("/ws/mobile/notifications")
async def mobile_notifications(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token, notification_alias=True)


@ws_router.websocket("/ws/mobile/approvals")
async def mobile_approval_events_legacy(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token)


async def _mobile_notifications(websocket: WebSocket, token: str = "", *, notification_alias: bool = False):
    try:
        claims = decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={TOKEN_SCOPE})
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
                "pending": mobile_pairing_service.list_pending_approvals(claims),
            }
        )
        while True:
            if await _close_if_mobile_claims_inactive(websocket, claims):
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                if not _mobile_event_allowed(event, claims):
                    continue
                if event.get("type") == "mobile_device_revoked":
                    await websocket.send_json(event)
                    await websocket.close(code=1008)
                    return
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                if notification_alias and event.get("type") == "approval_created":
                    await websocket.send_json({"type": "approval_notification", "approval": event.get("approval")})
                else:
                    await websocket.send_json(event)
            except asyncio.TimeoutError:
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return
    finally:
        get_approval_event_bus().unsubscribe(queue)


async def _close_if_mobile_claims_inactive(websocket: WebSocket, claims: dict) -> bool:
    try:
        validate_mobile_claims_active(claims)
    except HTTPException:
        await websocket.close(code=1008)
        return True
    return False


def _mobile_event_allowed(event: dict, claims: dict) -> bool:
    if event.get("type") in {"remote_input_grant_created", "remote_input_grant_revoked", "mobile_device_revoked"}:
        return str(event.get("device_id") or "") == str(claims.get("device_id") or "")
    approval = event.get("approval")
    if not isinstance(approval, dict):
        return True
    return mobile_pairing_service.mobile_claims_can_access_approval(approval, claims)
