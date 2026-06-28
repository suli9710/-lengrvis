from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.routes_approvals import (
    _approval_native_confirmation,
    _deny_rejected_step,
    _reconcile_runs,
    _record_desktop_native_confirmation,
    _rejection_native_confirmation,
    approval_execution_response,
    approval_for_execution,
)
from app.api.routes_schedules import _require_scheduling
from app.core import db
from app.core.audit import record
from app.core.schemas import AgentMessage, Approval, MessageType, RunCreateRequest, Wakeup, WakeupStatus, now_iso
from app.orchestration.agent_bus import GLOBAL_TASK_ID
from app.policy.redaction import redact_value
from app.security.desktop_api import (
    DESKTOP_API_TOKEN_HEADER,
    DESKTOP_API_WS_PROTOCOL_PREFIX,
    close_unauthorized_desktop_websocket,
    desktop_api_token_headers,
)
from app.security.lan import is_mobile_token_websocket_path, is_secure_mobile_transport
from app.security.mobile_jwt import (
    TOKEN_SCOPE,
    mobile_token_from_websocket,
    require_mobile_or_remote_input_token,
    require_mobile_token,
    validate_mobile_claims_active,
)
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    create_native_confirmation_challenge,
    enforce_native_confirmation_challenge_rate_limit,
    require_native_confirmation,
)
from app.services import mobile_pairing_service, wakeup_service
from app.services.approval_event_service import get_approval_event_bus
from app.services.guardian_runtime import runtime
from app.services.notification_service import SYSTEM_TASK_ID
from app.services.scheduler_service import get_scheduler

router = APIRouter()
proxy_router = APIRouter()
ws_router = APIRouter()


class MobileApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|denied)$")


