from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import pytest

from app.api import routes_ui_automation
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview
from app.perception import ui_automation as uia
from app.perception import ui_automation_actions as uia_actions
from app.perception.ui_automation import (
    UIAutomationElement,
    UnavailableUIAutomationTarget,
    WindowsCOMUIAutomationTarget,
    create_ui_automation_target,
)
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    settings_fingerprint,
)
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import ui_automation_tools
from app.tools.registry import register_all_tools
from app.tools.tool_abort import ToolAbortedError


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
        engineering_boundary={
            "risk_provenance": {
                "version": "effective-risk/v1",
                "declared_risk_level": risk_level.value,
                "effective_risk_level": risk_level.value,
                "review_id": "review_00000000000000000000000000000000",
            }
        },
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
        runtime_id: tuple[int, ...] = (1, 2, 3),
        enabled: bool = True,
        offscreen: bool = False,
        class_name: str = "Button",
        process_id: int = 42,
        native_window_handle: int = 0,
    ) -> None:
        self.CurrentName = name
        self.CurrentAutomationId = automation_id
        self.CurrentControlType = control_type
        self.CurrentClassName = class_name
        self.CurrentProcessId = process_id
        self.CurrentNativeWindowHandle = native_window_handle
        self.children = children or []
        self.runtime_id = runtime_id
        self.CurrentIsEnabled = enabled
        self.CurrentIsOffscreen = offscreen
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

    def GetRuntimeId(self):
        return self.runtime_id

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
        parents: dict[int, FakeNative] = {}

        def register_parent(parent: FakeNative) -> None:
            for child in parent.children:
                parents[id(child)] = parent
                register_parent(child)

        register_parent(root)
        self.ControlViewWalker = types.SimpleNamespace(GetParentElement=lambda element: parents.get(id(element)))

    def GetRootElement(self):
        return self.root

    def CreateTrueCondition(self):
        return object()


def _stub_process_identity(monkeypatch: pytest.MonkeyPatch, *, executable: str = "mail-client") -> None:
    monkeypatch.setattr(
        uia,
        "_process_identity_fingerprint",
        lambda process_id: {  # noqa: ARG005
            "executable_hmac": f"ui-process-executable:{executable}",
            "instance_hmac": "ui-process-instance:stable",
        },
    )


def _window_with_child(child: FakeNative, *, name: str = "Application") -> FakeNative:
    return FakeNative(
        name=name,
        automation_id="app-window",
        control_type="Window",
        class_name="AppWindow",
        native_window_handle=9001,
        runtime_id=(10, 20, 30),
        children=[child],
    )


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


def test_ui_automation_bridge_observes_tool_abort_with_low_latency() -> None:
    abort = threading.Event()

    async def slow_call() -> dict:
        await asyncio.sleep(30)
        return {"ok": True}

    timer = threading.Timer(0.05, abort.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ToolAbortedError):
            ui_automation_tools._run_ui_automation(
                slow_call(),
                "slow",
                timeout_seconds=30,
                abort_context={"_tool_abort_event": abort},
            )
    finally:
        timer.cancel()

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
@pytest.mark.parametrize("action", ["click", "type_text", "focus"])
async def test_write_actions_fail_closed_when_selector_matches_multiple_elements(action: str):
    first = FakeNative(name="Duplicate", automation_id="shared")
    second = FakeNative(name="Duplicate", automation_id="shared")
    root = FakeNative(name="Root", automation_id="root", children=[first, second])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))

    if action == "type_text":
        result = await target.type_text({"automation_id": "shared"}, "hello")
    else:
        result = await getattr(target, action)({"automation_id": "shared"})

    assert result["ok"] is False
    assert result["match_count"] == 2
    assert "matched multiple elements" in result["error"]
    assert first.invoked is False and second.invoked is False
    assert first.value == "" and second.value == ""
    assert first.focused is False and second.focused is False


@pytest.mark.asyncio
async def test_find_element_fails_closed_on_duplicates_and_inspection_returns_candidates():
    first = FakeNative(name="Duplicate", automation_id="shared", runtime_id=(1,))
    second = FakeNative(name="Duplicate", automation_id="shared", runtime_id=(2,))
    root = FakeNative(name="Root", automation_id="root", children=[first, second])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))

    found = await target.find_element({"automation_id": "shared"})
    inspection = await target.inspect_selector({"automation_id": "shared"})

    assert found is None
    assert inspection["ok"] is False
    assert inspection["match_count"] == 2
    assert len(inspection["candidates"]) == 2
    assert "multiple elements" in inspection["error"]


