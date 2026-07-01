from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview
from app.perception import ui_automation as uia
from app.perception.ui_automation import (
    UIAutomationElement,
    UnavailableUIAutomationTarget,
    WindowsCOMUIAutomationTarget,
    create_ui_automation_target,
)
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import ui_automation_tools
from app.tools.registry import register_all_tools


class FakePolicy:
    def __init__(self, verdict: SafetyVerdict = SafetyVerdict.ALLOW) -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, dict]] = []

    def review_tool_call(self, task_id, step_id, tool_name, args, risk_level):
        self.calls.append((tool_name, args))
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_call",
            verdict=self.verdict,
            risk_level=risk_level,
            reasons=["fake policy"],
        )


def _bound_ui_approval(
    *,
    task_id: str = "task_1",
    step_id: str = "step_1",
    tool_name: str = "ui_automation.key_press",
    args: dict | None = None,
    risk_level: RiskLevel = RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
) -> Approval:
    bound_args = args or {"key": "enter"}
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "key_press", **bound_args}]}
    return Approval(
        task_id=task_id,
        step_id=step_id,
        status=ApprovalStatus.APPROVED,
        message="Approve UIAutomation action",
        diff_preview=preview,
        tool_name=tool_name,
        risk_level=risk_level.value,
        args_binding_hmac=args_binding_hmac(tool_name, bound_args, task_id=task_id, step_id=step_id),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(None, allowed_directories=[]),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
    )


class FakeNative:
    CurrentName = "Send"
    CurrentAutomationId = "send_button"
    CurrentControlType = "Button"
    CurrentClassName = "Button"
    CurrentProcessId = 42
    CurrentIsEnabled = True
    CurrentIsKeyboardFocusable = True
    CurrentIsOffscreen = False
    CurrentHasKeyboardFocus = False
    CurrentLocalizedControlType = "button"

    def __init__(
        self,
        *,
        name: str = "Send",
        automation_id: str = "send_button",
        control_type: str = "Button",
        children: list[FakeNative] | None = None,
    ) -> None:
        self.CurrentName = name
        self.CurrentAutomationId = automation_id
        self.CurrentControlType = control_type
        self.children = children or []
        self.invoked = False
        self.value = ""
        self.focused = False

    def GetCurrentPattern(self, pattern_id: int):
        if pattern_id == 10000:
            return self
        if pattern_id == 10002:
            return self
        raise RuntimeError("unsupported pattern")

    def Invoke(self) -> None:
        self.invoked = True

    def SetValue(self, text: str) -> None:
        self.value = text

    def SetFocus(self) -> None:
        self.focused = True

    def FindAll(self, scope: int, condition):
        return FakeCollection(self.children)


class FakeCollection:
    def __init__(self, children: list[FakeNative]) -> None:
        self._children = children
        self.Length = len(children)

    def GetElement(self, index: int):
        return self._children[index]


class FakeAutomation:
    def __init__(self, root: FakeNative) -> None:
        self.root = root

    def GetRootElement(self):
        return self.root

    def CreateTrueCondition(self):
        return object()


def _install_fake_comtypes(monkeypatch: pytest.MonkeyPatch, create_object) -> None:
    fake_client = types.ModuleType("comtypes.client")
    fake_client.CreateObject = create_object
    fake_comtypes = types.ModuleType("comtypes")
    fake_comtypes.client = fake_client
    monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", fake_client)


def test_ui_automation_bridge_times_out_slow_call() -> None:
    async def slow_call() -> dict:
        await asyncio.sleep(1.0)
        return {"ok": True}

    started = time.monotonic()
    result = ui_automation_tools._run_ui_automation(slow_call(), "slow", timeout_seconds=0.01)

    assert result["ok"] is False
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5