class WakeupNativeConfirmationChallengeRequest(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


def _client_scope(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return (host or "unknown").strip().lower() or "unknown"


def _wakeup_native_confirmation(
    wakeup_id: str,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
) -> dict[str, Any]:
    return require_native_confirmation(
        action="approve",
        approval_id=wakeup_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
    )


def _wakeup_rejection_native_confirmation(
    wakeup_id: str,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
) -> dict[str, Any]:
    return require_native_confirmation(
        action="reject",
        approval_id=wakeup_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
    )


def _record_desktop_wakeup_native_confirmation(
    wakeup: Wakeup,
    native_confirmation: dict[str, Any],
    *,
    decision: str,
) -> None:
    record(
        "wakeup.desktop_native_confirmed",
        "DesktopMain",
        {
            "wakeup_id": wakeup.id,
            "source": wakeup.source,
            "source_id": wakeup.source_id,
            "decision": decision,
            "desktop_native_confirmed": True,
            "desktop_native_confirmed_at": now_iso(),
            "desktop_native_confirmation_id": native_confirmation.get("confirmation_id"),
            "confirmation_evidence": {
                "wakeup_id": wakeup.id,
                "goal": redact_value(wakeup.goal or ""),
                "mode": wakeup.mode,
            },
        },
        task_id=wakeup.source_id or wakeup.id,
    )


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


@router.get("/api/approvals/pending")
def pending_approvals() -> list[dict]:
    return mobile_pairing_service.list_pending_approvals()


@router.post("/api/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: str,
    native_confirmation: dict[str, Any] = Depends(_approval_native_confirmation),
) -> dict:
    approval = approval_for_execution(approval_id)
    _record_desktop_native_confirmation(approval, "approve", native_confirmation)
    approval = await _wake_full_backend_for_approval(approval) or approval
    return approval_execution_response(approval)


@router.post("/api/approvals/{approval_id}/reject")
def reject_approval(
    approval_id: str,
    native_confirmation: dict[str, Any] = Depends(_rejection_native_confirmation),
) -> dict:
    before = db.fetch_one("approvals", approval_id)
    approval = mobile_pairing_service.reject_approval(approval_id)
    _record_desktop_native_confirmation(
        Approval.model_validate(before) if before else approval, "reject", native_confirmation
    )
    _deny_rejected_step(approval)
    _reconcile_runs(approval.task_id)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.get("/api/mobile/approvals/pending")
def pending_mobile_approvals(token: dict = Depends(require_mobile_token)) -> list[dict]:
    return mobile_pairing_service.list_pending_approvals(token)


@router.get("/api/mobile/approvals/{approval_id}")
def mobile_approval_detail(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.get_approval_detail(approval_id, token)


@router.post("/api/mobile/approvals/{approval_id}/approve")
async def approve_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = approval_for_execution(approval_id, token)
    approval = await _wake_full_backend_for_approval(approval) or approval
    return approval_execution_response(approval)


@router.post("/api/mobile/approvals/{approval_id}/reject")
def reject_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = mobile_pairing_service.reject_approval(approval_id, token)
    _deny_rejected_step(approval)
    _reconcile_runs(approval.task_id)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.post("/api/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: MobileApprovalDecision = Body(...),
    token: dict = Depends(require_mobile_or_remote_input_token),
) -> dict:
    if request.decision == "approved":
        return await approve_mobile_approval(approval_id, token)
    return reject_mobile_approval(approval_id, token)


@router.get("/api/mobile/devices")
def list_mobile_devices(token: dict = Depends(require_mobile_token)) -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices(token)}


@router.post("/api/mobile/remote-input-grants/{grant_id}/token")
def claim_remote_input_grant_token(grant_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.claim_remote_input_grant_token(grant_id, token)


@router.get("/api/schedules")
def list_schedules() -> list[Any]:
    _require_scheduling()
    return get_scheduler().list()


@router.get("/api/schedules/status")
def schedules_status() -> dict[str, Any]:
    _require_scheduling()
    from app.services.guardian_scheduler import get_guardian_scheduler

    sched = get_scheduler()
    return {
        **get_guardian_scheduler().status(),
        "schedules": [item.model_dump(mode="json") for item in sched.list()],
        "cron_engine": "croniter",
    }


@router.post("/api/schedules")
def create_schedule(payload: dict[str, Any] = Body(...)) -> Any:
    _require_scheduling()
    return get_scheduler().schedule(
        str(payload.get("cron") or ""),
        str(payload.get("goal") or ""),
        str(payload.get("mode") or "efficiency"),
        note=str(payload.get("note") or ""),
    )


@router.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    _require_scheduling()
    ok = get_scheduler().cancel(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True, "id": schedule_id}


@router.post("/api/schedules/{schedule_id}/enable")
def enable_schedule(schedule_id: str, payload: dict[str, Any] = Body(...)) -> Any:
    _require_scheduling()
    item = get_scheduler().enable(schedule_id, bool(payload.get("enabled", True)))
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return item


@router.get("/api/wakeups/pending")
def pending_wakeups() -> list[dict[str, Any]]:
    return [wakeup_service.safe_wakeup_payload(item) for item in wakeup_service.list_pending_wakeups()]


@router.post("/api/wakeups/{wakeup_id}/native-confirmation-challenge")
def wakeup_native_confirmation_challenge(
    wakeup_id: str,
    payload: WakeupNativeConfirmationChallengeRequest,
    request: Request,
) -> dict[str, Any]:
    enforce_native_confirmation_challenge_rate_limit(_client_scope(request))
    db.require_sensitive_integrity_ok()
    wakeup = wakeup_service.get_wakeup(wakeup_id)
    if wakeup.status != WakeupStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Wakeup is already {wakeup.status}.")
    return create_native_confirmation_challenge(
        action=payload.action,
        approval_id=wakeup.id,
    )


@router.post("/api/wakeups/{wakeup_id}/approve")
async def approve_wakeup(
    wakeup_id: str,
    native_confirmation: dict[str, Any] = Depends(_wakeup_native_confirmation),
) -> dict[str, Any]:
    wakeup = wakeup_service.get_wakeup(wakeup_id)
    _record_desktop_wakeup_native_confirmation(wakeup, native_confirmation, decision="approve")
    wakeup = wakeup_service.approve_wakeup(wakeup_id)
    try:
        await _execute_wakeup(wakeup)
    except Exception as exc:  # noqa: BLE001 - wakeup execution should settle into a failed wakeup instead of surfacing a stale approval.
        wakeup_service.complete_wakeup(wakeup, error=str(exc))
    refreshed = wakeup_service.get_wakeup(wakeup.id)
    if refreshed.status == "failed":
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Wakeup execution failed.",
                "wakeup": wakeup_service.safe_wakeup_payload(refreshed),
            },
        )
    return wakeup_service.safe_wakeup_payload(refreshed)


@router.post("/api/wakeups/{wakeup_id}/reject")
def reject_wakeup(
    wakeup_id: str,
    native_confirmation: dict[str, Any] = Depends(_wakeup_rejection_native_confirmation),
) -> dict[str, Any]:
    wakeup = wakeup_service.get_wakeup(wakeup_id)
    _record_desktop_wakeup_native_confirmation(wakeup, native_confirmation, decision="reject")
    wakeup = wakeup_service.reject_wakeup(wakeup_id)
    return wakeup_service.safe_wakeup_payload(wakeup_service.get_wakeup(wakeup.id))


@router.get("/api/mobile/wakeups/pending")
def pending_mobile_wakeups(token: dict = Depends(require_mobile_token)) -> list[dict[str, Any]]:
    return wakeup_service.list_pending_mobile_wakeups(token)


@router.post("/api/mobile/wakeups/{wakeup_id}/approve")
async def approve_mobile_wakeup(wakeup_id: str, token: dict = Depends(require_mobile_token)) -> dict[str, Any]:
    wakeup = wakeup_service.approve_wakeup(wakeup_id, token)
    try:
        await _execute_wakeup(wakeup)
    except Exception as exc:  # noqa: BLE001 - wakeup execution should settle into a failed wakeup instead of surfacing a stale approval.
        wakeup_service.complete_wakeup(wakeup, error=str(exc))
    refreshed = wakeup_service.get_wakeup(wakeup.id)
    if refreshed.status == "failed":
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Wakeup execution failed.",
                "wakeup": wakeup_service.safe_wakeup_payload(refreshed),
            },
        )
    return wakeup_service.safe_wakeup_payload(refreshed)


