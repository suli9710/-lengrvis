from __future__ import annotations

import builtins
import sys
import types

from app.perception import ui_automation as uia
from app.perception import ui_automation_actions as actions


def test_main_module_reexports_action_exception() -> None:
    assert uia.UIAutomationUnavailable is actions.UIAutomationUnavailable


def test_action_inputs_are_normalized_and_bounded() -> None:
    assert actions.bounded_int("12", default=1, minimum=1, maximum=10) == 10
    assert actions.bounded_int("invalid", default=3, minimum=1, maximum=10) == 3
    assert actions.normalize_mouse_button(" RIGHT ") == "right"
    assert actions.normalize_mouse_button("side") == "left"
    assert actions.normalize_key(" Ctrl ") == "control"
    assert actions.normalize_key("F12") == "f12"
    assert actions.virtual_key_code("escape") == 0x1B
    assert actions.virtual_key_code("z") == ord("Z")
    assert actions.virtual_key_code("?") is None


def test_windows_input_uses_ctypes_fallback_without_pyautogui(monkeypatch) -> None:
    real_import = builtins.__import__
    pressed: list[str] = []
    clicked: list[tuple[int, int, str, int]] = []

    def import_without_pyautogui(name, *args, **kwargs):
        if name == "pyautogui":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pyautogui)
    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions, "_ctypes_press_key", pressed.append)
    monkeypatch.setattr(
        actions,
        "_ctypes_mouse_click",
        lambda x, y, button, clicks: clicked.append((x, y, button, clicks)),
    )

    actions.press_key("Ctrl")
    actions.send_mouse_click(10, 20, "invalid", 2)

    assert pressed == ["control"]
    assert clicked == [(10, 20, "left", 2)]


def test_capture_screenshot_normalizes_frame_and_context(monkeypatch) -> None:
    frame = types.SimpleNamespace(
        image_base64="aW1hZ2U=",
        timestamp="2026-07-03T12:00:00Z",
        width=800,
        height=450,
        original_width=1600,
        original_height=900,
        quality=60,
    )
    remote_desktop = types.SimpleNamespace(capture_screen_frame=lambda **_kwargs: frame)
    monkeypatch.setitem(sys.modules, "app.services.remote_desktop_service", remote_desktop)
    monkeypatch.setattr(
        actions,
        "get_current_app_context",
        lambda: types.SimpleNamespace(model_dump=lambda mode="json": {"available": True}),
    )

    result = actions.capture_screenshot(800, 450, 60)

    assert result == {
        "ok": True,
        "image": "data:image/jpeg;base64,aW1hZ2U=",
        "mime_type": "image/jpeg",
        "timestamp": "2026-07-03T12:00:00Z",
        "width": 800,
        "height": 450,
        "original_width": 1600,
        "original_height": 900,
        "quality": 60,
        "app_context": {"available": True},
    }


def test_focus_window_filters_candidates_and_reports_windows_result(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeUser32:
        def ShowWindow(self, hwnd: int, command: int) -> None:
            assert command == 9
            calls.append(("show", hwnd))

        def SetForegroundWindow(self, hwnd: int) -> bool:
            calls.append(("foreground", hwnd))
            return True

    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(
        actions,
        "list_windows",
        lambda: [
            {"hwnd": 12, "title": "Other", "class_name": "Window", "process_id": 4, "rect": None},
            {"hwnd": 34, "title": "Editor - notes", "class_name": "Editor", "process_id": 8, "rect": None},
        ],
    )
    monkeypatch.setattr(actions.ctypes, "windll", types.SimpleNamespace(user32=FakeUser32()), raising=False)

    result = actions.focus_window(title_contains="notes", class_name="Editor", process_id=8)

    assert result == {
        "ok": True,
        "window": {
            "hwnd": 34,
            "title": "Editor - notes",
            "class_name": "Editor",
            "process_id": 8,
            "rect": None,
        },
        "action": "focus_window",
        "error": "",
    }
    assert calls == [("show", 34), ("foreground", 34)]
