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
_REMOTE_WEBSOCKET_RETRY_CLOSE_CODE = 1012
_REMOTE_WEBSOCKET_AUTH_CLOSE_CODE = 4401
_REMOTE_WEBSOCKET_GRANT_CLOSE_CODE = 4403
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
_REMOTE_INPUT_PREVIEW_MESSAGE = "Remote desktop input preview. User approval is required before execution."
_REMOTE_INPUT_SAFE_KEY_PATTERN = re.compile(r"^[a-z0-9_.+\- ]{1,24}$", re.IGNORECASE)
_REMOTE_INPUT_SAFE_KEYS = {
    "backspace",
    "delete",
    "down",
    "end",
    "enter",
    "escape",
    "home",
    "left",
    "pagedown",
    "pageup",
    "right",
    "space",
    "tab",
    "up",
}


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

            if await _close_remote_websocket_if_inactive(websocket, claims):
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
                        "screen_origin_x": int(getattr(frame, "screen_origin_x", 0) or 0),
                        "screen_origin_y": int(getattr(frame, "screen_origin_y", 0) or 0),
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
                await _close_remote_websocket_if_inactive(websocket, claims)
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
                if await _close_remote_websocket_if_inactive(websocket, claims):
                    break
                continue
            except WebSocketDisconnect:
                break
            if await _close_remote_websocket_if_inactive(websocket, claims):
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
    if not bool(getattr(tool, "supports_dry_run", False)):
        raise HTTPException(status_code=409, detail="Remote input dry-run preview is unavailable.")
    preview = tool.execute({**args, "dry_run": True}, {"settings": settings, "allowed_directories": settings.allowed_directories})
    safe_preview = _remote_input_binding_preview(tool_name, preview)
    if not _remote_input_safe_preview_verified(safe_preview):
        raise HTTPException(status_code=409, detail="Remote input dry-run preview is unavailable.")
    resource_kinds = _remote_input_resource_kinds(tool)
    tool_effects = _remote_input_tool_effects(tool)
    tool_trust_tier = _remote_input_trust_tier(tool)
    dry_run_summary = _remote_input_dry_run_summary(safe_preview)
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
        tool_trust_tier=tool_trust_tier,
        tool_effects=tool_effects,
        resource_kinds=resource_kinds,
        dry_run_summary=dry_run_summary,
        engineering_boundary=_remote_input_approval_boundary_facts(
            tool,
            review,
            safe_preview,
            event_type=str(event.get("type") or ""),
            resource_kinds=resource_kinds,
            tool_effects=tool_effects,
            tool_trust_tier=tool_trust_tier,
            dry_run_summary=dry_run_summary,
        ),
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
        await websocket.close(code=_REMOTE_WEBSOCKET_RETRY_CLOSE_CODE, reason="Remote desktop is disabled.")
        return None
    try:
        required_scope = REMOTE_INPUT_SCOPE if websocket.url.path.endswith("/input") else REMOTE_VIEW_SCOPE
        return decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={required_scope})
    except HTTPException as exc:
        await websocket.close(code=_remote_websocket_close_code(exc), reason=_remote_websocket_close_reason(exc))
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
    if "key" in args:
        key = _safe_remote_input_key_label(args.get("key"))
        return {**args, "key": key or "***"}
    return dict(args)


def _remote_input_binding_preview(tool_name: str, preview: dict[str, Any]) -> dict[str, Any]:
    public_preview = {
        "ok": bool(preview.get("ok")),
        "dry_run": preview.get("dry_run") is True,
        "message": _REMOTE_INPUT_PREVIEW_MESSAGE,
        "diff_preview": _remote_input_safe_diff_preview(tool_name, preview.get("diff_preview")),
    }
    return binding_preview(public_preview)


def _remote_input_safe_preview_verified(safe_preview: dict[str, Any]) -> bool:
    return safe_preview.get("ok") is True and safe_preview.get("dry_run") is True


