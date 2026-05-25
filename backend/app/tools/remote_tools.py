from __future__ import annotations

import ctypes
import sys
from typing import Any

from app.core.audit import record
from app.llm.registry import get_effective_settings
from app.policy.risk import RiskLevel
from app.services.remote_desktop_service import capture_screen
from app.tools.schemas import ToolDefinition


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
    if not _has_approval(args):
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
    if not _has_approval(args):
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
    if not _has_approval(args):
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
    return bool(getattr(settings, "remote_desktop_enabled", False))


def _has_approval(args: dict[str, Any]) -> bool:
    return bool(args.get("approved") and args.get("approval_id"))


def _approval_error(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"Remote desktop {action} requires an approved approval_id after dry-run preview.",
    }


def _click_at(x: int, y: int) -> None:
    try:
        import pyautogui

        pyautogui.click(x=x, y=y)
        return
    except ImportError:
        pass

    if sys.platform != "win32":
        raise RuntimeError("Remote click requires pyautogui outside Windows.")
    ctypes.windll.user32.SetCursorPos(x, y)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def _type_text(text: str) -> None:
    try:
        import pyautogui

        pyautogui.write(text)
        return
    except ImportError:
        pass

    if sys.platform != "win32":
        raise RuntimeError("Remote typing requires optional dependency pyautogui outside Windows.")
    for character in text:
        _send_unicode_character(character)


def _press_key(key: str) -> None:
    try:
        import pyautogui

        pyautogui.press(key)
        return
    except ImportError:
        pass

    if sys.platform != "win32":
        raise RuntimeError("Remote key press requires optional dependency pyautogui outside Windows.")
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
        ("remote.view_screen", view_screen, RiskLevel.R1_OPEN_ONLY, False),
        ("remote.click", click, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
        ("remote.type_text", type_text, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
        ("remote.key_press", key_press, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True),
    ]
    for name, fn, risk, supports_dry_run in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
                output_schema={},
                risk_level=risk,
                agent_owner="ComputerAgent",
                supports_dry_run=supports_dry_run,
                requires_authorized_path=False,
                execute=fn,
            )
        )
