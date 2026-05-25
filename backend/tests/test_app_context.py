from __future__ import annotations

from app.perception import app_context


def test_non_windows_returns_empty_app_context(monkeypatch):
    monkeypatch.setattr(app_context.sys, "platform", "linux")

    context = app_context.get_current_app_context()

    assert context.platform == "linux"
    assert context.available is False
    assert context.active_window_title == ""
    assert context.focus_control is None


def test_windows_context_uses_ctypes_when_available(monkeypatch):
    class FakeUser32:
        def GetForegroundWindow(self):
            return 123

        def GetWindowTextLengthW(self, hwnd):
            return len("Notepad")

        def GetWindowTextW(self, hwnd, buffer, size):
            buffer.value = "Notepad"
            return len(buffer.value)

        def GetWindowThreadProcessId(self, hwnd, process_id_ref):
            process_id_ref._obj.value = 42
            return 1

        def GetWindowRect(self, hwnd, rect_ref):
            rect = rect_ref._obj
            rect.left = 10
            rect.top = 20
            rect.right = 310
            rect.bottom = 220
            return 1

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(app_context.sys, "platform", "win32")
    monkeypatch.setattr(app_context.ctypes, "windll", FakeWindll(), raising=False)
    monkeypatch.setattr(app_context, "_process_name", lambda process_id: "notepad.exe")
    monkeypatch.setattr(app_context, "_focused_control", lambda: None)
    monkeypatch.setattr(app_context, "_window_metadata", lambda hwnd: {"hwnd": hwnd, "class_name": "Notepad"})

    context = app_context.get_current_app_context()

    assert context.available is True
    assert context.active_window_title == "Notepad"
    assert context.process_id == 42
    assert context.process_name == "notepad.exe"
    assert context.active_window_rect.x == 10
    assert context.active_window_rect.width == 300
    assert context.metadata["class_name"] == "Notepad"


def test_windows_context_gracefully_handles_dependency_errors(monkeypatch):
    class BrokenUser32:
        def GetForegroundWindow(self):
            raise RuntimeError("user32 unavailable")

    class FakeWindll:
        user32 = BrokenUser32()

    monkeypatch.setattr(app_context.sys, "platform", "win32")
    monkeypatch.setattr(app_context.ctypes, "windll", FakeWindll(), raising=False)

    context = app_context.get_current_app_context()

    assert context.available is False
    assert "user32 unavailable" in context.error
