"""Opt-in acceptance against the installed Windows Calculator application."""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from app.core import db
from app.core.schemas import TaskStatus
from app.perception import ui_automation as uia
from app.perception import ui_automation_com as com
from app.tools import app_tools

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MAVRIS_NATIVE_APP_ACCEPTANCE") != "1",
    reason="Set MAVRIS_NATIVE_APP_ACCEPTANCE=1 to test an installed Windows application",
)


def _owned_window_diagnostics(pid):
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    owned = []

    def owner(hwnd):
        result = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(result))
        return result.value

    @callback_type
    def collect_root(hwnd, _):
        root_owner = owner(hwnd)
        if root_owner == pid:
            owned.append({"hwnd": hwnd, "parent_pid": pid, "visible": bool(user32.IsWindowVisible(hwnd))})

        @callback_type
        def collect_child(child, _):
            if owner(child) == pid:
                owned.append({"hwnd": child, "parent_pid": root_owner, "visible": bool(user32.IsWindowVisible(child))})
            return True

        user32.EnumChildWindows(hwnd, collect_child, 0)
        return True

    user32.EnumWindows(collect_root, 0)
    return owned


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("normal_task_entry", "transfer_result"),
    [(False, False), (True, False), (False, True)],
    ids=["direct-launch", "normal-entry-launch", "calculation-and-transfer"],
)
async def test_store_calculator_native_task(monkeypatch, tmp_path, normal_task_entry, transfer_result):
    pytest.importorskip("comtypes.client")
    before = set(psutil.pids())
    for process in psutil.process_iter(["name"]):
        if str(process.info["name"] or "").casefold() in {"calculator.exe", "calculatorapp.exe"}:
            pytest.skip("Calculator is already running; leave the user's application untouched")
    db.init_db()
    started = time.time()
    process = None
    creation_time = None
    windows = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int
    activate = app_tools._activate_packaged_app

    def capture_owned_activation(app_id):
        nonlocal process, creation_time
        pid = activate(app_id)
        assert isinstance(pid, int) and pid > 0 and pid not in before
        candidate = psutil.Process(pid)
        assert candidate.create_time() >= started - 0.1
        assert candidate.name().casefold() in {"calculator.exe", "calculatorapp.exe"}
        process, creation_time = candidate, candidate.create_time()
        return pid

    monkeypatch.setattr(app_tools, "_activate_packaged_app", capture_owned_activation)
    try:
        if normal_task_entry:
            from app.agents.orchestrator_agent import OrchestratorAgent

            monkeypatch.setenv("LENGRVIS_APP_ALLOWLIST", "microsoft.windowscalculator")
            monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
            monkeypatch.setenv("LENGRVIS_API_KEY", "")
            monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
            # Exercise the existing deterministic open-app intent, not LLM planning.
            task = await OrchestratorAgent().handle_user_goal("打开 Microsoft.WindowsCalculator", "efficiency")
            assert task.status == TaskStatus.COMPLETED, task.final_summary
            calls = [
                call for call in db.list_tool_calls_for_task(task.id) if call["tool_name"] == "app.launch_installed"
            ]
            assert len(calls) == 1
            results = db.fetch_many("tool_results", "tool_call_id = ?", (calls[0]["id"],))
            assert len(results) == 1 and results[0]["ok"]
            result = results[0]["output"]
        else:
            result = app_tools.launch_installed(
                {"app": "Microsoft.WindowsCalculator"},
                {"settings": SimpleNamespace(app_allowlist=["microsoft.windowscalculator"])},
            )
        assert result["ok"], result
        pid = result["process_id"]
        assert process is not None and pid == process.pid
        target = uia.WindowsCOMUIAutomationTarget()

        def scoped_windows():
            return [
                {"hwnd": int(window.CurrentNativeWindowHandle), "process_id": int(window.CurrentProcessId)}
                for window in com.process_windows(target._automation, pid)
            ]

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            windows = com.run_sync(scoped_windows)
            if windows:
                break
            time.sleep(0.1)
        assert windows, f"No scoped window: alive={process.is_running()}, owned={_owned_window_diagnostics(pid)}"
        assert all(window["process_id"] == pid and window["hwnd"] for window in windows)
        if transfer_result:
            await _exercise_calculation_and_transfer(pid, monkeypatch, tmp_path)
    finally:
        # Only the newly activated process belongs to this test. Never close an
        # existing instance or a reused PID, including after an assertion fails.
        if process is not None and process.is_running() and process.create_time() == creation_time:
            for window in windows:
                user32.PostMessageW(window["hwnd"], 0x0010, 0, 0)  # WM_CLOSE
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)


