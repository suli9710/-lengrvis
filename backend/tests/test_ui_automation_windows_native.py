"""Real UIA tests against two hidden, isolated WinForms host processes."""

from __future__ import annotations

import ctypes
import sys
import time
from types import SimpleNamespace

import pytest
from scripts.windows_agent_acceptance_fixtures import native_windows_hosts

from app.perception import ui_automation as uia
from app.perception import ui_automation_com as com

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native UIA integration")


@pytest.fixture
def native_hosts(tmp_path, request):
    pytest.importorskip("comtypes.client")
    with native_windows_hosts(tmp_path, discoverable=getattr(request, "param", False), lifetime_seconds=60) as hosts:
        yield hosts


def test_native_worker_is_mta_and_reentrant():
    pytest.importorskip("comtypes.client")

    def apartment():
        kind, qualifier = ctypes.c_int(), ctypes.c_int()
        ctypes.OleDLL("ole32").CoGetApartmentType(ctypes.byref(kind), ctypes.byref(qualifier))
        return kind.value

    assert com.run_sync(lambda: com.run_sync(apartment)) == 1


@pytest.mark.parametrize("native_hosts", [True], indirect=True)
def test_process_scope_finds_an_application_hosted_by_another_process(native_hosts):
    host, app = native_hosts
    target = uia.WindowsCOMUIAutomationTarget()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.SetParent.restype = ctypes.c_void_p
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    style = user32.GetWindowLongW(app["window"], -16)
    old_parent = user32.SetParent(app["window"], host["window"])
    user32.SetWindowLongW(app["window"], -16, (style & ~0x80000000) | 0x40000000)  # WS_CHILD
    try:

        def roots():
            return [
                (int(window.CurrentNativeWindowHandle), int(window.CurrentProcessId))
                for window in com.process_windows(target._automation, app["process_id"])
            ]

        assert com.run_sync(roots) == [(app["window"], app["process_id"])]
    finally:
        user32.SetParent(app["window"], old_parent)
        user32.SetWindowLongW(app["window"], -16, style)


def test_native_value_patterns_transfer_unicode_across_processes(native_hosts):
    source, destination = native_hosts
    assert source["process_id"] != destination["process_id"]
    target = uia.WindowsCOMUIAutomationTarget()
    text = "跨应用任务\r\nWindows UI Automation ✓"

    def transfer():
        source_element = target._automation.ElementFromHandle(source["editor"])
        target_element = target._automation.ElementFromHandle(destination["editor"])
        target._type_text_sync(source_element, text)
        observed = uia._native_text(source_element)
        target._type_text_sync(target_element, observed)
        return observed, uia._native_text(target_element)

    assert com.run_sync(transfer) == (text, text)


def test_native_edit_text_preserves_the_utf16_limit_and_rejects_larger_values(native_hosts):
    target = uia.WindowsCOMUIAutomationTarget()

    def check():
        editor = target._automation.ElementFromHandle(native_hosts[0]["editor"])
        for text in ("x" * 65536, "😀" * 32768):
            target._type_text_sync(editor, text)
            assert uia._native_text(editor) == text
        for text in ("x" * 65537, "😀" * 32769):
            target._type_text_sync(editor, text)
            with pytest.raises(uia.UIAutomationUnavailable, match="text limit"):
                uia._native_text(editor)

    com.run_sync(check)


def test_native_edit_read_checks_process_and_password_style(native_hosts):
    host = native_hosts[0]
    target = uia.WindowsCOMUIAutomationTarget()

    def check():
        editor = target._automation.ElementFromHandle(host["editor"])
        wrong_owner = SimpleNamespace(
            CurrentNativeWindowHandle=host["editor"],
            CurrentProcessId=host["process_id"] + 1,
            QueryInterface=editor.QueryInterface,
        )
        with pytest.raises(OSError, match="process identity"):
            com.edit_control_text(wrong_owner, 65536)
        assert com.edit_control_text(target._automation.ElementFromHandle(host["button"]), 65536) is None

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(host["editor"], -16)
        try:
            user32.SetWindowLongW(host["editor"], -16, style | 0x0020)
            # Exercise the native password guard independently of UIA's flag.
            native = target._automation.ElementFromHandle(host["editor"])
            assert com.edit_control_text(native, 65536) == ""
        finally:
            user32.SetWindowLongW(host["editor"], -16, style)

    com.run_sync(check)


def test_native_invoke_reaches_control_handler(native_hosts):
    host = native_hosts[0]
    target = uia.WindowsCOMUIAutomationTarget()

    def invoke():
        button = target._automation.ElementFromHandle(host["button"])
        target._click_sync(button)

    def read_status():
        status = target._automation.ElementFromHandle(host["status"])
        return uia._native_text(status)

    com.run_sync(invoke)
    deadline = time.monotonic() + 3
    while com.run_sync(read_status) != "Isolated source text" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert com.run_sync(read_status) == "Isolated source text"