@pytest.mark.asyncio
async def test_selector_search_fails_closed_when_traversal_limit_is_exceeded():
    children = [FakeNative(name=f"Item {index}", automation_id=f"item_{index}") for index in range(5001)]
    children[0].CurrentAutomationId = "target"
    root = FakeNative(name="Root", automation_id="root", children=children)
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))

    found = await target.find_element({"automation_id": "target"})
    inspection = await target.inspect_selector({"automation_id": "target"})

    assert found is None
    assert inspection["ok"] is False
    assert inspection["search_truncated"] is True
    assert "traversal limit" in inspection["error"]


@pytest.mark.asyncio
async def test_element_object_action_still_rechecks_selector_uniqueness():
    first = FakeNative(name="Duplicate", automation_id="shared", runtime_id=(1,))
    second = FakeNative(name="Duplicate", automation_id="shared", runtime_id=(2,))
    root = FakeNative(name="Root", automation_id="root", children=[first, second])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))
    first_element = uia._element_from_native(first)

    result = await target.click(first_element)

    assert result["ok"] is False
    assert result["match_count"] == 2
    assert first.invoked is False and second.invoked is False


def test_semantic_preview_binds_unique_target_resource_state(monkeypatch):
    native = FakeNative(runtime_id=(7, 8, 9))
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(), automation=FakeAutomation(_window_with_child(native))
    )
    _stub_process_identity(monkeypatch)
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )

    preview = ui_automation_tools.click({"automation_id": "send_button", "dry_run": True}, {})

    assert preview["ok"] is True
    resource_state = preview["_resource_state"][0]
    assert resource_state["kind"] == "ui_automation_element"
    assert resource_state["identity_version"] == "ui-automation-identity/v2"
    assert resource_state["selector_hmac"].startswith("ui-selector:")
    assert resource_state["fingerprint"]["identity_hmac"].startswith("ui-element:")
    assert "send_button" not in str(resource_state)
    assert "send" not in str(resource_state).casefold()
    target_window = resource_state["target_window"]
    assert target_window["executable_hmac"].startswith("ui-process-executable:")
    assert target_window["parent_chain_hmac"].startswith("ui-parent-chain:")
    assert target_window["parent_chain_depth"] == 1


def test_process_identity_fingerprint_never_persists_executable_path(monkeypatch):
    private_path = r"C:\Users\Alice\Private Workspace\mail.exe"

    class FakeProcess:
        def create_time(self) -> float:
            return 1234.5

        def exe(self) -> str:
            return private_path

    monkeypatch.setattr(uia.psutil, "Process", lambda process_id: FakeProcess())  # noqa: ARG005

    identity = uia._process_identity_fingerprint(42)

    assert identity is not None
    assert identity["executable_hmac"].startswith("ui-process-executable:")
    assert identity["instance_hmac"].startswith("ui-process-instance:")
    assert "alice" not in str(identity).casefold()
    assert "private workspace" not in str(identity).casefold()
    assert "mail.exe" not in str(identity).casefold()


def test_process_identity_fingerprint_fails_closed_on_pid_reuse(monkeypatch):
    class ReusedProcess:
        def __init__(self) -> None:
            self.reads = 0

        def create_time(self) -> float:
            self.reads += 1
            return 100.0 if self.reads == 1 else 200.0

        def exe(self) -> str:
            return r"C:\Apps\mail.exe"

    monkeypatch.setattr(uia.psutil, "Process", lambda process_id: ReusedProcess())  # noqa: ARG005

    assert uia._process_identity_fingerprint(42) is None


@pytest.mark.parametrize("process_id", [None, 0, -1, "42"])
def test_process_identity_fingerprint_rejects_invalid_pid(process_id):
    assert uia._process_identity_fingerprint(process_id) is None


