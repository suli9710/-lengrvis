from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.llm.registry import get_effective_settings
from app.orchestration.state_machine import safe_transition
from app.orchestration.step_phase import set_step_status
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    redacted_preview,
    settings_fingerprint,
)
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import PolicyEngine
from app.policy.redaction import redact_public_text
from app.policy.risk import RiskLevel, SafetyVerdict
from app.security.mobile_jwt import (
    REMOTE_INPUT_SCOPE,
    REMOTE_VIEW_SCOPE,
    decode_mobile_token,
    mobile_token_from_websocket,
    validate_mobile_claims_active,
)
from app.security.lan import is_secure_mobile_transport
from app.services.approval_event_service import publish_approval_created
from app.services import mobile_pairing_service
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
_FRAME_ACK_MAX_WAIT_SECONDS = 1.5
_FRAME_ACK_MIN_WAIT_SECONDS = 1.1
_FRAME_ACK_POLL_SECONDS = 0.2
_INPUT_ACTIVE_POLL_SECONDS = 0.2
_REMOTE_SCREEN_CONTROL_ERROR_CODE = "remote_screen.invalid_control"
_REMOTE_SCREEN_CAPTURE_ERROR_CODE = "remote_screen.capture_failed"
_REMOTE_INPUT_DENIED_ERROR_CODE = "remote_input.denied"
_REMOTE_INPUT_REJECTED_ERROR_CODE = "remote_input.rejected"
_REMOTE_INPUT_UNEXPECTED_ERROR_CODE = "remote_input.failed"
_REMOTE_REVIEW_REASON_AUDIT_LIMIT = 3
_REMOTE_ERROR_SELECTOR_PATTERN = re.compile(
    r"(?i)\b(selector|locator)\s*[:=]\s*['\"]?([^\s,'\"<>]+)"
)
_REMOTE_ERROR_HOST_PATTERN = re.compile(
    r"(?i)\b(host|hostname)\s*[:=]\s*['\"]?([^\s,'\"<>]+)"
)
_REMOTE_ERROR_HOSTNAME_VALUE_PATTERN = re.compile(
    r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:internal|local|lan|corp|home|test|example|com|net|org)\b"
)
_REMOTE_ERROR_STACK_PATTERN = re.compile(
    r"(?is)(traceback \(most recent call last\):|file\s+\"[^\"]+\",\s+line\s+\d+,\s+in\s+[^\n\r]+)"
)
_REMOTE_ERROR_TOKEN_HINT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization|cookie)\s*[:=]\s*['\"]?[^\s,'\"<>]{4,}"
    r"|\bBearer\s+[^\s,'\"<>]{4,}\b"
    r"|\bsk-[A-Za-z0-9_\-]{8,}\b"
)


