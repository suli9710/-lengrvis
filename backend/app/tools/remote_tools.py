from __future__ import annotations

import ctypes
import sys
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.audit import record
from app.llm.registry import get_effective_settings
from app.policy.approval_binding import args_binding_hmac
from app.policy.risk import RiskLevel
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE
from app.services.remote_desktop_service import capture_screen
from app.tools.schemas import ToolDefinition
from app.tools.tool_catalog import tool_description, tool_search_hint


_REMOTE_ACTOR = "RemoteDesktop"
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_VK_CODES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "delete": 0x2E,
}


def view_screen(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    if not _remote_enabled(context):
        return {"ok": False, "error": "Remote desktop is disabled."}
    quality = int(args.get("quality") or 50)
    image = capture_screen(quality=quality)
    record("remote.view_screen", _REMOTE_ACTOR, {"quality": quality})
    return {"ok": True, "image": f"data:image/jpeg;base64,{image}", "mime_type": "image/jpeg"}


def click(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    if not _remote_enabled(context):
        return {"ok": False, "error": "Remote desktop is disabled."}
    x = int(args.get("x") or 0)
    y = int(args.get("y") or 0)
    if args.get("dry_run", True):
        return _preview("click", {"x": x, "y": y})
    if not _has_approval(args, "remote.click"):
        return _approval_error("click")
    _click_at(x, y)
    record("remote.click", _REMOTE_ACTOR, {"x": x, "y": y})
    return {"ok": True, "clicked": {"x": x, "y": y}}


def type_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    if not _remote_enabled(context):
        return {"ok": False, "error": "Remote desktop is disabled."}
    text = str(args.get("text") or "")
    if args.get("dry_run", True):
        return _preview("type_text", {"characters": len(text)})
    if not _has_approval(args, "remote.type_text"):
        return _approval_error("type_text")
    _type_text(text)
    record("remote.type_text", _REMOTE_ACTOR, {"characters": len(text)})
    return {"ok": True, "characters": len(text)}


def key_press(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    if not _remote_enabled(context):
        return {"ok": False, "error": "Remote desktop is disabled."}
    key = _normalize_key(str(args.get("key") or ""))
    if not key:
        return {"ok": False, "error": "Key is required."}
    if args.get("dry_run", True):
        return _preview("key_press", {"key": key})
    if not _has_approval(args, "remote.key_press"):
        return _approval_error("key_press")
    _press_key(key)
    record("remote.key_press", _REMOTE_ACTOR, {"key": key})
    return {"ok": True, "key": key}


def _preview(action: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "dry_run": True,
        "message": "Remote desktop input preview. User approval is required before execution.",
        "diff_preview": [{"action": action, **detail}],
    }


def _remote_enabled(context: dict[str, Any]) -> bool:
    settings = context.get("settings") or get_effective_settings()
    if not bool(getattr(settings, "remote_desktop_enabled", False)):
        return False
    # P0-11: gate remote desktop control behind a paid entitlement so free-tier
    # users cannot invoke remote input even when the feature flag is enabled.
    # The entitlement is an optional context hint: when a caller resolves and
    # provides it we require a paid tier, but when it is absent we fall back to
    # the remote_desktop_enabled flag (plus the upstream mobile auth/grant
    # checks) as the control, instead of failing closed and disabling remote
    # input for every caller, since no tool context populates it yet.
    entitlement = str(context.get("user_entitlement") or "").strip().lower()
    if entitlement and entitlement not in ("pro", "team"):
        return False
    return True


def _has_approval(args: dict[str, Any], tool_name: str) -> bool:
    """Check that a valid, unconsumed approval exists for this remote input call.

    P0-4 fix: The original logic required consumed_at to be NON-empty,
    meaning only already-consumed approvals would pass — the exact opposite
    of anti-replay. Now we correctly require consumed_at to be EMPTY.
    """
    if args.get("approved") is not True:
        return False
    approval_id = str(args.get("approval_id") or "").strip()
    if not approval_id:
        return False
    approval = db.fetch_one("approvals", approval_id)
    if not approval:
        return False
    if str(approval.get("status") or "") != "approved":
        return False
    # P0-4 fix: consumed_at must be EMPTY (not yet consumed) to proceed.
    # The old code used `not bool(...)` which inverted the check.
    if bool(str(approval.get("consumed_at") or "").strip()):
        return False  # Already consumed — reject replay.
    return _approval_matches_remote_input(approval, tool_name, args) and _approval_remote_input_grant_active(approval)


def _approval_error(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Remote desktop {action} requires an approved approval_id after dry-run preview.",
    }


def _approval_matches_remote_input(approval: dict[str, Any], tool_name: str, args: dict[str, Any]) -> bool:
    if str(approval.get("approval_type") or "") != "remote_input" and str(approval.get("source") or "") != "remote_input":
        return False
    if str(approval.get("tool_name") or "") != tool_name:
        return False
    task_id = str(approval.get("task_id") or "")
    step_id = str(approval.get("step_id") or "")
    expected_hmac = str(approval.get("args_binding_hmac") or "")
    if not task_id or not step_id or not expected_hmac:
        return False
    return expected_hmac == args_binding_hmac(tool_name, _approval_bound_args(tool_name, args), task_id=task_id, step_id=step_id)


def _approval_bound_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "remote.click":
        return {"x": int(args.get("x") or 0), "y": int(args.get("y") or 0)}
    if tool_name == "remote.type_text":
        return {"text": str(args.get("text") or "")}
    if tool_name == "remote.key_press":
        return {"key": str(args.get("key") or "")}
    return {}


def _approval_remote_input_grant_active(approval: dict[str, Any]) -> bool:
    device_id = str(approval.get("source_device_id") or "").strip()
    grant_id = str(approval.get("source_grant_id") or "").strip()
    if not device_id or not grant_id:
        return False
    if REMOTE_INPUT_SCOPE not in _text_list(approval.get("required_mobile_scopes")):
        return False
    device = db.fetch_one("mobile_devices", device_id)
    if not isinstance(device, dict) or str(device.get("status") or "active").strip().lower() != "active":
        return False
    grants = device.get("remote_input_grants") or []
    if not isinstance(grants, list):
        return False
    now = datetime.now(timezone.utc)
    for grant in grants:
        if isinstance(grant, dict) and _remote_input_grant_active(grant, grant_id, now):
            return True
    return False


def _remote_input_grant_active(grant: dict[str, Any], expected_grant_id: str, now: datetime) -> bool:
    if str(grant.get("id") or "").strip() != expected_grant_id:
        return False
    if str(grant.get("scope") or REMOTE_INPUT_SCOPE).strip().lower() != REMOTE_INPUT_SCOPE:
        return False
    if str(grant.get("status") or "").strip().lower() != "active":
        return False
    if str(grant.get("revoked_at") or "").strip():
        return False
    expires_at = _parse_remote_input_grant_expiry(grant.get("expires_at"))
    return expires_at is not None and expires_at > now


def _parse_remote_input_grant_expiry(value: Any) -> datetime | None:
    try:
        expires_at = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)


def _text_list(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item.strip() for item in value.replace(",", " ").split() if item.strip()}
    if isinstance(value, (list, tuple, set)):
        return {str(item or "").strip() for item in value if str(item or "").strip()}
    return set()


def _click_at(x: int, y: int) -> None:
    try:
        import pyautogui

        pyautogui.click(x=x, y=y)
        return
    except ImportError:
        if sys.platform != "win32":
            raise RuntimeError("Remote click requires pyautogui outside Windows.") from None
    ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def _type_text(text: str) -> None:
    try:
        import pyautogui

        pyautogui.write(text)
        return
    except ImportError:
        if sys.platform != "win32":
            raise RuntimeError("Remote typing requires optional dependency pyautogui outside Windows.") from None
    for character in text:
        _send_unicode_character(character)


def _press_key(key: str) -> None:
    try:
        import pyautogui

        pyautogui.press(key)
        return
    except ImportError:
        if sys.platform != "win32":
            raise RuntimeError("Remote key press requires optional dependency pyautogui outside Windows.") from None
    vk_code = _VK_CODES.get(key)
    if vk_code is None:
        if len(key) == 1:
            _send_unicode_character(key)
            return
        raise RuntimeError(f"Unsupported key without pyautogui: {key}")
    _send_virtual_key(vk_code)


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


def _send_keyboard_input(vk_code: int, scan_code: int, flags: int) -> None:
    extra = ctypes.c_ulong(0)
    event = _Input(
        type=_INPUT_KEYBOARD,
        union=_InputUnion(
            ki=_KeyboardInput(
                wVk=vk_code,
                wScan=scan_code,
                dwFlags=flags,
                time=0,
                dwExtraInfo=ctypes.pointer(extra),
            )
        ),
    )
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
    if sent != 1:
        raise RuntimeError("Windows SendInput failed.")


def _send_unicode_character(character: str) -> None:
    codepoint = ord(character)
    _send_keyboard_input(0, codepoint, _KEYEVENTF_UNICODE)
    _send_keyboard_input(0, codepoint, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)


def _send_virtual_key(vk_code: int) -> None:
    _send_keyboard_input(vk_code, 0, 0)
    _send_keyboard_input(vk_code, 0, _KEYEVENTF_KEYUP)


def _normalize_key(key: str) -> str:
    aliases = {
        "esc": "escape",
        "escape": "escape",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
    }
    normalized = key.strip().lower()
    return aliases.get(normalized, normalized)


def register(registry) -> None:
    defs = [
        (
            "remote.view_screen",
            view_screen,
            RiskLevel.R1_OPEN_ONLY,
            False,
            ["read"],
            ["remote_screen"],
            True,
        ),
        (
            "remote.click",
            click,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            ["click", "write"],
            ["remote_screen", "desktop_ui"],
            False,
        ),
        (
            "remote.type_text",
            type_text,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            ["type", "write"],
            ["remote_screen", "desktop_ui"],
            False,
        ),
        (
            "remote.key_press",
            key_press,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            ["key", "write"],
            ["remote_screen", "desktop_ui"],
            False,
        ),
    ]
    for name, fn, risk, supports_dry_run, effects, resource_kinds, read_only in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema={},
                output_schema={},
                risk_level=risk,
                agent_owner="ComputerAgent",
                supports_dry_run=supports_dry_run,
                requires_authorized_path=False,
                execute=fn,
                read_only=read_only,
                concurrency_safe=read_only,
                destructive=False,
                effects=effects,
                resource_kinds=resource_kinds,
                trust_tier="system",
            )
        )