@pytest.mark.parametrize("action", ["click", "type_text"])
def test_semantic_write_preview_fails_closed_without_process_identity(monkeypatch, action: str):
    button = FakeNative()
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(), automation=FakeAutomation(_window_with_child(button))
    )
    monkeypatch.setattr(uia, "_process_identity_fingerprint", lambda process_id: None)  # noqa: ARG005
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )
    payload = {"automation_id": "send_button", "dry_run": True}
    if action == "type_text":
        payload["text"] = "hello"

    preview = getattr(ui_automation_tools, action)(payload, {})

    assert preview["ok"] is False
    assert "could not be proven" in preview["error"]
    assert "_resource_state" not in preview


def test_semantic_write_preview_fails_closed_when_parent_chain_walk_fails(monkeypatch):
    button = FakeNative()
    automation = FakeAutomation(_window_with_child(button))
    automation.ControlViewWalker.GetParentElement = lambda element: (_ for _ in ()).throw(  # noqa: ARG005
        RuntimeError("stale COM parent")
    )
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=automation)
    _stub_process_identity(monkeypatch)
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )

    preview = ui_automation_tools.click({"automation_id": "send_button", "dry_run": True}, {})

    assert preview["ok"] is False
    assert "could not be proven" in preview["error"]
    assert "stale COM parent" not in str(preview)
    assert "_resource_state" not in preview


def test_runtime_id_lookup_failure_degrades_without_dropping_element():
    class RuntimeIdFailingNative(FakeNative):
        def GetRuntimeId(self):
            raise RuntimeError("COM identity unavailable")

    element = uia._element_from_native(RuntimeIdFailingNative())

    assert element.automation_id == "send_button"
    assert "runtime_id" not in element.properties


def test_stale_runtime_id_collection_degrades_without_dropping_element():
    class StaleRuntimeId:
        Length = 1

        def GetElement(self, index: int):  # noqa: ARG002
            raise RuntimeError("stale COM collection")

    class StaleRuntimeIdNative(FakeNative):
        def GetRuntimeId(self):
            return StaleRuntimeId()

    element = uia._element_from_native(StaleRuntimeIdNative())

    assert element.automation_id == "send_button"
    assert "runtime_id" not in element.properties


def test_semantic_preview_rejects_ambiguous_target(monkeypatch):
    first = FakeNative(name="Duplicate", automation_id="shared")
    second = FakeNative(name="Duplicate", automation_id="shared")
    root = FakeNative(name="Root", automation_id="root", children=[first, second])
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(root))
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )

    preview = ui_automation_tools.click({"automation_id": "shared", "dry_run": True}, {})

    assert preview["ok"] is False
    assert preview["match_count"] == 2
    assert "multiple elements" in preview["error"]


def test_direct_gui_approval_detects_target_replacement(monkeypatch):
    original = FakeNative(runtime_id=(1, 2, 3))
    replacement = FakeNative(runtime_id=(9, 9, 9))
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(), automation=FakeAutomation(_window_with_child(original))
    )
    _stub_process_identity(monkeypatch)
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )
    payload = {"automation_id": "send_button", "dry_run": True}
    preview = ui_automation_tools.click(payload, {})
    approval = Approval(
        task_id="task_gui_target_state",
        message="Approve semantic click",
        tool_name="ui_automation.click",
        diff_preview=binding_preview(preview),
    )
    target._automation = FakeAutomation(_window_with_child(replacement))

    error = routes_ui_automation._approval_resource_state_error(
        approval,
        "ui_automation.click",
        {**payload, "dry_run": False},
        {"settings": None, "allowed_directories": []},
    )

    assert "no longer matches" in error


def test_direct_gui_approval_detects_owning_window_account_change(monkeypatch):
    button = FakeNative(runtime_id=(1, 2, 3))
    window = FakeNative(
        name="Inbox - alice@example.test",
        automation_id="mail-window",
        control_type="Window",
        class_name="MailWindow",
        native_window_handle=9001,
        runtime_id=(10, 20, 30),
        children=[button],
    )
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(window))
    _stub_process_identity(monkeypatch)
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )
    payload = {"automation_id": "send_button", "dry_run": True}
    preview = ui_automation_tools.click(payload, {})
    resource_state = preview["_resource_state"][0]
    assert resource_state["target_window"]["identity_hmac"].startswith("ui-window:")
    assert "alice@example.test" not in str(resource_state)
    approval = Approval(
        task_id="task_gui_window_account_state",
        message="Approve semantic click in the reviewed account window",
        tool_name="ui_automation.click",
        diff_preview=binding_preview(preview),
    )

    window.CurrentName = "Inbox - bob@example.test"
    error = routes_ui_automation._approval_resource_state_error(
        approval,
        "ui_automation.click",
        {**payload, "dry_run": False},
        {"settings": None, "allowed_directories": []},
    )

    assert "no longer matches" in error


