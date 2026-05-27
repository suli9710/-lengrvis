from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.api.routes_pair import PairRedeemRequest
from app.core import db
from app.core.schemas import AgentMessage, Approval, MessageType, RunCreateRequest, Wakeup
from app.orchestration.agent_bus import GLOBAL_TASK_ID
from app.security.mobile_jwt import require_mobile_token
from app.services import mobile_pairing_service, wakeup_service
from app.services.guardian_runtime import runtime
from app.services.notification_service import SYSTEM_TASK_ID
from app.services.scheduler_service import get_scheduler


router = APIRouter()
proxy_router = APIRouter()
ws_router = APIRouter()


@router.get("/health")
@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "mode": "guardian", **await runtime.status()}


@router.get("/api/runtime/status")
async def runtime_status() -> dict[str, Any]:
    return await runtime.status()


@router.post("/api/runtime/foreground")
async def runtime_foreground(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return await runtime.enter_foreground(reason=str((payload or {}).get("reason") or "foreground_requested"))


@router.post("/api/runtime/background")
async def runtime_background(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    return await runtime.enter_background(reason=str((payload or {}).get("reason") or "background_requested"))


@router.post("/api/pair/request")
@router.post("/api/pair/code")
def create_pairing_code() -> dict:
    return mobile_pairing_service.create_pairing_request()


@router.post("/api/pair/confirm")
@router.post("/api/pair")
def confirm_pairing(payload: PairRedeemRequest, request: Request) -> dict:
    client_host = request.client.host if request.client else ""
    return mobile_pairing_service.confirm_pairing(
        code=payload.code,
        device_name=payload.device_name,
        client_host=client_host,
    )


@router.get("/api/pair/devices")
def list_paired_devices() -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices()}


@router.get("/api/approvals/pending")
def pending_approvals() -> list[dict]:
    return mobile_pairing_service.list_pending_approvals()


@router.post("/api/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str) -> dict:
    approval = mobile_pairing_service.approve_approval(approval_id)
    await _wake_full_backend_for_approval(approval)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.post("/api/approvals/{approval_id}/reject")
def reject_approval(approval_id: str) -> dict:
    approval = mobile_pairing_service.reject_approval(approval_id)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.get("/api/mobile/approvals/pending")
def pending_mobile_approvals(_token: dict = Depends(require_mobile_token)) -> list[dict]:
    return mobile_pairing_service.list_pending_approvals()


@router.get("/api/mobile/approvals/{approval_id}")
def mobile_approval_detail(approval_id: str, _token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.get_approval_detail(approval_id)


@router.post("/api/mobile/approvals/{approval_id}/approve")
async def approve_mobile_approval(approval_id: str, _token: dict = Depends(require_mobile_token)) -> dict:
    return await approve_approval(approval_id)


@router.post("/api/mobile/approvals/{approval_id}/reject")
def reject_mobile_approval(approval_id: str, _token: dict = Depends(require_mobile_token)) -> dict:
    return reject_approval(approval_id)


@router.post("/api/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: dict = Body(...),
    _token: dict = Depends(require_mobile_token),
) -> dict:
    decision = str((request or {}).get("decision") or "").lower()
    if decision == "approved":
        return await approve_approval(approval_id)
    return reject_approval(approval_id)


@router.get("/api/mobile/devices")
def list_mobile_devices(_token: dict = Depends(require_mobile_token)) -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices()}


@router.get("/api/schedules")
def list_schedules() -> list[Any]:
    return get_scheduler().list()


@router.get("/api/schedules/status")
def schedules_status() -> dict[str, Any]:
    from app.services.guardian_scheduler import get_guardian_scheduler

    sched = get_scheduler()
    return {
        **get_guardian_scheduler().status(),
        "schedules": [item.model_dump(mode="json") for item in sched.list()],
        "cron_engine": "croniter",
    }


@router.post("/api/schedules")
def create_schedule(payload: dict[str, Any] = Body(...)) -> Any:
    return get_scheduler().schedule(
        str(payload.get("cron") or ""),
        str(payload.get("goal") or ""),
        str(payload.get("mode") or "efficiency"),
        note=str(payload.get("note") or ""),
    )


@router.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    ok = get_scheduler().cancel(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True, "id": schedule_id}


@router.post("/api/schedules/{schedule_id}/enable")
def enable_schedule(schedule_id: str, payload: dict[str, Any] = Body(...)) -> Any:
    item = get_scheduler().enable(schedule_id, bool(payload.get("enabled", True)))
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return item


@router.get("/api/wakeups/pending")
def pending_wakeups() -> list[dict[str, Any]]:
    return wakeup_service.list_pending_wakeups()


@router.post("/api/wakeups/{wakeup_id}/approve")
async def approve_wakeup(wakeup_id: str) -> dict[str, Any]:
    wakeup = wakeup_service.approve_wakeup(wakeup_id)
    await _execute_wakeup(wakeup)
    return wakeup.model_dump(mode="json")


@router.post("/api/wakeups/{wakeup_id}/reject")
def reject_wakeup(wakeup_id: str) -> dict[str, Any]:
    return wakeup_service.reject_wakeup(wakeup_id).model_dump(mode="json")


@router.get("/api/mobile/wakeups/pending")
def pending_mobile_wakeups(_token: dict = Depends(require_mobile_token)) -> list[dict[str, Any]]:
    return pending_wakeups()


@router.post("/api/mobile/wakeups/{wakeup_id}/approve")
async def approve_mobile_wakeup(wakeup_id: str, _token: dict = Depends(require_mobile_token)) -> dict[str, Any]:
    return await approve_wakeup(wakeup_id)


@router.post("/api/mobile/wakeups/{wakeup_id}/reject")
def reject_mobile_wakeup(wakeup_id: str, _token: dict = Depends(require_mobile_token)) -> dict[str, Any]:
    return reject_wakeup(wakeup_id)


@ws_router.websocket("/ws/notifications")
@ws_router.websocket("/api/ws/notifications")
async def notification_messages(websocket: WebSocket):
    await websocket.accept()
    seen: set[str] = set()
    try:
        await websocket.send_json({"type": "connected", "task_id": SYSTEM_TASK_ID})
        while True:
            for message in _notification_messages():
                if message.id in seen:
                    continue
                seen.add(message.id)
                await websocket.send_json({"type": "agent_message", "task_id": message.task_id, "message": message.to_openai_dict()})
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        return


@ws_router.websocket("/ws/mobile/notifications")
@ws_router.websocket("/api/ws/mobile/notifications")
@ws_router.websocket("/ws/mobile/approvals")
@ws_router.websocket("/api/ws/mobile/approvals")
async def mobile_notifications(websocket: WebSocket, token: str = ""):
    from app.security.mobile_jwt import decode_mobile_token

    try:
        claims = decode_mobile_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    seen: set[str] = set()
    try:
        await websocket.send_json({"type": "connected", "device_id": claims.get("device_id"), "pending": mobile_pairing_service.list_pending_approvals()})
        while True:
            for approval in mobile_pairing_service.list_pending_approvals():
                approval_id = str(approval.get("id") or "")
                if not approval_id or approval_id in seen:
                    continue
                seen.add(approval_id)
                await websocket.send_json({"type": "approval_notification", "approval": approval})
            await asyncio.sleep(25.0)
            await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return


@ws_router.websocket("/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    if runtime.shell_mode != "foreground":
        await websocket.close(code=1013)
        return
    await runtime.ensure_full_backend(reason=f"ws_proxy:/{path}")
    target = _full_backend_ws_url(path, websocket.url.query)
    await websocket.accept()
    try:
        async with websockets.connect(target) as upstream:
            await asyncio.gather(
                _client_to_upstream(websocket, upstream),
                _upstream_to_client(websocket, upstream),
            )
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass


@proxy_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_full_backend(path: str, request: Request) -> Response:
    raw_body = await request.body()
    response = await runtime.proxy(
        request.method,
        f"/{path}",
        query=request.url.query.encode("utf-8"),
        headers=dict(request.headers),
        body=raw_body,
    )
    return _proxy_response(response)


async def _wake_full_backend_for_approval(approval: Approval) -> None:
    await runtime.wake_transient(reason=f"approval:{approval.id}")
    async with httpx.AsyncClient(timeout=120.0) as client:
        await client.post(f"{full_backend_url()}/api/runtime/approvals/{approval.id}/continue")


async def _execute_wakeup(wakeup: Wakeup) -> None:
    await runtime.wake_transient(reason=f"wakeup:{wakeup.id}")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{full_backend_url()}/api/runs",
                json=RunCreateRequest(message=wakeup.goal, mode=wakeup.mode).model_dump(mode="json"),
            )
        if response.status_code >= 400:
            wakeup_service.complete_wakeup(wakeup, error=response.text)
            return
        data = response.json()
        wakeup_service.complete_wakeup(wakeup, run_id=str(data.get("run_id") or ""))
    except Exception as exc:  # noqa: BLE001
        wakeup_service.complete_wakeup(wakeup, error=str(exc))


def full_backend_url() -> str:
    from app.services.guardian_runtime import FULL_BACKEND_URL

    return FULL_BACKEND_URL


def _full_backend_ws_url(path: str, query: str) -> str:
    base = full_backend_url().replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    suffix = "/" + path.lstrip("/")
    if query:
        suffix += f"?{query}"
    return f"{base}{suffix}"


async def _client_to_upstream(websocket: WebSocket, upstream: Any) -> None:
    while True:
        message = await websocket.receive()
        if "text" in message:
            await upstream.send(message["text"])
        elif "bytes" in message:
            await upstream.send(message["bytes"])
        elif message.get("type") == "websocket.disconnect":
            await upstream.close()
            return


async def _upstream_to_client(websocket: WebSocket, upstream: Any) -> None:
    async for message in upstream:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
        else:
            await websocket.send_text(str(message))


def _notification_messages() -> list[AgentMessage]:
    messages: list[AgentMessage] = []
    for task_id in (SYSTEM_TASK_ID, GLOBAL_TASK_ID):
        for item in db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=200):
            try:
                message = AgentMessage.model_validate(item)
            except Exception:
                continue
            if message.message_type == MessageType.NOTIFICATION:
                messages.append(message)
    return sorted(messages, key=lambda item: (item.created_at, item.id))


def _proxy_response(response: httpx.Response) -> Response:
    excluded = {"content-encoding", "transfer-encoding", "connection"}
    headers = {key: value for key, value in response.headers.items() if key.lower() not in excluded}
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=headers,
        media_type=response.headers.get("content-type"),
    )
