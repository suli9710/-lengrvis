from __future__ import annotations

import pytest

from app.core.schemas import SafetyReview
from app.perception.ui_automation import (
    UIAutomationElement,
    UIAutomationSelector,
    UnavailableUIAutomationTarget,
    WindowsCOMUIAutomationTarget,
    create_ui_automation_target,
)
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
        children: list["FakeNative"] | None = None,
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


@pytest.mark.asyncio
async def test_unavailable_target_gracefully_reports_actions():
    target = UnavailableUIAutomationTarget("missing provider")

    assert await target.find_element({"name": "Anything"}) is None
    assert await target.get_children({"name": "Anything"}) == []
    assert await target.wait_for_element({"name": "Anything"}, timeout_seconds=0) is None
    assert await target.click({"name": "Anything"}) == {"ok": False, "error": "missing provider", "available": False}
    assert await target.observe() == {"ok": False, "error": "missing provider", "available": False, "elements": [], "count": 0}


def test_factory_returns_graceful_target_when_provider_missing():
    target = create_ui_automation_target(policy_engine=FakePolicy())

    assert isinstance(target, (WindowsCOMUIAutomationTarget, UnavailableUIAutomationTarget))


@pytest.mark.asyncio
async def test_windows_adapter_finds_and_clicks_native_element_with_policy():
    native = FakeNative()
    policy = FakePolicy()
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(native))

    found = await target.find_element(automation_id="send_button")
    clicked = await target.click(found, task_id="task_1", step_id="step_1", approved=True, approval_id="approval_1")

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
    monkeypatch.setattr("app.perception.ui_automation.get_current_app_context", lambda: type("FakeContext", (), {"available": True, "model_dump": lambda self, mode="json": {"available": True}})())

    observed = await target.observe(max_depth=1, max_elements=10)

    assert observed["ok"] is True
    assert observed["count"] == 2
    assert observed["root"]["children"][0]["name"] == "Cancel"


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
async def test_approved_keyboard_action_bypasses_policy_wait(monkeypatch):
    policy = FakePolicy(SafetyVerdict.NEEDS_USER_APPROVAL)
    target = WindowsCOMUIAutomationTarget(policy_engine=policy, automation=FakeAutomation(FakeNative()))
    pressed = []
    monkeypatch.setattr("app.perception.ui_automation._press_key", lambda key: pressed.append(key))

    blocked = await target.key_press("enter")
    allowed = await target.key_press("enter", approved=True, approval_id="approval_2")

    assert blocked["approval_required"] is True
    assert allowed["ok"] is True
    assert pressed == ["enter"]


@pytest.mark.asyncio
async def test_approved_gui_action_still_honors_policy_denial():
    native = FakeNative()
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(SafetyVerdict.DENY), automation=FakeAutomation(native))

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
        {"name": "Notes", "text": "token=abcdef1234567890", "dry_run": True},
        RiskLevel.R2_REVERSIBLE_MODIFY,
    )

    assert password_target.verdict == SafetyVerdict.DENY
    assert password_target.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF
    assert token_text.verdict == SafetyVerdict.DENY
    assert "sensitive" in " ".join(token_text.reasons).lower()