def test_direct_gui_approval_detects_private_parent_chain_change(monkeypatch):
    button = FakeNative(runtime_id=(1, 2, 3))
    container = FakeNative(
        name="Alice confidential workspace",
        automation_id="workspace-alice",
        control_type="Pane",
        class_name="WorkspacePane",
        runtime_id=(4, 5, 6),
        children=[button],
    )
    window = _window_with_child(container)
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(window))
    _stub_process_identity(monkeypatch)
    monkeypatch.setattr(
        ui_automation_tools,
        "create_ui_automation_target",
        lambda policy_engine=None, approval_context=None: target,  # noqa: ARG005
    )
    payload = {"automation_id": "send_button", "dry_run": True}
    preview = ui_automation_tools.click(payload, {})
    resource_state = preview["_resource_state"][0]
    assert resource_state["target_window"]["parent_chain_depth"] == 2
    assert "alice confidential" not in str(resource_state).casefold()
    assert "workspace-alice" not in str(resource_state).casefold()
    approval = Approval(
        task_id="task_gui_parent_chain_state",
        message="Approve semantic click in the reviewed workspace",
        tool_name="ui_automation.click",
        diff_preview=binding_preview(preview),
    )

    container.CurrentAutomationId = "workspace-bob"
    error = routes_ui_automation._approval_resource_state_error(
        approval,
        "ui_automation.click",
        {**payload, "dry_run": False},
        {"settings": None, "allowed_directories": []},
    )

    assert "no longer matches" in error


def test_direct_gui_approval_refresh_failure_is_redacted_and_fails_closed(monkeypatch):
    def fail_refresh(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("provider failed with token=secret-token-1234567890")

    monkeypatch.setattr(
        routes_ui_automation,
        "_tool_definition",
        lambda tool_name: types.SimpleNamespace(execute=fail_refresh),  # noqa: ARG005
    )
    approval = Approval(
        task_id="task_gui_refresh_failure",
        message="Approve semantic click",
        tool_name="ui_automation.click",
        diff_preview={"_resource_state": [{"kind": "ui_automation_element"}]},
    )

    error = routes_ui_automation._approval_resource_state_error(
        approval,
        "ui_automation.click",
        {"automation_id": "send_button", "dry_run": False},
        {"settings": None, "allowed_directories": []},
    )

    assert error.startswith("Could not refresh the approved GUI target state:")
    assert "secret-token-1234567890" not in error
    assert "[REDACTED]" in error


@pytest.mark.asyncio
async def test_click_revalidates_runtime_identity_and_blocks_replaced_target():
    original = FakeNative(runtime_id=(1, 2, 3))
    replacement = FakeNative(runtime_id=(9, 9, 9))

    class ReplacingAutomation(FakeAutomation):
        def __init__(self) -> None:
            self.calls = 0

        def GetRootElement(self):
            self.calls += 1
            return original if self.calls == 1 else replacement

    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=ReplacingAutomation())

    result = await target.click({"automation_id": "send_button"})

    assert result["ok"] is False
    assert "changed before execution" in result["error"]
    assert original.invoked is False
    assert replacement.invoked is False