def test_ui_automation_bridge_timeout_returns_from_running_event_loop() -> None:
    async def slow_call() -> dict:
        await asyncio.sleep(1.0)
        return {"ok": True}

    async def invoke() -> dict:
        return ui_automation_tools._run_ui_automation(slow_call(), "slow", timeout_seconds=0.01)

    started = time.monotonic()
    result = asyncio.run(invoke())

    assert result["ok"] is False
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_unavailable_target_gracefully_reports_actions():
    target = UnavailableUIAutomationTarget("missing provider")

    assert await target.find_element({"name": "Anything"}) is None
    assert await target.get_children({"name": "Anything"}) == []
    assert await target.wait_for_element({"name": "Anything"}, timeout_seconds=0) is None
    assert await target.click({"name": "Anything"}) == {"ok": False, "error": "missing provider", "available": False}
    assert await target.observe() == {
        "ok": False,
        "error": "missing provider",
        "available": False,
        "elements": [],
        "count": 0,
    }


def test_factory_returns_graceful_target_when_provider_missing():
    target = create_ui_automation_target(policy_engine=FakePolicy())

    assert isinstance(target, WindowsCOMUIAutomationTarget | UnavailableUIAutomationTarget)


def test_windows_adapter_reports_expected_com_activation_errors(monkeypatch):
    def fail_create_object(_prog_id: str):
        raise OSError("activation failed")

    _install_fake_comtypes(monkeypatch, fail_create_object)
    monkeypatch.setattr(uia.sys, "platform", "win32")

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy())

    assert target.available is False
    assert "activation failed" in target.unavailable_reason


def test_windows_adapter_does_not_swallow_unexpected_com_activation_bugs(monkeypatch):
    def fail_create_object(_prog_id: str):
        raise AssertionError("activation bug")

    _install_fake_comtypes(monkeypatch, fail_create_object)
    monkeypatch.setattr(uia.sys, "platform", "win32")

    with pytest.raises(AssertionError, match="activation bug"):
        WindowsCOMUIAutomationTarget(policy_engine=FakePolicy())


@pytest.mark.asyncio
async def test_windows_adapter_finds_and_clicks_native_element_with_policy():
    native = FakeNative()
    policy = FakePolicy()
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(native))

    found = await target.find_element(automation_id="send_button")
    clicked = await target.click(found, task_id="task_1", step_id="step_1")

    assert isinstance(found, UIAutomationElement)
    assert clicked["ok"] is True
    assert native.invoked is True
    assert policy.calls[0][0] == "ui_automation.click"
    assert found.to_perception_element().attributes["automation_id"] == "send_button"


@pytest.mark.asyncio
async def test_type_text_is_blocked_when_policy_requires_approval():
    native = FakeNative()
    policy = FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL)
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(native))

    result = await target.type_text({"automation_id": "send_button"}, "hello")

    assert result["approval_required"] is True
    assert policy.calls[0][0] == "ui_automation.type_text"
    assert native.value == ""


@pytest.mark.asyncio
async def test_type_text_sets_value_when_policy_allows():
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    result = await target.type_text({"automation_id": "send_button"}, "hello")

    assert result["ok"] is True
    assert result["characters"] == 5
    assert native.value == "hello"


@pytest.mark.asyncio
async def test_user_contract_supports_find_kwargs_and_property_lookup():
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    element = await target.find_element(name="Send", control_type="Button", automation_id="send_button")

    assert element is not None
    assert await target.get_property(element, "is_enabled") is True


@pytest.mark.asyncio
async def test_observe_returns_tree_and_flat_elements(monkeypatch):
    child = FakeNative(name="Cancel", automation_id="cancel_button")
    root = FakeNative(name="Window", automation_id="root", control_type="Window", children=[child])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))
    monkeypatch.setattr(
        "app.perception.ui_automation.get_current_app_context",
        lambda: type(
            "FakeContext", (), {"available": True, "model_dump": lambda self, mode="json": {"available": True}}
        )(),
    )

    observed = await target.observe(max_depth=1, max_elements=10)

    assert observed["ok"] is True
    assert observed["count"] == 2
    assert observed["root"]["children"][0]["name"] == "Cancel"


