from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.llm.registry import get_effective_settings
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.security.mobile_jwt import decode_mobile_token
from app.services.approval_event_service import publish_approval_created
from app.services.remote_desktop_service import (
    DEFAULT_CAPTURE_HEIGHT,
    DEFAULT_CAPTURE_WIDTH,
    DEFAULT_FPS,
    DEFAULT_JPEG_QUALITY,
    capture_screen_frame,
    frame_interval_seconds,
    normalize_fps,
    normalize_quality,
)
from app.tools.registry import register_all_tools


ws_router = APIRouter()

_REMOTE_ACTOR = "RemoteDesktop"


@ws_router.websocket("/ws/remote/screen")
async def remote_screen_stream(websocket: WebSocket, token: str = ""):
    claims = await _authorize_remote_websocket(websocket, token)
    if claims is None:
        return

    await websocket.accept()
    fps = DEFAULT_FPS
    quality = DEFAULT_JPEG_QUALITY
    record("remote.screen.connected", _REMOTE_ACTOR, _claim_payload(claims))
    try:
        await websocket.send_json({"type": "connected", "fps": fps, "quality": quality})
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.01)
                fps, quality = _apply_stream_controls(message, fps=fps, quality=quality)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            except Exception:
                await websocket.send_json({"type": "error", "message": "Invalid screen stream control message."})

            try:
                frame = await asyncio.to_thread(
                    capture_screen_frame,
                    max_width=DEFAULT_CAPTURE_WIDTH,
                    max_height=DEFAULT_CAPTURE_HEIGHT,
                    quality=quality,
                )
                await websocket.send_json(
                    {
                        "type": "frame",
                        "image": f"data:image/jpeg;base64,{frame.image_base64}",
                        "timestamp": frame.timestamp,
                        "width": frame.width,
                        "height": frame.height,
                        "original_width": frame.original_width,
                        "original_height": frame.original_height,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"type": "error", "message": str(exc)})
                await asyncio.sleep(frame_interval_seconds(fps))
                continue
            await asyncio.sleep(frame_interval_seconds(fps))
    finally:
        record("remote.screen.disconnected", _REMOTE_ACTOR, _claim_payload(claims))


@ws_router.websocket("/ws/remote/input")
async def remote_input_events(websocket: WebSocket, token: str = ""):
    claims = await _authorize_remote_websocket(websocket, token)
    if claims is None:
        return

    await websocket.accept()
    record("remote.input.connected", _REMOTE_ACTOR, _claim_payload(claims))
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            try:
                event = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            try:
                result = handle_remote_input_event(event, claims=claims)
            except HTTPException as exc:
                result = {"type": "error", "status_code": exc.status_code, "message": str(exc.detail)}
            except Exception as exc:  # noqa: BLE001
                result = {"type": "error", "status_code": 500, "message": str(exc)}
            await websocket.send_json(result)
    finally:
        record("remote.input.disconnected", _REMOTE_ACTOR, _claim_payload(claims))


def handle_remote_input_event(event: dict[str, Any], *, claims: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_effective_settings()
    if not settings.remote_desktop_enabled:
        raise HTTPException(status_code=403, detail="Remote desktop is disabled.")

    tool_name, args = _event_to_tool_call(event)
    payload = {
        "event_type": event.get("type"),
        "tool_name": tool_name,
        "args": _audit_args(args),
        **_claim_payload(claims or {}),
    }
    record("remote.input.received", _REMOTE_ACTOR, payload)

    task = Task(
        user_goal=f"Remote desktop input: {tool_name}",
        status=TaskStatus.REVIEWING_TOOL_CALL,
        mode=settings.mode,
    )
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="ComputerAgent",
        tool_name=tool_name,
        description=f"Remote desktop input event {event.get('type')}",
        args=args,
        expected_observation=f"{tool_name} completed.",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        requires_approval=True,
    )
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[step],
        global_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        requires_user_approval=True,
    )
    db.upsert_model("plans", plan)

    review = PolicyEngine(settings).review_tool_call(task.id, step.id, tool_name, args, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM)
    db.upsert_model("safety_reviews", review)
    if review.verdict == SafetyVerdict.DENY:
        record("remote.input.denied", _REMOTE_ACTOR, {**payload, "reasons": review.reasons}, task_id=task.id)
        return {"type": "denied", "task_id": task.id, "reasons": review.reasons}

    registry = register_all_tools(settings=settings)
    tool = registry.get(tool_name)
    preview = tool.execute({**args, "dry_run": True}, {"settings": settings, "allowed_directories": settings.allowed_directories})
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        approval_type="remote_input",
        message=review.user_confirmation_message or f"Approve remote desktop input {tool_name}?",
        diff_preview=preview,
    )
    db.upsert_model("approvals", approval)
    publish_approval_created(approval)
    step.status = StepStatus.WAITING_USER_APPROVAL
    task.status = TaskStatus.WAITING_USER_APPROVAL
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    record(
        "remote.input.approval_requested",
        _REMOTE_ACTOR,
        {**payload, "approval_id": approval.id},
        task_id=task.id,
    )
    return {
        "type": "approval_required",
        "task_id": task.id,
        "approval_id": approval.id,
        "review": review.model_dump(mode="json"),
        "preview": preview,
    }


async def _authorize_remote_websocket(websocket: WebSocket, token: str) -> dict[str, Any] | None:
    if not get_effective_settings().remote_desktop_enabled:
        await websocket.close(code=1008, reason="Remote desktop is disabled.")
        return None
    try:
        return decode_mobile_token(token)
    except HTTPException:
        await websocket.close(code=1008, reason="Unauthorized.")
        return None


def _apply_stream_controls(message: Any, *, fps: float, quality: int) -> tuple[float, int]:
    if not isinstance(message, dict):
        return fps, quality
    next_fps = normalize_fps(message.get("fps")) if "fps" in message else fps
    next_quality = normalize_quality(message.get("quality")) if "quality" in message else quality
    return next_fps, next_quality


def _event_to_tool_call(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    event_type = str(event.get("type") or "").strip().lower()
    if event_type == "click":
        return "remote.click", {"x": int(event.get("x") or 0), "y": int(event.get("y") or 0)}
    if event_type == "type":
        return "remote.type_text", {"text": str(event.get("text") or "")}
    if event_type == "key":
        return "remote.key_press", {"key": str(event.get("key") or "")}
    raise HTTPException(status_code=400, detail="Unsupported remote input event.")


def _audit_args(args: dict[str, Any]) -> dict[str, Any]:
    if "text" in args:
        return {**args, "text": "***", "characters": len(str(args.get("text") or ""))}
    return dict(args)


def _claim_payload(claims: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": claims.get("device_id"),
        "device_name": claims.get("device_name"),
        "subject": claims.get("sub"),
    }