@pytest.mark.asyncio
async def test_click_revalidates_approved_window_account_at_action_boundary(monkeypatch):
    button = FakeNative(runtime_id=(1, 2, 3))
    window = FakeNative(
        name="Inbox - alice@example.test",
        automation_id="mail-window",
        control_type="Window",
        class_name="MailWindow",
        native_window_handle=9001,
        runtime_id=(10, 20, 30),
        children=[button],
    )
    automation = FakeAutomation(window)
    _stub_process_identity(monkeypatch)
    preview_target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=automation)
    reviewed = await preview_target.inspect_selector({"automation_id": "send_button"})
    approval_context = {"_expected_resource_state": [reviewed["resource_state"]]}
    mark_execution_approved(approval_context)
    window.CurrentName = "Inbox - bob@example.test"
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(),
        automation=automation,
        approval_context=approval_context,
    )

    result = await target.click(
        {"automation_id": "send_button"},
        task_id="task_window_boundary",
        approved=True,
        approval_id="approval_window_boundary",
    )

    assert result["ok"] is False
    assert "window or account context changed" in result["error"]
    assert button.invoked is False


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["click", "type_text"])
@pytest.mark.parametrize("changed_identity", ["process", "parent_chain"])
async def test_semantic_write_revalidates_identity_at_final_action_boundary(
    monkeypatch,
    action: str,
    changed_identity: str,
):
    button = FakeNative(runtime_id=(1, 2, 3))
    container = FakeNative(
        name="Reviewed workspace",
        automation_id="workspace-reviewed",
        control_type="Pane",
        class_name="WorkspacePane",
        runtime_id=(4, 5, 6),
        children=[button],
    )
    automation = FakeAutomation(_window_with_child(container))
    identity_reads = 0

    def process_identity(process_id):  # noqa: ANN001, ANN202, ARG001
        nonlocal identity_reads
        identity_reads += 1
        if identity_reads == 3 and changed_identity == "parent_chain":
            container.CurrentAutomationId = "workspace-replaced"
        executable = "replaced" if identity_reads == 3 and changed_identity == "process" else "reviewed"
        return {
            "executable_hmac": f"ui-process-executable:{executable}",
            "instance_hmac": "ui-process-instance:stable",
        }

    monkeypatch.setattr(uia, "_process_identity_fingerprint", process_identity)
    preview_target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=automation)
    reviewed = await preview_target.inspect_selector({"automation_id": "send_button"})
    approval_context = {"_expected_resource_state": [reviewed["resource_state"]]}
    mark_execution_approved(approval_context)
    target = WindowsCOMUIAutomationTarget(
        policy_engine=FakePolicy(),
        automation=automation,
        approval_context=approval_context,
    )

    action_args = {
        "task_id": "task_final_identity_boundary",
        "approved": True,
        "approval_id": "approval_final_identity_boundary",
    }
    if action == "click":
        result = await target.click({"automation_id": "send_button"}, **action_args)
    else:
        result = await target.type_text({"automation_id": "send_button"}, "private text", **action_args)

    assert identity_reads == 3
    assert result["ok"] is False
    assert "process, parent chain" in result["error"]
    assert button.invoked is False
    assert button.value == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "offscreen", "expected_error"),
    [(False, False, "disabled"), (True, True, "offscreen")],
)
async def test_click_blocks_disabled_or_offscreen_target(enabled: bool, offscreen: bool, expected_error: str):
    native = FakeNative(enabled=enabled, offscreen=offscreen)
    target = WindowsCOMUIAutomationTarget(policy_engine=FakePolicy(), automation=FakeAutomation(native))

    result = await target.click({"automation_id": "send_button"})

    assert result["ok"] is False
    assert expected_error in result["error"]
    assert native.invoked is False


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
        uia_actions,
        "list_windows",
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


def test_direct_ui_adapter_routes_block_before_adapter_when_dynamic_risk_requires_approval(monkeypatch):
    adapter_calls: list[str] = []
    review_calls: list[str] = []
    adapter_names = {
        "active_window",
        "observe",
        "find_element",
        "wait_for_element",
        "list_windows",
        "screenshot",
        "get_property",
        "get_children",
    }
    for adapter_name in adapter_names:
        monkeypatch.setattr(
            routes_ui_automation.ui_automation_tools,
            adapter_name,
            lambda _payload, _context, name=adapter_name: adapter_calls.append(name) or {"ok": True},
        )

    def require_approval(tool_name: str, _payload: dict, _context: dict) -> SafetyReview:
        review_calls.append(tool_name)
        return SafetyReview(
            task_id="direct_ui_automation_api",
            target_type="tool_call",
            verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            declared_risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Authoritative dynamic risk increased this direct adapter call."],
        )

    monkeypatch.setattr(routes_ui_automation, "_review_tool_call", require_approval)
    cases = [
        ("ui_automation.active_window", routes_ui_automation.active_window),
        ("ui_automation.observe", lambda: routes_ui_automation.observe({"max_depth": 1})),
        ("ui_automation.find_element", lambda: routes_ui_automation.find_element({"name": "Send"})),
        (
            "ui_automation.wait_for_element",
            lambda: routes_ui_automation.wait_for_element({"name": "Send"}),
        ),
        ("ui_automation.list_windows", routes_ui_automation.list_windows),
        ("ui_automation.screenshot", lambda: routes_ui_automation.screenshot({"quality": 70})),
        (
            "ui_automation.get_property",
            lambda: routes_ui_automation.get_property({"name": "Send", "property": "is_enabled"}),
        ),
        ("ui_automation.get_children", lambda: routes_ui_automation.get_children({"name": "Window"})),
    ]

    for expected_tool, invoke in cases:
        result = invoke()
        assert result["status"] == "requires_approval"
        assert review_calls[-1] == expected_tool

    assert adapter_calls == []


