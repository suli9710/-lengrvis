from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.perception import ui_automation as uia


class UnknownPattern:
    """The native API returns IUnknown, not an already typed Python object."""

    def __init__(self, typed):
        self.typed = typed
        self.queries = []

    def QueryInterface(self, interface):
        self.queries.append(interface)
        return self.typed


@pytest.fixture
def interfaces(monkeypatch):
    module = SimpleNamespace(
        IUIAutomationInvokePattern=object(), IUIAutomationValuePattern=object(), IUIAutomationTextPattern=object()
    )
    # Test the actual IUnknown boundary without depending on a Windows desktop.
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", module)
    return module


def test_invoke_queries_native_interface_without_coordinate_fallback(interfaces):
    invoked = []
    unknown = UnknownPattern(SimpleNamespace(Invoke=lambda: invoked.append(True)))
    native = SimpleNamespace(GetCurrentPattern=lambda _: unknown)
    target = uia.WindowsCOMUIAutomationTarget(automation=object())

    target._click_sync(native)

    assert invoked == [True]
    assert unknown.queries == [interfaces.IUIAutomationInvokePattern]


def test_set_value_queries_native_interface_without_focus_or_keyboard(interfaces):
    values = []
    unknown = UnknownPattern(SimpleNamespace(SetValue=values.append))
    native = SimpleNamespace(GetCurrentPattern=lambda _: unknown)
    target = uia.WindowsCOMUIAutomationTarget(automation=object())

    target._type_text_sync(native, "跨应用文本")

    assert values == ["跨应用文本"]
    assert unknown.queries == [interfaces.IUIAutomationValuePattern]


def test_read_value_uses_typed_pattern_and_preserves_empty_value(interfaces):
    unknown = UnknownPattern(SimpleNamespace(CurrentValue=""))
    native = SimpleNamespace(CurrentName="Document title", GetCurrentPattern=lambda _: unknown)

    assert uia._native_text(native) == ""
    assert unknown.queries == [interfaces.IUIAutomationValuePattern]


def test_read_document_uses_bounded_text_pattern(interfaces):
    limits = []

    def read_text(limit):
        limits.append(limit)
        return "邮件正文\n下一行"

    unknown = UnknownPattern(SimpleNamespace(DocumentRange=SimpleNamespace(GetText=read_text)))

    def get_pattern(pattern_id):
        if pattern_id == 10002:
            raise RuntimeError("ValuePattern unavailable")
        assert pattern_id == 10014
        return unknown

    native = SimpleNamespace(CurrentName="Mail", GetCurrentPattern=get_pattern)

    assert uia._native_text(native) == "邮件正文\n下一行"
    # One extra UTF-16 unit distinguishes a complete value from a truncated one.
    assert 0 < limits[0] <= 65537
    assert unknown.queries == [interfaces.IUIAutomationTextPattern]


@pytest.mark.parametrize("pattern_id", [10002, 10014])
@pytest.mark.parametrize("content", ["x" * 65537, "😀" * 32769], ids=["bmp", "surrogate_pairs"])
def test_oversized_native_text_is_rejected_instead_of_silently_truncated(pattern_id, content):
    def get_pattern(requested):
        if requested != pattern_id:
            raise RuntimeError("Pattern unavailable")
        if pattern_id == 10002:
            return SimpleNamespace(CurrentValue=content)
        return SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda limit: content[:limit]))

    native = SimpleNamespace(CurrentIsPassword=False, CurrentName="Document label", GetCurrentPattern=get_pattern)

    with pytest.raises(uia.UIAutomationUnavailable, match="text limit"):
        uia._native_text(native)


@pytest.mark.parametrize("content", ["x" * 65536, "😀" * 32768], ids=["bmp", "surrogate_pairs"])
def test_native_text_at_the_limit_is_preserved(content):
    native = SimpleNamespace(GetCurrentPattern=lambda _: SimpleNamespace(CurrentValue=content))

    assert uia._native_text(native) == content