def test_children_sync_degrades_for_expected_provider_errors():
    class FindAllFailingNative(FakeNative):
        def FindAll(self, scope: int, condition):  # noqa: ARG002
            raise RuntimeError("tree unavailable")

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(FakeNative()))

    assert target._children_sync(FindAllFailingNative()) == []


def test_children_sync_skips_stale_provider_children():
    class StaleCollection:
        Length = 2

        def GetElement(self, index: int):
            if index == 0:
                raise RuntimeError("stale child")
            return FakeNative(name="Ready", automation_id="ready_button")

    class NativeWithStaleChild(FakeNative):
        def FindAll(self, scope: int, condition):  # noqa: ARG002
            return StaleCollection()

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(FakeNative()))

    children = target._children_sync(NativeWithStaleChild())

    assert len(children) == 1
    assert children[0].name == "Ready"


@pytest.mark.asyncio
async def test_wait_for_element_supports_contains_selector():
    child = FakeNative(name="Send message", automation_id="send_button")
    root = FakeNative(name="Window", automation_id="root", control_type="Window", children=[child])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))

    element = await target.wait_for_element({"name_contains": "message"}, timeout_seconds=0)

    assert element is not None
    assert element.automation_id == "send_button"


@pytest.mark.asyncio
async def test_focus_sets_native_focus():
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    result = await target.focus({"automation_id": "send_button"})

    assert result["ok"] is True
    assert native.focused is True


@pytest.mark.asyncio
async def test_action_wrappers_return_error_for_expected_provider_failures(monkeypatch):
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    def fail_action(*args, **kwargs):  # noqa: ARG001
        raise uia.UIAutomationUnavailable("provider failed")

    monkeypatch.setattr(target, "_click_sync", fail_action)
    monkeypatch.setattr(target, "_type_text_sync", fail_action)
    monkeypatch.setattr(target, "_focus_sync", fail_action)
    monkeypatch.setattr(uia, "_send_mouse_click", fail_action)
    monkeypatch.setattr(uia, "_send_mouse_drag", fail_action)
    monkeypatch.setattr(uia, "_press_key", fail_action)
    monkeypatch.setattr(uia, "_send_hotkey", fail_action)

    click = await target.click({"automation_id": "send_button"})
    typed = await target.type_text({"automation_id": "send_button"}, "hello")
    focused = await target.focus({"automation_id": "send_button"})
    clicked_at = await target.click_at(1, 2)
    dragged = await target.drag(1, 2, 3, 4)
    key = await target.key_press("enter")
    hotkey = await target.hotkey(["ctrl", "s"])

    assert click["ok"] is False
    assert typed["ok"] is False
    assert focused["ok"] is False
    assert clicked_at["ok"] is False
    assert dragged["ok"] is False
    assert key["ok"] is False
    assert hotkey["ok"] is False
    assert all(
        "provider failed" in result["error"] for result in (click, typed, focused, clicked_at, dragged, key, hotkey)
    )


def test_native_text_falls_back_to_name_when_value_pattern_fails():
    class NativeWithoutValuePattern:
        CurrentName = "Fallback name"

        def GetCurrentPattern(self, _pattern_id: int):
            raise RuntimeError("value pattern unavailable")

    assert uia._native_text(NativeWithoutValuePattern()) == "Fallback name"


def test_click_sync_falls_back_to_pointer_when_invoke_pattern_unavailable(monkeypatch):
    class NativeWithoutInvoke(FakeNative):
        CurrentBoundingRectangle = types.SimpleNamespace(left=2, top=4, right=12, bottom=24)

        def GetCurrentPattern(self, pattern_id: int):
            if pattern_id == 10000:
                raise RuntimeError("invoke unavailable")
            return super().GetCurrentPattern(pattern_id)

    clicked: list[tuple[int, int, str, int]] = []
    monkeypatch.setattr(
        uia, "_send_mouse_click", lambda x, y, button="left", clicks=1: clicked.append((x, y, button, clicks))
    )
    native = NativeWithoutInvoke()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    target._click_sync(native)

    assert native.focused is True
    assert clicked == [(7, 14, "left", 1)]