def test_direct_ui_permission_denial_never_calls_adapter(monkeypatch):
    adapter_calls: list[dict] = []
    monkeypatch.setattr(
        routes_ui_automation.ui_automation_tools,
        "screenshot",
        lambda payload, _context: adapter_calls.append(payload) or {"ok": True},
    )
    monkeypatch.setattr(
        routes_ui_automation,
        "_review_tool_call",
        lambda *_args: SafetyReview(
            task_id="direct_ui_automation_api",
            target_type="tool_call",
            verdict=SafetyVerdict.DENY,
            risk_level=RiskLevel.R0_READ_ONLY,
            declared_risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Permission policy denied ui_automation.screenshot."],
        ),
    )

    result = routes_ui_automation.screenshot({"quality": 70})

    assert result["status"] == "denied"
    assert adapter_calls == []


def test_direct_ui_open_only_action_blocks_before_adapter_when_dynamic_risk_requires_approval(monkeypatch):
    adapter_calls: list[dict] = []
    tool = routes_ui_automation._tool_definition("ui_automation.focus")
    monkeypatch.setattr(
        tool,
        "execute",
        lambda payload, _context: adapter_calls.append(payload) or {"ok": True},
    )
    monkeypatch.setattr(
        routes_ui_automation,
        "_review_tool_call",
        lambda *_args: SafetyReview(
            task_id="direct_ui_automation_api",
            target_type="tool_call",
            verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            declared_risk_level=RiskLevel.R1_OPEN_ONLY,
            reasons=["Dynamic risk increased UI focus."],
        ),
    )

    result = routes_ui_automation.action({"action": "focus", "name": "Editor"})

    assert result["status"] == "requires_approval"
    assert adapter_calls == []


def test_direct_ui_result_uses_shared_runtime_finalizer(monkeypatch):
    db.init_db()
    monkeypatch.setattr(
        routes_ui_automation.ui_automation_tools,
        "screenshot",
        lambda _payload, _context: {
            "ok": True,
            "image": "safe-inline-image",
            "outcome_unknown": True,
            "persisted_result": True,
            "post_tool_review_id": "review_forged",
            "post_tool_review_verdict": SafetyVerdict.DENY.value,
            "automatic_replay_available": True,
            "direct_result_journaled": True,
            "changed_paths": [r"C:\workspace\forged.png"],
            "rollback_info": {"trash_created_file": r"C:\workspace\forged.png"},
        },
    )
    monkeypatch.setattr(
        routes_ui_automation,
        "_review_tool_call",
        lambda *_args: SafetyReview(
            task_id="direct_ui_automation_api",
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            declared_risk_level=RiskLevel.R0_READ_ONLY,
        ),
    )

    result = routes_ui_automation.screenshot({"quality": 70})

    assert result["ok"] is True
    assert result["post_tool_review_id"] != "review_forged"
    assert result["post_tool_review_verdict"] == SafetyVerdict.ALLOW.value
    assert result["automatic_replay_available"] is False
    assert result["direct_result_journaled"] is False
    assert result["changed_paths"] == []
    assert result["rollback_info"] == {"_runtime_evidence_status": "invalid"}
    assert "outcome_unknown" not in result
    assert "persisted_result" not in result


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