def test_password_text_is_never_read():
    def get_pattern(_):
        raise AssertionError("Password control must not be queried for text")

    native = SimpleNamespace(CurrentIsPassword=True, CurrentName="Password", GetCurrentPattern=get_pattern)

    assert uia._native_text(native) == ""


def test_password_status_error_does_not_fall_back_to_reading_text():
    class UnavailablePasswordStatus:
        @property
        def CurrentIsPassword(self):
            raise RuntimeError("Provider cannot report password status")

        def GetCurrentPattern(self, _):
            pytest.fail("Text must remain unread when password status cannot be checked")

    assert uia._native_text(UnavailablePasswordStatus()) == ""


def test_native_edit_read_failure_does_not_fall_back_to_a_clipped_value(monkeypatch):
    def unavailable(*_):
        raise OSError("Native text retrieval failed or timed out.")

    monkeypatch.setattr(uia._com, "edit_control_text", unavailable)
    native = SimpleNamespace(GetCurrentPattern=lambda _: pytest.fail("Must not hide an incomplete native read"))

    with pytest.raises(uia.UIAutomationUnavailable, match="timed out"):
        uia._native_text(native)


def test_click_without_pattern_or_bounds_fails_instead_of_reporting_focus_as_click():
    focus_calls = []

    def get_pattern(_):
        raise RuntimeError("InvokePattern unavailable")

    native = SimpleNamespace(GetCurrentPattern=get_pattern, SetFocus=lambda: focus_calls.append(True))
    target = uia.WindowsCOMUIAutomationTarget(automation=object())

    with pytest.raises(uia.UIAutomationUnavailable, match="click"):
        target._click_sync(native)
    assert focus_calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows UIAutomationCore")
def test_windows_native_activation_provides_typed_automation():
    pytest.importorskip("comtypes.client")
    target = uia.WindowsCOMUIAutomationTarget()

    assert target.available, target.unavailable_reason
    assert callable(target._automation.GetRootElement)


@pytest.mark.asyncio
async def test_property_lookup_never_exposes_a_native_method():
    native = SimpleNamespace(GetCurrentPattern=lambda _: object())
    target = uia.WindowsCOMUIAutomationTarget(automation=object())
    element = uia.UIAutomationElement(name="Editor", native=native)

    assert await target.get_property(element, "GetCurrentPattern") is None
    assert await target.get_property(element, "CurrentName") == "Editor"


@pytest.mark.parametrize("action", ["click", "type_text"])
def test_pattern_action_failure_does_not_send_a_second_input(monkeypatch, action):
    def rejected(*_):
        raise RuntimeError("Native operation rejected")

    native = SimpleNamespace(
        GetCurrentPattern=lambda _: SimpleNamespace(Invoke=rejected, SetValue=rejected),
        CurrentBoundingRectangle=SimpleNamespace(left=0, top=0, right=20, bottom=20),
        SetFocus=lambda: pytest.fail("A failed operation must not silently fall back to another input"),
    )
    target = uia.WindowsCOMUIAutomationTarget(automation=object())
    with pytest.raises(RuntimeError, match="Native operation rejected"):
        if action == "click":
            target._click_sync(native)
        else:
            target._type_text_sync(native, "draft")


def test_keyboard_newline_cannot_submit_an_unclassified_or_single_line_field(monkeypatch):
    def no_value(_):
        raise RuntimeError("ValuePattern unavailable")

    native = SimpleNamespace(
        CurrentControlType=50004,  # Edit does not establish multiline semantics.
        GetCurrentPattern=no_value,
        SetFocus=lambda: pytest.fail("Reject a potential submit before taking focus"),
    )
    monkeypatch.setattr(uia, "_send_text", lambda *a, **k: pytest.fail("No input may be dispatched"))
    target = uia.WindowsCOMUIAutomationTarget(automation=object())
    with pytest.raises(uia.UIAutomationUnavailable, match="Document control"):
        target._type_text_sync(native, "first\r\nsecond")