@ws_router.websocket("/ws/remote/screen")
async def remote_screen_stream(websocket: WebSocket, token: str = ""):
    claims = await _authorize_remote_websocket(websocket, token)
    if claims is None:
        return

    await websocket.accept()
    fps = DEFAULT_FPS
    quality = DEFAULT_JPEG_QUALITY
    frame_sequence = 0
    record("remote.screen.connected", _REMOTE_ACTOR, _claim_payload(claims))
    try:
        await websocket.send_json({"type": "connected", "fps": fps, "quality": quality})
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.01)
                fps, quality = _apply_stream_controls(message, fps=fps, quality=quality)
            except asyncio.TimeoutError:
                message = None
            except WebSocketDisconnect:
                break
            except Exception:
                await websocket.send_json(
                    _remote_client_error(
                        code=_REMOTE_SCREEN_CONTROL_ERROR_CODE,
                        message="Invalid screen stream control message.",
                    )
                )

            if not _remote_session_still_active(claims):
                await websocket.close(code=1008, reason="Remote desktop session is no longer active.")
                break

            try:
                frame_sequence += 1
                frame = await asyncio.to_thread(
                    capture_screen_frame,
                    max_width=DEFAULT_CAPTURE_WIDTH,
                    max_height=DEFAULT_CAPTURE_HEIGHT,
                    quality=quality,
                )
                await websocket.send_json(
                    {
                        "type": "frame",
                        "sequence": frame_sequence,
                        "image": f"data:image/jpeg;base64,{frame.image_base64}",
                        "timestamp": frame.timestamp,
                        "width": frame.width,
                        "height": frame.height,
                        "original_width": frame.original_width,
                        "original_height": frame.original_height,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                _record_remote_exception(
                    "remote.screen.capture_failed",
                    exc,
                    claims,
                    code=_REMOTE_SCREEN_CAPTURE_ERROR_CODE,
                    sequence=frame_sequence,
                )
                await websocket.send_json(
                    _remote_client_error(
                        code=_REMOTE_SCREEN_CAPTURE_ERROR_CODE,
                        message="Remote screen is temporarily unavailable.",
                    )
                )
                await asyncio.sleep(frame_interval_seconds(fps))
                continue

            sent_at = asyncio.get_running_loop().time()
            try:
                fps, quality, device_active = await _wait_for_frame_ack_or_timeout(
                    websocket,
                    sequence=frame_sequence,
                    fps=fps,
                    quality=quality,
                    is_device_active=lambda: _remote_session_still_active(claims),
                )
            except WebSocketDisconnect:
                break
            if not device_active:
                await websocket.close(code=1008, reason="Remote desktop session is no longer active.")
                break

            remaining_delay = frame_interval_seconds(fps) - (asyncio.get_running_loop().time() - sent_at)
            if remaining_delay > 0:
                await asyncio.sleep(remaining_delay)
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
                event = await asyncio.wait_for(websocket.receive_json(), timeout=_INPUT_ACTIVE_POLL_SECONDS)
            except asyncio.TimeoutError:
                if not _remote_session_still_active(claims):
                    await websocket.close(code=1008, reason="Remote desktop session is no longer active.")
                    break
                continue
            except WebSocketDisconnect:
                break
            if not _remote_session_still_active(claims):
                await websocket.close(code=1008, reason="Remote desktop session is no longer active.")
                break
            try:
                result = handle_remote_input_event(event, claims=claims)
            except HTTPException as exc:
                _record_remote_exception(
                    "remote.input.rejected",
                    exc,
                    claims,
                    code=_REMOTE_INPUT_REJECTED_ERROR_CODE,
                    status_code=exc.status_code,
                )
                result = _remote_client_error(
                    code=_REMOTE_INPUT_REJECTED_ERROR_CODE,
                    message="Remote input event was rejected.",
                    status_code=exc.status_code,
                )
            except Exception as exc:  # noqa: BLE001
                _record_remote_exception(
                    "remote.input.failed",
                    exc,
                    claims,
                    code=_REMOTE_INPUT_UNEXPECTED_ERROR_CODE,
                    status_code=500,
                )
                result = _remote_client_error(
                    code=_REMOTE_INPUT_UNEXPECTED_ERROR_CODE,
                    message="Remote input event could not be handled.",
                    status_code=500,
                )
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
        record(
            "remote.input.denied",
            _REMOTE_ACTOR,
            {**payload, **_remote_denied_audit_metadata(review)},
            task_id=task.id,
        )
        return _remote_input_denied_client_payload(task.id)

    registry = register_all_tools(settings=settings)
    tool = registry.get(tool_name)
    preview = tool.execute({**args, "dry_run": True}, {"settings": settings, "allowed_directories": settings.allowed_directories})
    safe_preview = binding_preview(preview)
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        approval_type="remote_input",
        message=review.user_confirmation_message or f"Approve remote desktop input {tool_name}?",
        diff_preview=safe_preview,
        tool_name=tool_name,
        risk_level=tool.risk_level.value,
        args_binding_hmac=args_binding_hmac(tool_name, args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(safe_preview),
        settings_fingerprint=settings_fingerprint(settings, allowed_directories=settings.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version=getattr(tool, "tool_version", "1"),
        source="remote_input",
        source_device_id=str((claims or {}).get("device_id") or ""),
        source_grant_id=str((claims or {}).get("grant_id") or ""),
        allowed_device_ids=[str((claims or {}).get("device_id") or "")] if (claims or {}).get("device_id") else [],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)
    publish_approval_created(approval)
    set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor=_REMOTE_ACTOR)
    safe_transition(task, TaskStatus.WAITING_USER_APPROVAL, actor=_REMOTE_ACTOR)
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
        "preview": redacted_preview(safe_preview),
    }


async def _authorize_remote_websocket(websocket: WebSocket, token: str) -> dict[str, Any] | None:
    client_host = websocket.client.host if websocket.client else ""
    if not is_secure_mobile_transport(client_host, websocket.url.scheme):
        await websocket.close(code=1008, reason="Remote mobile WebSockets require WSS unless the client is on this computer.")
        return None
    if not get_effective_settings().remote_desktop_enabled:
        await websocket.close(code=1008, reason="Remote desktop is disabled.")
        return None
    try:
        required_scope = REMOTE_INPUT_SCOPE if websocket.url.path.endswith("/input") else REMOTE_VIEW_SCOPE
        return decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={required_scope})
    except HTTPException:
        await websocket.close(code=1008, reason="Unauthorized.")
        return None