@router.post("/api/mobile/wakeups/{wakeup_id}/reject")
def reject_mobile_wakeup(wakeup_id: str, token: dict = Depends(require_mobile_token)) -> dict[str, Any]:
    wakeup = wakeup_service.reject_wakeup(wakeup_id, token)
    return wakeup_service.safe_wakeup_payload(wakeup_service.get_wakeup(wakeup.id))


@ws_router.websocket("/ws/notifications")
@ws_router.websocket("/api/ws/notifications")
async def notification_messages(websocket: WebSocket):
    if await close_unauthorized_desktop_websocket(websocket):
        return
    await websocket.accept()
    seen: set[str] = set()
    try:
        await websocket.send_json({"type": "connected", "task_id": SYSTEM_TASK_ID})
        while True:
            for message in _notification_messages():
                if message.id in seen:
                    continue
                seen.add(message.id)
                await websocket.send_json(
                    {"type": "agent_message", "task_id": message.task_id, "message": message.to_openai_dict()}
                )
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        return


@ws_router.websocket("/ws/mobile/notifications")
@ws_router.websocket("/api/ws/mobile/notifications")
@ws_router.websocket("/ws/mobile/approvals")
@ws_router.websocket("/api/ws/mobile/approvals")
async def mobile_notifications(websocket: WebSocket, token: str = ""):
    from app.security.mobile_jwt import decode_mobile_token

    client_host = websocket.client.host if websocket.client else ""
    if not is_secure_mobile_transport(client_host, websocket.url.scheme):
        await websocket.close(code=1008)
        return
    try:
        claims = decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={TOKEN_SCOPE})
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = get_approval_event_bus().subscribe()
    seen: set[str] = set()
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
            await _send_guardian_pending_mobile_approvals(websocket, claims, seen)
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
            except TimeoutError:
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                await websocket.send_json({"type": "heartbeat"})
                continue
            if not _guardian_mobile_event_allowed(event, claims):
                continue
            if event.get("type") == "mobile_device_revoked":
                await websocket.send_json(event)
                await websocket.close(code=1008)
                return
            if await _close_if_mobile_claims_inactive(websocket, claims):
                return
            if event.get("type") in {"approval_created", "approval_decided"}:
                approval = event.get("approval")
                if isinstance(approval, dict):
                    seen.add(str(approval.get("id") or ""))
                payload_type = (
                    "approval_notification" if event.get("type") == "approval_created" else "approval_decided"
                )
                safe_approval = (
                    mobile_pairing_service.safe_approval_payload(approval, claims) if isinstance(approval, dict) else {}
                )
                await websocket.send_json({"type": payload_type, "approval": safe_approval})
            else:
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        get_approval_event_bus().unsubscribe(queue)


