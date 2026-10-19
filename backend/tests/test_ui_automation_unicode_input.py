"""Windows SendInput ABI and Unicode semantics without sending real input."""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

import pytest

from app.perception import ui_automation_actions as actions


def test_windows_input_uses_the_full_sdk_union_layout():
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(actions._Input) == (40 if pointer_size == 8 else 28)
    assert actions._Input.union.offset == (8 if pointer_size == 8 else 4)
    assert ctypes.sizeof(actions._KeyboardInput) == (24 if pointer_size == 8 else 16)


@pytest.fixture
def sent_input(monkeypatch):
    events = []

    def send(count, pointer, size):
        assert count == 1
        assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        event = ctypes.cast(pointer, ctypes.POINTER(actions._Input)).contents
        events.append((event.type, event.union.ki.wVk, event.union.ki.wScan, event.union.ki.dwFlags))
        return 1

    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendInput=send)), raising=False)
    return events


def test_unicode_text_preserves_bmp_and_surrogate_pairs(sent_input):
    actions.send_text("A中😀")
    units = [0x41, 0x4E2D, 0xD83D, 0xDE00]
    assert sent_input == [(1, 0, unit, flag) for unit in units for flag in (4, 6)]


def test_invalid_unicode_is_rejected_before_sending_a_partial_prefix(sent_input):
    with pytest.raises((UnicodeError, ValueError, actions.UIAutomationUnavailable)):
        actions.send_text("prefix\ud800suffix")
    assert sent_input == []


@pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
def test_text_line_endings_send_one_native_enter(sent_input, newline):
    actions.send_text("a" + newline + "b")
    assert sent_input == [
        (1, 0, ord("a"), 4),
        (1, 0, ord("a"), 6),
        (1, 0x0D, 0, 0),
        (1, 0x0D, 0, 2),
        (1, 0, ord("b"), 4),
        (1, 0, ord("b"), 6),
    ]


def test_keyboard_text_stops_after_focus_or_cancellation_guard_fails(sent_input):
    checks = []

    def guard():
        checks.append(True)
        if len(checks) == 2:
            raise actions.UIAutomationUnavailable("Focus moved")

    with pytest.raises(actions.UIAutomationUnavailable, match="Focus moved"):
        actions.send_text("ab", before_input=guard)
    assert sent_input == [(1, 0, ord("a"), 4), (1, 0, ord("a"), 6)]


def test_native_hotkey_uses_valid_keyboard_events(sent_input):
    actions._ctypes_hotkey(["control", "s"])
    assert sent_input == [(1, 0x11, 0, 0), (1, 0x53, 0, 0), (1, 0x53, 0, 2), (1, 0x11, 0, 2)]


def test_native_hotkey_releases_modifiers_when_a_later_key_fails(monkeypatch):
    events = []

    def send(vk_code, *, key_up=False):
        events.append((vk_code, key_up))
        if vk_code == 0x53 and not key_up:
            raise actions.UIAutomationUnavailable("Failed final key")

    monkeypatch.setattr(actions, "_send_virtual_key", send)
    with pytest.raises(actions.UIAutomationUnavailable, match="Failed final key"):
        actions._ctypes_hotkey(["control", "s"])
    assert events == [(0x11, False), (0x53, False), (0x11, True)]


def test_pyautogui_receives_supported_control_alias(monkeypatch):
    events = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(hotkey=lambda *keys: events.append(keys)))
    actions.send_hotkey(["ctrl", "s"])
    assert events == [("ctrl", "s")]