def _remote_input_safe_diff_preview(tool_name: str, value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    safe_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action = _remote_input_action(str(item.get("action") or ""), tool_name=tool_name)
        safe_item: dict[str, Any] = {"action": action}
        if action == "click":
            safe_item["x"] = _safe_int(item.get("x"))
            safe_item["y"] = _safe_int(item.get("y"))
        elif action == "type_text":
            safe_item["characters"] = _safe_nonnegative_int(item.get("characters"))
        elif action == "key_press":
            key = _safe_remote_input_key_label(item.get("key"))
            if key:
                safe_item["key"] = key
        safe_items.append(safe_item)
        if len(safe_items) >= 5:
            break
    if safe_items:
        return safe_items
    return [{"action": _remote_input_action("", tool_name=tool_name)}]


def _remote_input_resource_kinds(tool: Any) -> list[str]:
    return _unique_strings(_metadata_strings(getattr(tool, "resource_kinds", [])), ["remote_screen", "desktop_ui"])


def _remote_input_tool_effects(tool: Any) -> list[str]:
    effects = _metadata_strings(getattr(tool, "effects", []))
    if effects:
        return _unique_strings(effects)
    tool_name = str(getattr(tool, "name", "") or "")
    if tool_name == "remote.click":
        return ["click", "write"]
    if tool_name == "remote.type_text":
        return ["type", "write"]
    if tool_name == "remote.key_press":
        return ["key", "write"]
    return ["write"]


def _remote_input_trust_tier(tool: Any) -> str:
    return str(getattr(tool, "trust_tier", "") or "unknown").strip() or "unknown"


def _remote_input_dry_run_summary(safe_preview: dict[str, Any]) -> str:
    detail = _remote_input_first_preview_item(safe_preview)
    action = str(detail.get("action") or "")
    if action == "click":
        return f"Remote desktop dry-run: click screen position ({_safe_int(detail.get('x'))}, {_safe_int(detail.get('y'))})."
    if action == "type_text":
        return f"Remote desktop dry-run: type {_safe_nonnegative_int(detail.get('characters'))} character(s) into the focused control."
    if action == "key_press":
        key = _safe_remote_input_key_label(detail.get("key"))
        if key:
            return f"Remote desktop dry-run: press {key}."
        return "Remote desktop dry-run: press a keyboard key."
    return "Remote desktop dry-run: review the requested input action."


def _remote_input_approval_boundary_facts(
    tool: Any,
    review: Any,
    safe_preview: dict[str, Any],
    *,
    event_type: str,
    resource_kinds: list[str],
    tool_effects: list[str],
    tool_trust_tier: str,
    dry_run_summary: str,
) -> dict[str, Any]:
    return {
        "source": "remote_input",
        "remote_input": {
            "event_type": _remote_input_public_event_type(event_type),
            "requires_active_grant": True,
            "required_mobile_scopes": [REMOTE_INPUT_SCOPE],
            "device_binding": "active grant",
            "grant_binding": "active grant",
        },
        "tool": {
            "name": str(getattr(tool, "name", "") or ""),
            "risk_level": _remote_input_risk_label(getattr(tool, "risk_level", "")),
            "trust_tier": tool_trust_tier,
            "effects": list(tool_effects),
            "resource_kinds": list(resource_kinds),
            "read_only": bool(tool.is_read_only()) if hasattr(tool, "is_read_only") else False,
            "destructive": bool(getattr(tool, "destructive", False)),
            "supports_dry_run": bool(getattr(tool, "supports_dry_run", False)),
            "tool_version": str(getattr(tool, "tool_version", "1") or "1"),
        },
        "policy": {
            "verdict": str(getattr(getattr(review, "verdict", ""), "value", getattr(review, "verdict", ""))),
            "risk_level": _remote_input_risk_label(getattr(review, "risk_level", "")),
            "target_type": str(getattr(review, "target_type", "") or ""),
            "reason_count": len(getattr(review, "reasons", None) or []),
            "required_change_count": len(getattr(review, "required_changes", None) or []),
        },
        "binding": {
            "args_bound": True,
            "preview_bound": True,
            "settings_bound": True,
            "permission_policy_bound": True,
        },
        "dry_run": {
            "verified": safe_preview.get("dry_run") is True,
            "summary": dry_run_summary,
            "action": str(_remote_input_first_preview_item(safe_preview).get("action") or ""),
            "preview_keys": sorted(str(key) for key in safe_preview.keys() if not str(key).startswith("_"))[:20],
        },
    }


def _remote_input_first_preview_item(safe_preview: dict[str, Any]) -> dict[str, Any]:
    items = safe_preview.get("diff_preview")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return dict(items[0])
    return {}


def _remote_input_action(action: str, *, tool_name: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"click", "type_text", "key_press"}:
        return normalized
    return {
        "remote.click": "click",
        "remote.type_text": "type_text",
        "remote.key_press": "key_press",
    }.get(tool_name, "input")


def _remote_input_public_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"click", "type", "key"}:
        return normalized
    return "input"


def _remote_input_risk_label(value: Any) -> str:
    text = str(getattr(value, "value", value) or "").strip()
    return text.replace("_", " ").lower()


def _safe_remote_input_key_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return ""
    if key in _REMOTE_INPUT_SAFE_KEYS:
        return key
    if len(key) == 1 and key.isprintable() and not key.isspace():
        return key
    if _REMOTE_INPUT_SAFE_KEY_PATTERN.fullmatch(key) and not _REMOTE_ERROR_TOKEN_HINT_PATTERN.search(key):
        return key
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_nonnegative_int(value: Any) -> int:
    return max(0, _safe_int(value))


def _unique_strings(*groups: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
    return result


def _metadata_strings(value: Any) -> list[Any]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


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
    text = _REMOTE_ERROR_TOKEN_HINT_PATTERN.sub("[REDACTED_SECRET]", text)
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
    return _remote_session_close_state(claims)[0]


async def _close_remote_websocket_if_inactive(websocket: WebSocket, claims: dict[str, Any]) -> bool:
    active, code, reason = _remote_session_close_state(claims)
    if active:
        return False
    await websocket.close(code=code, reason=reason)
    return True


def _remote_session_close_state(claims: dict[str, Any]) -> tuple[bool, int, str]:
    if not get_effective_settings().remote_desktop_enabled:
        return False, _REMOTE_WEBSOCKET_RETRY_CLOSE_CODE, "Remote desktop is disabled."
    try:
        validate_mobile_claims_active(claims)
    except HTTPException as exc:
        return False, _remote_websocket_close_code(exc), _remote_websocket_close_reason(exc)
    return True, 1000, ""


def _remote_websocket_close_code(exc: HTTPException) -> int:
    detail = _remote_websocket_close_reason(exc).lower()
    if "remote input grant" in detail:
        return _REMOTE_WEBSOCKET_GRANT_CLOSE_CODE
    if exc.status_code == 403:
        return _REMOTE_WEBSOCKET_GRANT_CLOSE_CODE
    return _REMOTE_WEBSOCKET_AUTH_CLOSE_CODE


def _remote_websocket_close_reason(exc: HTTPException) -> str:
    detail = str(exc.detail or "").strip()
    return detail or "Unauthorized."