async def _close_if_mobile_claims_inactive(websocket: WebSocket, claims: dict[str, Any]) -> bool:
    try:
        validate_mobile_claims_active(claims)
    except HTTPException:
        await websocket.close(code=1008)
        return True
    return False


async def _send_guardian_pending_mobile_approvals(websocket: WebSocket, claims: dict[str, Any], seen: set[str]) -> None:
    for approval in mobile_pairing_service.list_pending_approvals(claims):
        approval_id = str(approval.get("id") or "")
        if not approval_id or approval_id in seen:
            continue
        seen.add(approval_id)
        await websocket.send_json({"type": "approval_notification", "approval": approval})


def _guardian_mobile_event_allowed(event: dict[str, Any], claims: dict[str, Any]) -> bool:
    if event.get("type") in {"remote_input_grant_created", "remote_input_grant_revoked", "mobile_device_revoked"}:
        return str(event.get("device_id") or "") == str(claims.get("device_id") or "")
    approval = event.get("approval")
    if not isinstance(approval, dict):
        return True
    return mobile_pairing_service.mobile_claims_can_access_approval(approval, claims)


@ws_router.websocket("/{path:path}")
async def proxy_websocket(websocket: WebSocket, path: str):
    if _is_mobile_ws_path(websocket.url.path):
        await websocket.close(code=1008)
        return
    if await close_unauthorized_desktop_websocket(websocket):
        return
    if runtime.shell_mode != "foreground":
        await websocket.accept()
        await websocket.close(code=1013)
        return
    await websocket.accept()
    try:
        await runtime.ensure_full_backend(reason=f"ws_proxy:/{path}")
        target = _full_backend_ws_url(path, websocket.url.query)
        # Authenticate the upstream loopback hop the same way the HTTP proxy does
        # (desktop_api_token_headers). Without this, the full backend's WS token
        # guard rejects the proxied connection whenever the desktop token is
        # required, breaking LAN/mobile WS proxying.
        token_headers = desktop_api_token_headers()
        upstream_token = token_headers.get(DESKTOP_API_TOKEN_HEADER, "")
        connect_kwargs: dict[str, Any] = {}
        if token_headers:
            connect_kwargs["additional_headers"] = token_headers
        if upstream_token:
            connect_kwargs["subprotocols"] = [f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{upstream_token}"]
        async with websockets.connect(target, **connect_kwargs) as upstream:
            await asyncio.gather(
                _client_to_upstream(websocket, upstream),
                _upstream_to_client(websocket, upstream),
            )
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001 - websocket proxy errors are reported by closing the client socket.
        with suppress(RuntimeError):
            await websocket.close(code=1011)


@proxy_router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_full_backend(path: str, request: Request) -> Response:
    if _is_mobile_or_remote_proxy_path(path):
        raise HTTPException(
            status_code=404, detail="Mobile and remote routes are handled by Guardian and are not proxied."
        )
    raw_body = await request.body()
    response = await runtime.proxy(
        request.method,
        f"/{path}",
        query=request.url.query.encode("utf-8"),
        headers=dict(request.headers),
        body=raw_body,
    )
    return _proxy_response(response)


async def _wake_full_backend_for_approval(approval: Approval) -> Approval | None:
    try:
        await runtime.wake_transient(reason=f"approval:{approval.id}")
    except Exception as exc:  # noqa: BLE001 - guardian approval should surface wake failures clearly.
        raise HTTPException(
            status_code=503,
            detail=_approval_continue_unavailable_detail(approval, exc),
        ) from exc
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{full_backend_url()}/api/runtime/approvals/{approval.id}/continue",
                headers=desktop_api_token_headers(),
            )
    except Exception as exc:  # noqa: BLE001 - transport failures should be retryable approval errors.
        raise HTTPException(
            status_code=503,
            detail=_approval_continue_unavailable_detail(approval, exc),
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code, detail=_approval_continue_response_error_detail(approval, response)
        )
    refreshed = db.fetch_one("approvals", approval.id)
    return Approval.model_validate(refreshed) if refreshed else approval