def test_click_sync_does_not_swallow_unexpected_invoke_bugs():
    class BuggyInvokeNative(FakeNative):
        def GetCurrentPattern(self, pattern_id: int):  # noqa: ARG002
            raise AssertionError("invoke bug")

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(FakeNative()))

    with pytest.raises(AssertionError, match="invoke bug"):
        target._click_sync(BuggyInvokeNative())


def test_type_text_sync_falls_back_to_keyboard_when_value_pattern_unavailable(monkeypatch):
    class NativeWithoutValuePattern(FakeNative):
        def GetCurrentPattern(self, pattern_id: int):
            if pattern_id == 10002:
                raise RuntimeError("value unavailable")
            return super().GetCurrentPattern(pattern_id)

    sent_text: list[str] = []
    monkeypatch.setattr(uia, "_send_text", sent_text.append)
    native = NativeWithoutValuePattern()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    target._type_text_sync(native, "hello")

    assert native.focused is True
    assert sent_text == ["hello"]


def test_type_text_sync_does_not_swallow_unexpected_value_pattern_bugs():
    class BuggyValueNative(FakeNative):
        def GetCurrentPattern(self, pattern_id: int):  # noqa: ARG002
            raise AssertionError("value bug")

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(FakeNative()))

    with pytest.raises(AssertionError, match="value bug"):
        target._type_text_sync(BuggyValueNative(), "hello")


def test_focus_window_continues_when_show_window_fails(monkeypatch):
    calls: list[tuple[str, int]] = []

    class FakeUser32:
        def ShowWindow(self, hwnd: int, command: int) -> None:
            calls.append(("show", hwnd))
            assert command == 9
            raise OSError("show failed")

        def SetForegroundWindow(self, hwnd: int) -> bool:
            calls.append(("foreground", hwnd))
            return True

    monkeypatch.setattr(uia.sys, "platform", "win32")
    monkeypatch.setattr(
        uia,
        "_list_windows_sync",
        lambda: [{"hwnd": 123, "title": "Editor", "class_name": "Window", "process_id": 1, "rect": None}],
    )
    monkeypatch.setattr(uia.ctypes, "windll", types.SimpleNamespace(user32=FakeUser32()), raising=False)

    result = uia._focus_window_sync(title="Editor")

    assert result["ok"] is True
    assert calls == [("show", 123), ("foreground", 123)]


@pytest.mark.asyncio
async def test_approved_keyboard_action_bypasses_policy_wait(monkeypatch):
    policy = FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL)
    approval_context = {}
    mark_execution_approved(approval_context)
    target = WindowsCOMUIAutomationTarget(
        policy_engine=policy,
        automation=FakeAutomation(FakeNative()),
        approval_context=approval_context,
    )
    pressed = []
    monkeypatch.setattr("app.perception.ui_automation._press_key", lambda key: pressed.append(key))

    blocked = await target.key_press("enter")
    allowed = await target.key_press("enter", approved=True, approval_id="approval_2")

    assert blocked["approval_required"] is True
    assert allowed["ok"] is True
    assert pressed == ["enter"]


@pytest.mark.asyncio
async def test_approved_keyboard_action_claims_stored_approval_once(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    policy = FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL)
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(FakeNative()))
    pressed = []
    monkeypatch.setattr("app.perception.ui_automation._press_key", lambda key: pressed.append(key))
    approval = _bound_ui_approval()
    db.upsert_model("approvals", approval, status=approval.status)

    first = await target.key_press("enter", task_id="task_1", step_id="step_1", approved=True, approval_id=approval.id)
    second = await target.key_press("enter", task_id="task_1", step_id="step_1", approved=True, approval_id=approval.id)

    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert first["ok"] is True
    assert second["denied"] is True
    assert "consumed" in " ".join(second["reasons"]).lower()
    assert pressed == ["enter"]
    assert refreshed.consumed_at


