from __future__ import annotations

import ctypes
import sys
from typing import Any

from app.perception.schemas import AppContext, Rect, UIElement


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def get_current_app_context() -> AppContext:
    if sys.platform != "win32":
        return AppContext(platform=sys.platform)

    try:
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return AppContext(platform=sys.platform)

        title = _window_title(user32, hwnd)
        process_id = _window_process_id(user32, hwnd)
        rect = _window_rect(user32, hwnd)
        process_name = _process_name(process_id)
        focus_control = _focused_control()
        metadata = _window_metadata(hwnd)

        return AppContext(
            platform=sys.platform,
            available=True,
            active_window_title=title,
            process_name=process_name,
            process_id=process_id,
            active_window_rect=rect,
            focus_control=focus_control,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        return AppContext(platform=sys.platform, error=str(exc))


def _window_title(user32: Any, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return str(buffer.value or "")


def _window_process_id(user32: Any, hwnd: int) -> int | None:
    process_id = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value) if process_id.value else None


def _window_rect(user32: Any, hwnd: int) -> Rect | None:
    rect = _WinRect()
    ok = user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if not ok:
        return None
    return Rect(
        x=int(rect.left),
        y=int(rect.top),
        width=max(0, int(rect.right - rect.left)),
        height=max(0, int(rect.bottom - rect.top)),
    )


def _process_name(process_id: int | None) -> str:
    if not process_id:
        return ""
    try:
        import psutil

        return str(psutil.Process(process_id).name())
    except Exception:  # noqa: BLE001
        return ""


def _focused_control() -> UIElement | None:
    return _focused_control_from_comtypes() or _focused_control_from_pywin32()


def _focused_control_from_comtypes() -> UIElement | None:
    try:
        import comtypes.client

        automation = comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        element = automation.GetFocusedElement()
        if element is None:
            return None

        rect = getattr(element, "CurrentBoundingRectangle", None)
        bounds = None
        if rect is not None:
            bounds = Rect(
                x=int(getattr(rect, "left", 0)),
                y=int(getattr(rect, "top", 0)),
                width=max(0, int(getattr(rect, "right", 0) - getattr(rect, "left", 0))),
                height=max(0, int(getattr(rect, "bottom", 0) - getattr(rect, "top", 0))),
            )

        name = str(getattr(element, "CurrentName", "") or "")
        return UIElement(
            role=str(getattr(element, "CurrentControlType", "") or ""),
            name=name,
            text=name,
            bounding_box=bounds,
            attributes={
                "automation_id": str(getattr(element, "CurrentAutomationId", "") or ""),
                "class_name": str(getattr(element, "CurrentClassName", "") or ""),
            },
        )
    except Exception:  # noqa: BLE001
        return None


def _focused_control_from_pywin32() -> UIElement | None:
    try:
        import win32gui

        hwnd = win32gui.GetFocus() or win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = str(win32gui.GetWindowText(hwnd) or "")
        class_name = str(win32gui.GetClassName(hwnd) or "")
        return UIElement(
            role="window",
            name=title,
            text=title,
            attributes={"class_name": class_name, "hwnd": int(hwnd)},
        )
    except Exception:  # noqa: BLE001
        return None


def _window_metadata(hwnd: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {"hwnd": hwnd}
    try:
        import win32gui

        metadata["class_name"] = str(win32gui.GetClassName(hwnd) or "")
    except Exception:  # noqa: BLE001
        pass
    return metadata