def _approval_continue_unavailable_detail(approval: Approval, exc: Exception) -> dict[str, Any]:
    data = db.fetch_one("approvals", approval.id)
    return {
        "message": "Full backend is not ready to continue the approval.",
        "approval_id": approval.id,
        "approval": mobile_pairing_service.safe_approval_payload(data or approval),
        "error": redact_value(str(exc)),
    }


def _approval_continue_response_error_detail(approval: Approval, response: Any) -> dict[str, Any]:
    detail = _response_detail(response, include_text=False)
    message = "Full backend did not continue the approval."
    if isinstance(detail, dict):
        message = str(detail.get("message") or message)
    elif isinstance(detail, str) and detail:
        message = detail
    data = db.fetch_one("approvals", approval.id)
    payload: dict[str, Any] = {
        "message": redact_value(message),
        "approval_id": approval.id,
        "approval": mobile_pairing_service.safe_approval_payload(data or approval),
    }
    if isinstance(detail, dict):
        payload["backend_detail"] = _safe_backend_detail(detail)
    return payload


def _safe_backend_detail(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key == "approval" and isinstance(item, dict):
                result[text_key] = _safe_backend_approval_payload(item)
            elif text_key == "approvals" and isinstance(item, list):
                result[text_key] = [
                    _safe_backend_approval_payload(approval) if isinstance(approval, dict) else redact_value(approval)
                    for approval in item
                ]
            else:
                result[text_key] = _safe_backend_detail_value(text_key, item)
        return result
    if isinstance(value, list):
        return [_safe_backend_detail(item) for item in value]
    return redact_value(value)


def _safe_backend_detail_value(key: str, value: Any) -> Any:
    if isinstance(value, dict | list | tuple | set):
        return _safe_backend_detail(value)
    redacted = redact_value({key: value})
    return redacted.get(key) if isinstance(redacted, dict) else redact_value(value)


def _safe_backend_approval_payload(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {key: item for key, item in value.items() if key in Approval.model_fields}
    return mobile_pairing_service.safe_approval_payload(allowed)


async def _execute_wakeup(wakeup: Wakeup) -> None:
    await runtime.wake_transient(reason=f"wakeup:{wakeup.id}")
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{full_backend_url()}/api/runs",
                headers=desktop_api_token_headers(),
                json=RunCreateRequest(message=wakeup.goal, mode=wakeup.mode).model_dump(mode="json"),
            )
        if response.status_code >= 400:
            wakeup_service.complete_wakeup(wakeup, error=response.text)
            return
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            wakeup_service.complete_wakeup(wakeup, error=f"Invalid run creation response: {exc}")
            return
        run_id = str(data.get("run_id") or "").strip() if isinstance(data, dict) else ""
        if not run_id:
            wakeup_service.complete_wakeup(wakeup, error="Full backend did not return a run_id for the wakeup.")
            return
        wakeup_service.complete_wakeup(wakeup, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        wakeup_service.complete_wakeup(wakeup, error=str(exc))


def full_backend_url() -> str:
    from app.services.guardian_runtime import FULL_BACKEND_URL

    return FULL_BACKEND_URL


def _response_detail(response: Any, *, include_text: bool = True) -> Any:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - backend error bodies are best-effort JSON.
        return response.text if include_text else ""
    if isinstance(payload, dict) and "detail" in payload:
        return payload["detail"]
    return payload


def _full_backend_ws_url(path: str, query: str) -> str:
    base = full_backend_url().replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    suffix = "/" + path.lstrip("/")
    if query:
        suffix += f"?{query}"
    return f"{base}{suffix}"


def _is_mobile_ws_path(path: str) -> bool:
    return is_mobile_token_websocket_path(path)


def _is_mobile_or_remote_proxy_path(path: str) -> bool:
    normalized = "/" + path.lstrip("/")
    return (
        normalized == "/api/mobile"
        or normalized.startswith("/api/mobile/")
        or normalized == "/ws/mobile"
        or normalized.startswith("/ws/mobile/")
        or normalized == "/api/ws/mobile"
        or normalized.startswith("/api/ws/mobile/")
        or normalized == "/ws/remote"
        or normalized.startswith("/ws/remote/")
        or normalized == "/api/ws/remote"
        or normalized.startswith("/api/ws/remote/")
    )


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
            except Exception:  # noqa: BLE001, S112 - malformed notification rows are skipped.
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
