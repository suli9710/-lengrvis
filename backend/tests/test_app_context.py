from __future__ import annotations

import sys
import types

import pytest

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


def test_process_name_suppresses_psutil_errors_but_not_unexpected_bugs(monkeypatch):
    class FakePsutilError(Exception):
        pass

    class FakePsutil(types.SimpleNamespace):
        Error = FakePsutilError

    fake_psutil = FakePsutil()

    class MissingProcess:
        def __init__(self, _process_id):
            pass

        def name(self):
            raise FakePsutilError("process vanished")

    fake_psutil.Process = MissingProcess
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert app_context._process_name(42) == ""

    class BuggyProcess:
        def __init__(self, _process_id):
            pass

        def name(self):
            raise RuntimeError("psutil wrapper bug")

    fake_psutil.Process = BuggyProcess
    with pytest.raises(RuntimeError, match="psutil wrapper bug"):
        app_context._process_name(42)


def test_app_context_optional_focus_providers_are_narrow(monkeypatch):
    class FakeComtypesError(Exception):
        pass

    fake_comtypes = types.ModuleType("comtypes")
    fake_comtypes.COMError = FakeComtypesError
    fake_comtypes.__path__ = []
    fake_client = types.ModuleType("comtypes.client")
    fake_client.CreateObject = lambda _name: (_ for _ in ()).throw(FakeComtypesError("uia down"))
    fake_comtypes.client = fake_client
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)
    assert app_context._focused_control_from_comtypes() is None

    fake_client.CreateObject = lambda _name: (_ for _ in ()).throw(RuntimeError("uia runtime down"))
    assert app_context._focused_control_from_comtypes() is None

    fake_client.CreateObject = lambda _name: (_ for _ in ()).throw(AssertionError("uia bug"))
    with pytest.raises(AssertionError, match="uia bug"):
        app_context._focused_control_from_comtypes()

    class FakePywinError(Exception):
        pass

    fake_pywintypes = types.ModuleType("pywintypes")
    fake_pywintypes.error = FakePywinError
    fake_pywintypes.com_error = FakePywinError
    fake_win32gui = types.ModuleType("win32gui")
    fake_win32gui.GetFocus = lambda: 1
    fake_win32gui.GetForegroundWindow = lambda: 1
    fake_win32gui.GetWindowText = lambda _hwnd: (_ for _ in ()).throw(FakePywinError("win32 unavailable"))
    fake_win32gui.GetClassName = lambda _hwnd: "Edit"
    monkeypatch.setitem(sys.modules, "pywintypes", fake_pywintypes)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    assert app_context._focused_control_from_pywin32() is None
    assert app_context._window_metadata(123) == {"hwnd": 123, "class_name": "Edit"}

    fake_win32gui.GetClassName = lambda _hwnd: (_ for _ in ()).throw(FakePywinError("class unavailable"))
    assert app_context._window_metadata(123) == {"hwnd": 123}