def _apply_stream_controls(message: Any, *, fps: float, quality: int) -> tuple[float, int]:
    if not isinstance(message, dict):
        return fps, quality
    next_fps = normalize_fps(message.get("fps")) if "fps" in message else fps
    next_quality = normalize_quality(message.get("quality")) if "quality" in message else quality
    return next_fps, next_quality


async def _wait_for_frame_ack_or_timeout(
    websocket: WebSocket,
    *,
    sequence: int,
    fps: float,
    quality: int,
    is_device_active: Any,
) -> tuple[float, int, bool]:
    deadline = asyncio.get_running_loop().time() + min(
        _FRAME_ACK_MAX_WAIT_SECONDS,
        max(_FRAME_ACK_MIN_WAIT_SECONDS, frame_interval_seconds(fps) * 2),
    )
    while True:
        if not is_device_active():
            return fps, quality, False

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return fps, quality, True

        try:
            message = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=min(_FRAME_ACK_POLL_SECONDS, remaining),
            )
        except asyncio.TimeoutError:
            continue
        except WebSocketDisconnect:
            raise
        except Exception:
            await websocket.send_json(
                _remote_client_error(
                    code=_REMOTE_SCREEN_CONTROL_ERROR_CODE,
                    message="Invalid screen stream control message.",
                )
            )
            continue

        fps, quality = _apply_stream_controls(message, fps=fps, quality=quality)
        if _is_frame_ack(message, sequence):
            return fps, quality, True


def _is_frame_ack(message: Any, sequence: int) -> bool:
    if not isinstance(message, dict) or message.get("type") != "frame_ack":
        return False
    try:
        return int(message.get("sequence")) == sequence
    except (TypeError, ValueError):
        return False


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