@pytest.mark.parametrize("native_hosts", [True], indirect=True)
@pytest.mark.asyncio
async def test_native_selector_is_scoped_to_fixture_process(native_hosts):
    target = uia.WindowsCOMUIAutomationTarget()
    first, second = native_hosts
    selected = await target.find_element(
        {"process_id": first["process_id"], "name": "Fixture editor", "control_type": "Edit"}
    )

    assert selected is not None
    assert selected.process_id == first["process_id"]
    assert selected.process_id != second["process_id"]
    assert selected.properties["text"] == "Isolated source text"


@pytest.mark.parametrize("native_hosts", [True], indirect=True)
@pytest.mark.parametrize("change_after_write", [False, True])
@pytest.mark.asyncio
async def test_native_cross_app_workflow_approval_resume_and_verification(
    native_hosts, monkeypatch, tmp_path, change_after_write
):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core import db
    from app.core.schemas import Approval, Plan, PlanStep, StepStatus, Task, TaskStatus
    from app.orchestration.execution_stage import ExecutionStage
    from app.orchestration.workflow_plan import expand_workflow_steps
    from app.services.mobile_pairing_service import approve_approval

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "engine"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    db.init_db()
    source, destination = native_hosts
    adapter = uia.WindowsCOMUIAutomationTarget()
    text = "跨应用任务\r\nWindows UI Automation ✓"

    def set_fixture_text(host, value):
        element = adapter._automation.ElementFromHandle(host["editor"])
        adapter._type_text_sync(element, value)

    def destination_text():
        return uia._native_text(adapter._automation.ElementFromHandle(destination["editor"]))

    com.run_sync(set_fixture_text, source, text)
    assert com.run_sync(lambda: uia._native_text(adapter._automation.ElementFromHandle(source["editor"]))) == text
    # Only planning is omitted: the known fixture plan goes through the real
    # ComputerAgent, compiler, scheduler, approval store and native tools.
    orchestrator = OrchestratorAgent()
    task = Task(
        user_goal="Copy text between the isolated source and destination editors and verify it",
        mode="efficiency",
        status=TaskStatus.REVIEWING_PLAN,
    )
    selectors = [
        {"process_id": host["process_id"], "name": "Fixture editor", "control_type": "Edit"} for host in native_hosts
    ]
    workflow = {
        "id": "native-transfer",
        "steps": [
            {"id": "read", "action": "get_property", "ui_selector": selectors[0], "args": {"prop": "text"}},
            {
                "id": "write",
                "action": "type_text",
                "ui_selector": selectors[1],
                "depends_on": ["read"],
                "input_bindings": {"text": {"step_id": "read", "path": ["value"]}},
            },
            {
                "id": "verify",
                "action": "get_property",
                "ui_selector": selectors[1],
                "args": {"prop": "text"},
                "depends_on": ["read", "write"],
                "input_bindings": {"expected_value": {"step_id": "read", "path": ["value"]}},
            },
        ],
    }
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                id="transfer",
                task_id=task.id,
                agent_name="ComputerAgent",
                tool_name="workflow.run",
                description=task.user_goal,
                args={"workflow": workflow},
            )
        ],
    )
    visible = [tool.name for tool in orchestrator.registry.list()]
    expand_workflow_steps(plan, orchestrator.registry, visible)
    orchestrator.planning_handler._annotate_plan_tool_contracts(plan, visible)
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)

    await orchestrator._process_steps(task, plan)
    assert task.status == TaskStatus.EXECUTION, task.final_summary
    assert task.execution_stage == ExecutionStage.AWAITING_APPROVAL
    assert plan.steps[0].status == StepStatus.SUCCEEDED
    assert plan.steps[1].args["text"] == text
    assert com.run_sync(destination_text) == "Isolated source text"
    pending = db.fetch_many("approvals", "task_id = ?", (task.id,), limit=10)
    assert len(pending) == 1
    approval = Approval.model_validate(pending[0])
    assert approval.tool_name == "ui_automation.type_text"
    assert approval.args_binding_hmac
    assert approval.diff_preview

    approved = approve_approval(approval.id)
    resumed = await orchestrator.execute_approved_step(approved)
    assert resumed.status == TaskStatus.EXECUTION, resumed.final_summary
    assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
    assert com.run_sync(destination_text) == text
    assert db.fetch_one("approvals", approval.id)["consumed_at"]
    if change_after_write:
        com.run_sync(set_fixture_text, destination, "Changed by target application")

    resumed_plan = orchestrator._latest_plan_for_task(task.id)
    await orchestrator._process_steps(resumed, resumed_plan)
    # A failed postcondition enters the existing repair-required terminal
    # state: the engine refuses to claim success while leaving an ambiguous
    # external edit for the user to reconcile.
    expected_status = TaskStatus.REPAIR_REQUIRED if change_after_write else TaskStatus.COMPLETED
    assert resumed.status == expected_status, resumed.final_summary
    verification = resumed_plan.steps[-1]
    assert verification.status == (StepStatus.FAILED if change_after_write else StepStatus.SUCCEEDED)
    assert verification.args["expected_value"] == text