async def _exercise_calculation_and_transfer(calculator_pid, monkeypatch, tmp_path):
    from scripts.windows_agent_acceptance_fixtures import native_windows_hosts

    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task
    from app.orchestration.execution_stage import ExecutionStage
    from app.services.mobile_pairing_service import approve_approval

    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")

    # This acceptance proves native Invoke/Value operations. A missing native
    # pattern must not send pointer/keyboard input to the user's foreground app.
    def unexpected_fallback(*args, **kwargs):
        raise AssertionError("Native application acceptance cannot use global input fallback")

    monkeypatch.setattr(uia, "_send_mouse_click", unexpected_fallback)
    monkeypatch.setattr(uia, "_send_text", unexpected_fallback)
    target = uia.WindowsCOMUIAutomationTarget()
    display = {"process_id": calculator_pid, "automation_id": "CalculatorResults", "control_type": "Text"}
    buttons = ["clearButton", "num3Button", "num7Button", "multiplyButton", "num1Button", "num9Button", "equalButton"]
    for button in buttons:
        assert await target.find_element({"process_id": calculator_pid, "automation_id": button}) is not None

    with native_windows_hosts(tmp_path / "transfer", discoverable=True, lifetime_seconds=180) as hosts:
        destination = hosts[0]
        editor = {"process_id": destination["process_id"], "name": "Fixture editor", "control_type": "Edit"}

        def destination_text():
            return uia._native_text(target._automation.ElementFromHandle(destination["editor"]))

        initial_text = com.run_sync(destination_text)
        task = Task(
            user_goal="Calculate 37 times 19 in Calculator, copy its displayed text into the isolated editor and verify it",
            mode="efficiency",
            status=TaskStatus.REVIEWING_PLAN,
        )
        steps = []
        for index, button in enumerate(buttons):
            steps.append(
                PlanStep(
                    id=f"button-{index}",
                    task_id=task.id,
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.click",
                    description=f"Invoke Calculator {button}",
                    args={"process_id": calculator_pid, "automation_id": button, "control_type": "Button"},
                    depends_on=[steps[-1].id] if steps else [],
                )
            )
        steps.extend(
            [
                PlanStep(
                    id="read",
                    task_id=task.id,
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.get_property",
                    description="Read Calculator result",
                    args={**display, "prop": "text"},
                    depends_on=[steps[-1].id],
                ),
                PlanStep(
                    id="write",
                    task_id=task.id,
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.type_text",
                    description="Copy Calculator display to the isolated editor",
                    args=editor,
                    depends_on=["read"],
                    input_bindings={"text": {"step_id": "read", "path": ["value"]}},
                ),
                PlanStep(
                    id="verify",
                    task_id=task.id,
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.get_property",
                    description="Verify the destination matches Calculator",
                    args={**editor, "prop": "text"},
                    depends_on=["read", "write"],
                    input_bindings={"expected_value": {"step_id": "read", "path": ["value"]}},
                ),
            ]
        )
        # Only the plan is supplied. The real worker, policy, approval store,
        # parameter binding and native UIA tools execute every step.
        orchestrator = OrchestratorAgent()
        plan = Plan(task_id=task.id, goal=task.user_goal, steps=steps)
        orchestrator.planning_handler._annotate_plan_tool_contracts(
            plan, [tool.name for tool in orchestrator.registry.list()]
        )
        db.upsert_model("tasks", task)
        db.upsert_model("plans", plan)
        approvals = []
        for index in range(len(buttons) + 2):
            await orchestrator._process_steps(task, plan)
            if task.status == TaskStatus.COMPLETED:
                break
            assert task.execution_stage == ExecutionStage.AWAITING_APPROVAL, task.final_summary
            pending = [
                Approval.model_validate(row)
                for row in db.fetch_many("approvals", "task_id = ?", (task.id,), limit=32)
                if row["status"] == ApprovalStatus.PENDING.value
            ]
            assert len(pending) == 1
            approval = pending[0]
            expected_step = f"button-{index}" if index < len(buttons) else "write"
            assert approval.step_id == expected_step
            assert approval.args_binding_hmac and approval.diff_preview
            if expected_step == "write":
                assert com.run_sync(destination_text) == initial_text
                value = await target.get_property(display, "text")
                assert re.search(r"(?<!\d)703\s*$", value), value
                assert next(step for step in plan.steps if step.id == "write").args["text"] == value
            approved = approve_approval(approval.id)
            task = await orchestrator.execute_approved_step(approved)
            approvals.append(approved.id)
            assert db.fetch_one("approvals", approved.id)["consumed_at"]
            plan = orchestrator._latest_plan_for_task(task.id)
        assert task.status == TaskStatus.COMPLETED, task.final_summary
        assert len(approvals) == len(buttons) + 1
        assert all(step.status == StepStatus.SUCCEEDED for step in plan.steps)
        source_text = await target.get_property(display, "text")
        assert com.run_sync(destination_text) == source_text
        assert re.search(r"(?<!\d)703\s*$", source_text), source_text