@pytest.mark.asyncio
async def test_ui_automation_approval_args_mismatch_denies_before_claim(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL),
        automation=FakeAutomation(FakeNative()),
    )
    pressed = []
    monkeypatch.setattr("app.perception.ui_automation._press_key", lambda key: pressed.append(key))
    approval = _bound_ui_approval(args={"key": "enter"})
    db.upsert_model("approvals", approval, status=approval.status)

    result = await target.key_press(
        "escape",
        task_id="task_1",
        step_id="step_1",
        approved=True,
        approval_id=approval.id,
    )

    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert result["denied"] is True
    assert "arguments" in " ".join(result["reasons"]).lower()
    assert refreshed.consumed_at is None
    assert pressed == []


@pytest.mark.asyncio
async def test_forged_ui_automation_approval_id_does_not_self_authorize(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    policy = FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL)
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(FakeNative()))
    pressed = []
    monkeypatch.setattr("app.perception.ui_automation._press_key", lambda key: pressed.append(key))

    result = await target.key_press("enter", approved=True, approval_id="forged_approval")

    assert result["denied"] is True
    assert "not found" in " ".join(result["reasons"]).lower()
    assert pressed == []


@pytest.mark.asyncio
async def test_approved_gui_action_still_honors_policy_denial():
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(SafetyVerdict.DENY), automation=FakeAutomation(native)
    )

    result = await target.key_press("enter", approved=True, approval_id="approval_denied")

    assert result["denied"] is True


def test_tool_previews_and_registry_cover_complete_gui_actions():
    registry = register_all_tools(load_skills=False)

    assert registry.get("ui_automation.active_window").risk_level == RiskLevel.R0_READ_ONLY
    assert registry.get("ui_automation.observe").risk_level == RiskLevel.R0_READ_ONLY
    assert registry.get("ui_automation.wait_for_element").risk_level == RiskLevel.R0_READ_ONLY
    assert registry.get("ui_automation.focus_window").risk_level == RiskLevel.R1_OPEN_ONLY
    assert registry.get("ui_automation.click_at").risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    assert registry.get("ui_automation.hotkey").supports_dry_run is True

    preview = ui_automation_tools.hotkey({"keys": "ctrl+s", "dry_run": True}, {})

    assert preview["ok"] is True
    assert preview["dry_run"] is True
    assert preview["diff_preview"][0]["keys"] == ["ctrl", "s"]


def test_policy_classifies_gui_input_as_approval_gated():
    policy = PolicyEngine()

    assert policy.classify_tool_call("ui_automation.observe") == RiskLevel.R0_READ_ONLY
    assert policy.classify_tool_call("ui_automation.focus_window") == RiskLevel.R1_OPEN_ONLY
    assert policy.classify_tool_call("ui_automation.click") == RiskLevel.R2_REVERSIBLE_MODIFY
    assert policy.classify_tool_call("ui_automation.key_press") == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM


def test_policy_blocks_sensitive_gui_text_and_targets():
    policy = PolicyEngine()
    fake_token = "abcdef12" + "34567890"

    password_target = policy.review_tool_call(
        "task_sensitive_gui",
        "step_1",
        "ui_automation.type_text",
        {"name": "Password", "text": "hello", "dry_run": True},
        RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    token_text = policy.review_tool_call(
        "task_sensitive_gui",
        "step_2",
        "ui_automation.type_text",
        {"name": "Notes", "text": f"token={fake_token}", "dry_run": True},
        RiskLevel.R2_REVERSIBLE_MODIFY,
    )

    assert password_target.verdict == SafetyVerdict.DENY
    assert password_target.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF
    assert token_text.verdict == SafetyVerdict.DENY
    assert "sensitive" in " ".join(token_text.reasons).lower()


def test_policy_blocks_sensitive_remote_type_text():
    policy = PolicyEngine()
    fake_token = "abcdef12" + "34567890"

    review = policy.review_tool_call(
        "task_sensitive_remote",
        "step_1",
        "remote.type_text",
        {"text": f"token={fake_token}", "dry_run": True},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert review.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF
    assert "sensitive" in " ".join(review.reasons).lower()
