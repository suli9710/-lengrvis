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
from app.policy.risk import RiskLevel, SafetyVerdict


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

    def __init__(self) -> None:
        self.invoked = False
        self.value = ""

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


class FakeAutomation:
    def __init__(self, root: FakeNative) -> None:
        self.root = root

    def GetRootElement(self):
        return self.root


@pytest.mark.asyncio
async def test_unavailable_target_gracefully_reports_actions():
    target = UnavailableUIAutomationTarget("missing provider")

    assert await target.find_element({"name": "Anything"}) is None
    assert await target.get_children({"name": "Anything"}) == []
    assert await target.click({"name": "Anything"}) == {"ok": False, "error": "missing provider", "available": False}


def test_factory_returns_graceful_target_when_provider_missing():
    target = create_ui_automation_target(policy_engine=FakePolicy())

    assert isinstance(target, (WindowsCOMUIAutomationTarget, UnavailableUIAutomationTarget))


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