def _remote_client_error(*, code: str, message: str, status_code: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if status_code is not None:
        payload["status_code"] = status_code
    return payload


def _remote_input_denied_client_payload(task_id: str) -> dict[str, Any]:
    return {
        "type": "denied",
        "code": _REMOTE_INPUT_DENIED_ERROR_CODE,
        "message": "Remote input event was denied by policy.",
        "status_code": 403,
        "task_id": task_id,
    }


def _remote_denied_audit_metadata(review: Any) -> dict[str, Any]:
    reasons = [str(reason or "") for reason in (getattr(review, "reasons", None) or [])]
    return {
        "code": _REMOTE_INPUT_DENIED_ERROR_CODE,
        "status_code": 403,
        "verdict": str(getattr(getattr(review, "verdict", ""), "value", getattr(review, "verdict", ""))),
        "risk_level": str(getattr(getattr(review, "risk_level", ""), "value", getattr(review, "risk_level", ""))),
        "target_type": str(getattr(review, "target_type", "") or ""),
        "reason_count": len(reasons),
        "required_change_count": len(getattr(review, "required_changes", None) or []),
        "safe_alternative_present": bool(getattr(review, "safe_alternative", "") or ""),
        "reason_summaries": [_remote_review_reason_summary(reason) for reason in reasons[:_REMOTE_REVIEW_REASON_AUDIT_LIMIT]],
    }


def _remote_review_reason_summary(reason: str) -> dict[str, Any]:
    redacted = _redact_remote_diagnostic_text(reason)
    return {
        "summary": "Policy denied remote input; reason details redacted.",
        "sensitive_categories": _remote_sensitive_diagnostic_categories(reason, redacted),
        "digest": hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12],
    }


def _remote_sensitive_diagnostic_categories(raw_text: str, redacted_text: str) -> list[str]:
    categories: set[str] = set()
    if "[REDACTED_LOCAL_PATH]" in redacted_text:
        categories.add("local_path")
    if _REMOTE_ERROR_TOKEN_HINT_PATTERN.search(raw_text):
        categories.add("secret_or_token")
    if _REMOTE_ERROR_SELECTOR_PATTERN.search(raw_text):
        categories.add("selector")
    if _REMOTE_ERROR_HOST_PATTERN.search(raw_text) or _REMOTE_ERROR_HOSTNAME_VALUE_PATTERN.search(raw_text):
        categories.add("hostname")
    if "[REDACTED_STACK]" in redacted_text:
        categories.add("stack_trace")
    return sorted(categories)


def _record_remote_exception(
    event_type: str,
    exc: BaseException,
    claims: dict[str, Any],
    **payload: Any,
) -> None:
    record(
        event_type,
        _REMOTE_ACTOR,
        {
            **_claim_payload(claims),
            **payload,
            "error_type": exc.__class__.__name__,
            "error": _redacted_remote_exception(exc),
        },
    )


def _redacted_remote_exception(exc: BaseException) -> str:
    return _redact_remote_diagnostic_text(str(exc) or exc.__class__.__name__)


def _redact_remote_diagnostic_text(text: str) -> str:
    text = redact_public_text(text)
    text = _REMOTE_ERROR_STACK_PATTERN.sub("[REDACTED_STACK]", text)
    text = _REMOTE_ERROR_HOST_PATTERN.sub(r"\1=[REDACTED]", text)
    return _REMOTE_ERROR_SELECTOR_PATTERN.sub(r"\1=[REDACTED]", text)


def _claim_payload(claims: dict[str, Any]) -> dict[str, Any]:
    device_ref = _redacted_remote_identifier("DEVICE_ID", claims.get("device_id"))
    subject = str(claims.get("sub") or "")
    subject_ref = (
        f"mobile:{device_ref}"
        if device_ref and subject.startswith("mobile:")
        else _redacted_remote_identifier("SUBJECT", subject)
    )
    return {
        "device_id": device_ref,
        "device_name": _redacted_remote_identifier("DEVICE_NAME", claims.get("device_name")),
        "grant_id": _redacted_remote_identifier("GRANT_ID", claims.get("grant_id")),
        "subject": subject_ref,
    }


def _redacted_remote_identifier(label: str, value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED_{label}:{digest}]"


def _claims_still_active(claims: dict[str, Any]) -> bool:
    try:
        validate_mobile_claims_active(claims)
    except HTTPException:
        return False
    return True


def _remote_session_still_active(claims: dict[str, Any]) -> bool:
    return get_effective_settings().remote_desktop_enabled and _claims_still_active(claims)
