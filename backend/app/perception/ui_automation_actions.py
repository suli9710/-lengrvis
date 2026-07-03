from __future__ import annotations

import ctypes
import logging
import sys
import time
from typing import Any

from app.perception.app_context import get_current_app_context
from app.perception.schemas import Rect

logger = logging.getLogger(__name__)

__all__ = [
    "UIAutomationUnavailable",
    "bounded_int",
    "capture_screenshot",
    "focus_window",
    "list_windows",
    "normalize_key",
    "normalize_mouse_button",
    "press_key",
    "send_hotkey",
    "send_mouse_click",
    "send_mouse_drag",
    "send_text",
    "virtual_key_code",
]


class UIAutomationUnavailable(RuntimeError):
    """Raised when the local UIAutomation provider cannot operate."""


def send_mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> None:
    button = normalize_mouse_button(button)
    try:
        import pyautogui  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        if sys.platform != "win32":
            raise UIAutomationUnavailable(
                "pyautogui is required for coordinate click fallback outside Windows."
            ) from exc
        _ctypes_mouse_click(x, y, button, clicks)
        return
    pyautogui.click(x=x, y=y, button=button, clicks=clicks)


def send_mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float,
    button: str,
) -> None:
    button = normalize_mouse_button(button)
    try:
        import pyautogui  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        if sys.platform != "win32":
            raise UIAutomationUnavailable("pyautogui is required for mouse drag fallback outside Windows.") from exc
        _ctypes_mouse_drag(start_x, start_y, end_x, end_y, duration, button)
        return
    pyautogui.moveTo(start_x, start_y)
    pyautogui.dragTo(end_x, end_y, duration=duration, button=button)


def send_text(text: str) -> None:
    if sys.platform == "win32":
        for character in text:
            _send_unicode_character(character)
        return
    try:
        import pyautogui  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        raise UIAutomationUnavailable("pyautogui is required for text input fallback.") from exc
    pyautogui.write(text)


def press_key(key: str) -> None:
    key = normalize_key(key)
    try:
        import pyautogui  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        if sys.platform != "win32":
            raise UIAutomationUnavailable("pyautogui is required for key press fallback outside Windows.") from exc
        _ctypes_press_key(key)
        return
    pyautogui.press(key)


def send_hotkey(keys: list[str]) -> None:
    normalized = [normalize_key(key) for key in keys if normalize_key(key)]
    try:
        import pyautogui  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        if sys.platform != "win32":
            raise UIAutomationUnavailable("pyautogui is required for hotkey fallback outside Windows.") from exc
        _ctypes_hotkey(normalized)
        return
    pyautogui.hotkey(*normalized)


def capture_screenshot(max_width: int, max_height: int, quality: int) -> dict[str, Any]:
    from app.services.remote_desktop_service import capture_screen_frame

    frame = capture_screen_frame(max_width=max_width, max_height=max_height, quality=quality)
    return {
        "ok": True,
        "image": f"data:image/jpeg;base64,{frame.image_base64}",
        "mime_type": "image/jpeg",
        "timestamp": frame.timestamp,
        "width": frame.width,
        "height": frame.height,
        "original_width": frame.original_width,
        "original_height": frame.original_height,
        "quality": frame.quality,
        "app_context": get_current_app_context().model_dump(mode="json"),
    }


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_mouse_button(button: str) -> str:
    normalized = str(button or "left").strip().lower()
    if normalized not in {"left", "right", "middle"}:
        return "left"
    return normalized


_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "del": "delete",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "ctrl": "control",
    "ctl": "control",
    "cmd": "win",
    "windows": "win",
    "option": "alt",
}

_VK_CODES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
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
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
}
for _index in range(1, 13):
    _VK_CODES[f"f{_index}"] = 0x6F + _index


def normalize_key(key: str) -> str:
    normalized = str(key or "").strip().lower().replace(" ", "")
    return _KEY_ALIASES.get(normalized, normalized)


def virtual_key_code(key: str) -> int | None:
    key = normalize_key(key)
    if key in _VK_CODES:
        return _VK_CODES[key]
    if len(key) == 1 and "a" <= key <= "z":
        return ord(key.upper())
    if len(key) == 1 and "0" <= key <= "9":
        return ord(key)
    return None


def list_windows() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    windows: list[dict[str, Any]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if hasattr(user32, "IsWindowVisible") and not user32.IsWindowVisible(hwnd):
                return True
            title = _window_title(user32, hwnd)
            class_name = _window_class_name(user32, hwnd)
            process_id = _window_process_id(user32, hwnd)
            rect = _window_rect(user32, hwnd)
            if title or class_name:
                windows.append(
                    {
                        "hwnd": int(hwnd),
                        "title": title,
                        "class_name": class_name,
                        "process_id": process_id,
                        "rect": rect.model_dump() if rect else None,
                    }
                )
        except Exception:  # noqa: BLE001 - broad-exception-boundary: exceptions must not cross a ctypes EnumWindows callback.
            return True
        return True

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(callback)
    user32.EnumWindows(enum_proc, 0)
    return windows


def focus_window(
    *,
    title: str = "",
    title_contains: str = "",
    class_name: str = "",
    process_id: int | None = None,
    hwnd: int | None = None,
) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"ok": False, "error": "Window focus is only supported on Windows.", "available": False}
    target = None
    for candidate in list_windows():
        if hwnd is not None and int(candidate.get("hwnd") or 0) != int(hwnd):
            continue
        if title and str(candidate.get("title") or "") != title:
            continue
        if title_contains and title_contains.casefold() not in str(candidate.get("title") or "").casefold():
            continue
        if class_name and str(candidate.get("class_name") or "") != class_name:
            continue
        if process_id is not None and int(candidate.get("process_id") or 0) != int(process_id):
            continue
        target = candidate
        break
    if target is None:
        return {
            "ok": False,
            "error": "Window not found.",
            "query": {
                "title": title,
                "title_contains": title_contains,
                "class_name": class_name,
                "process_id": process_id,
                "hwnd": hwnd,
            },
        }
    user32 = ctypes.windll.user32
    target_hwnd = int(target["hwnd"])
    try:
        user32.ShowWindow(target_hwnd, 9)
    except (ctypes.ArgumentError, OSError, RuntimeError, AttributeError):
        # Restoring a minimized window is best-effort; foregrounding still runs.
        logger.debug("ShowWindow failed for hwnd %s", target_hwnd, exc_info=True)
    ok = bool(user32.SetForegroundWindow(target_hwnd))
    return {"ok": ok, "window": target, "action": "focus_window", "error": "" if ok else "SetForegroundWindow failed."}


def _window_title(user32: Any, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return str(buffer.value or "")


def _window_class_name(user32: Any, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    if not hasattr(user32, "GetClassNameW"):
        return ""
    user32.GetClassNameW(hwnd, buffer, 256)
    return str(buffer.value or "")


def _window_process_id(user32: Any, hwnd: int) -> int | None:
    process_id = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value) if process_id.value else None


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _window_rect(user32: Any, hwnd: int) -> Rect | None:
    rect = _WinRect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return Rect(
        x=int(rect.left),
        y=int(rect.top),
        width=max(0, int(rect.right - rect.left)),
        height=max(0, int(rect.bottom - rect.top)),
    )


_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040


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
        raise UIAutomationUnavailable("Windows SendInput failed.")


def _send_unicode_character(character: str) -> None:
    codepoint = ord(character)
    _send_keyboard_input(0, codepoint, _KEYEVENTF_UNICODE)
    _send_keyboard_input(0, codepoint, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)


def _send_virtual_key(vk_code: int, *, key_up: bool = False) -> None:
    _send_keyboard_input(vk_code, 0, _KEYEVENTF_KEYUP if key_up else 0)


def _ctypes_press_key(key: str) -> None:
    vk_code = virtual_key_code(key)
    if vk_code is None:
        if len(key) == 1:
            _send_unicode_character(key)
            return
        raise UIAutomationUnavailable(f"Unsupported key without pyautogui: {key}")
    _send_virtual_key(vk_code)
    _send_virtual_key(vk_code, key_up=True)


def _ctypes_hotkey(keys: list[str]) -> None:
    vk_codes = [virtual_key_code(key) for key in keys]
    if any(code is None for code in vk_codes):
        missing = [key for key, code in zip(keys, vk_codes, strict=False) if code is None]
        raise UIAutomationUnavailable(f"Unsupported hotkey without pyautogui: {', '.join(missing)}")
    for vk_code in vk_codes[:-1]:
        _send_virtual_key(int(vk_code))
    _send_virtual_key(int(vk_codes[-1]))
    _send_virtual_key(int(vk_codes[-1]), key_up=True)
    for vk_code in reversed(vk_codes[:-1]):
        _send_virtual_key(int(vk_code), key_up=True)


def _ctypes_mouse_flags(button: str) -> tuple[int, int]:
    if button == "right":
        return _MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP
    if button == "middle":
        return _MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP
    return _MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP


def _ctypes_mouse_click(x: int, y: int, button: str, clicks: int) -> None:
    user32 = ctypes.windll.user32
    down, up = _ctypes_mouse_flags(button)
    user32.SetCursorPos(int(x), int(y))
    for _ in range(max(1, clicks)):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)


def _ctypes_mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float,
    button: str,
) -> None:
    user32 = ctypes.windll.user32
    down, up = _ctypes_mouse_flags(button)
    user32.SetCursorPos(int(start_x), int(start_y))
    user32.mouse_event(down, 0, 0, 0, 0)
    if duration > 0:
        time.sleep(duration)
    user32.SetCursorPos(int(end_x), int(end_y))
    user32.mouse_event(_MOUSEEVENTF_MOVE, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)
