"""Opt-in native Notepad transfer/save acceptance using an owned text file."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid

import psutil
import pytest
from scripts.windows_agent_acceptance_fixtures import native_windows_hosts

from app.core import db
from app.perception import ui_automation as uia
from app.perception import ui_automation_com as com
from app.tools import app_tools

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MAVRIS_NATIVE_APP_ACCEPTANCE") != "1",
    reason="Set MAVRIS_NATIVE_APP_ACCEPTANCE=1 to test an installed Windows application",
)


@pytest.fixture
def notepad_document(tmp_path):
    pytest.importorskip("comtypes.client")
    if any(str(p.info["name"] or "").casefold() == "notepad.exe" for p in psutil.process_iter(["name"])):
        pytest.skip("Notepad is already running; leave the user's application untouched")
    token = uuid.uuid4().hex[:8]
    path = tmp_path / f"mavris-native-{token}.txt"
    initial_text = f"Isolated Notepad destination {token}"
    path.write_text(initial_text, encoding="utf-8", newline="")
    before = set(psutil.pids())
    started = time.time()
    launcher = subprocess.Popen([app_tools._system_executable("notepad.exe"), str(path)])
    ancestors = {launcher.pid}
    owned = {}
    windows = {}
    adapter = uia.WindowsCOMUIAutomationTarget()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = ctypes.c_int

    def capture_children():
        for process in psutil.process_iter(["name", "ppid", "create_time"]):
            if (
                str(process.info["name"] or "").casefold() == "notepad.exe"
                and process.pid not in before
                and process.info["create_time"] >= started - 0.1
                and (process.pid in ancestors or process.info["ppid"] in ancestors)
            ):
                ancestors.add(process.pid)
                owned[process.pid] = (process, process.info["create_time"])

    def owned_windows(pid):
        return [
            {"pid": pid, "hwnd": int(window.CurrentNativeWindowHandle), "title": str(window.CurrentName)}
            for window in com.process_windows(adapter._automation, pid)
        ]

    try:
        deadline = time.monotonic() + 15
        document = None
        while time.monotonic() < deadline:
            capture_children()
            for pid, (process, _) in list(owned.items()):
                if not process.is_running():
                    continue
                for window in com.run_sync(owned_windows, pid):
                    windows[window["hwnd"]] = pid
                    if path.name in window["title"]:
                        document = window
                        break
            if document:
                break
            time.sleep(0.05)
        assert document is not None, "The owned Notepad file did not acquire a visible window"
        # Titles of restored tabs are never emitted or retained in reports.
        yield {**document, "path": path, "initial_text": initial_text, "token": token}
    finally:
        for hwnd, pid in windows.items():
            process, created = owned[pid]
            if process.is_running() and process.create_time() == created:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # Close only windows created by this fixture.
        for process, created in owned.values():
            if process.is_running() and process.create_time() == created:
                try:
                    process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    # Notepad can restore existing unsaved tabs. Do not force
                    # terminate a process that refuses a graceful close.
                    pytest.fail("The owned Notepad process did not close gracefully")
        if launcher.poll() is None:
            launcher.wait(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("input_method", ["value_pattern", "keyboard"])
async def test_native_notepad_transfer_saves_exact_text_after_targeted_approval(
    notepad_document, monkeypatch, tmp_path, input_method
):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task, TaskStatus
    from app.orchestration.execution_stage import ExecutionStage
    from app.services.mobile_pairing_service import approve_approval

    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()
    target = uia.WindowsCOMUIAutomationTarget()
    document = notepad_document
    editor = {"process_id": document["pid"], "control_type": "Document", "class_name": "RichEditD2DPT"}
    own_initial_editor = {**editor, "text_contains": document["initial_text"]}
    inspection = await target.inspect_selector(own_initial_editor)
    assert inspection.get("ok"), "The temporary file's editor was not uniquely identified"

    def no_fallback(*args, **kwargs):
        raise AssertionError("Notepad text transfer must use its native ValuePattern")

    keyboard_writes = []
    if input_method == "keyboard":
        current_pattern = com.current_pattern
        send_text = uia._send_text

        def without_notepad_value_pattern(native, pattern_id, interface_name):
            if pattern_id == 10002 and str(getattr(native, "CurrentClassName", "")) == "RichEditD2DPT":
                raise RuntimeError("Exercise a document without ValuePattern")
            return current_pattern(native, pattern_id, interface_name)

        def native_keyboard_text(text, **kwargs):
            keyboard_writes.append(len(text))
            send_text(text, **kwargs)

        monkeypatch.setattr(com, "current_pattern", without_notepad_value_pattern)
        monkeypatch.setattr(uia, "_send_text", native_keyboard_text)
    else:
        monkeypatch.setattr(uia, "_send_text", no_fallback)
    monkeypatch.setattr(uia, "_send_mouse_click", no_fallback)
    source_text = f"Windows 原生保存验收 {document['token']} 😀\r\n第二行：跨应用传递。"
    with native_windows_hosts(tmp_path / "source-host", discoverable=True, lifetime_seconds=180) as hosts:
        source = hosts[0]
        com.run_sync(
            target._type_text_sync, com.run_sync(target._automation.ElementFromHandle, source["editor"]), source_text
        )
        task = Task(
            user_goal="Copy the isolated source editor into the temporary Notepad document, save it and verify the file",
            mode="efficiency",
            status=TaskStatus.REVIEWING_PLAN,
        )
        binding = {"step_id": "read-source", "path": ["value"]}
        steps = [
            PlanStep(
                id="read-source",
                agent_name="ComputerAgent",
                tool_name="ui_automation.get_property",
                description="Read source text",
                args={"process_id": source["process_id"], "name": "Fixture editor", "prop": "text"},
            ),
            PlanStep(
                id="write-notepad",
                agent_name="ComputerAgent",
                tool_name="ui_automation.type_text",
                description="Write the owned Notepad document",
                args=own_initial_editor,
                depends_on=["read-source"],
                input_bindings={"text": binding},
            ),
            PlanStep(
                id="verify-editor",
                agent_name="ComputerAgent",
                tool_name="ui_automation.get_property",
                description="Read back the same document",
                args={
                    **editor,
                    "text_contains": document["token"],
                    "prop": "text",
                    "text_comparison": "normalize_newlines",
                },
                depends_on=["read-source", "write-notepad"],
                input_bindings={"expected_value": binding},
            ),
            PlanStep(
                id="save-notepad",
                agent_name="ComputerAgent",
                tool_name="ui_automation.hotkey",
                description="Save the owned temporary file",
                args={**editor, "keys": ["ctrl", "s"]},
                depends_on=["verify-editor"],
                input_bindings={"text_contains": {"step_id": "verify-editor", "path": ["value"]}},
            ),
            PlanStep(
                id="wait-saved",
                agent_name="ComputerAgent",
                tool_name="ui_automation.wait_for_element",
                description="Wait until Notepad clears its modified title",
                args={
                    "process_id": document["pid"],
                    "name": document["title"],
                    "control_type": "Window",
                    "timeout_seconds": 5,
                },
                depends_on=["save-notepad"],
            ),
            PlanStep(
                id="verify-saved-file",
                agent_name="FileAgent",
                tool_name="file.read_text",
                description="Verify the complete saved UTF-8 file with exact line endings",
                args={"path": str(document["path"])},
                depends_on=["wait-saved", "read-source"],
                input_bindings={"expected_value": binding},
            ),
        ]
        if input_method == "keyboard":
            # Keyboard entry inserts at the current selection; selecting all
            # is a separate scoped, approved action on this owned document.
            steps[1].depends_on.append("select-own-text")
            steps.insert(
                1,
                PlanStep(
                    id="focus-own-window",
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.focus_window",
                    description="Bring the owned document window to the foreground before keyboard input",
                    args={"process_id": document["pid"], "hwnd": document["hwnd"], "title": document["title"]},
                    depends_on=["read-source"],
                ),
            )
            steps.insert(
                2,
                PlanStep(
                    id="select-own-text",
                    agent_name="ComputerAgent",
                    tool_name="ui_automation.hotkey",
                    description="Select only the owned temporary document's text",
                    args={**own_initial_editor, "keys": ["ctrl", "a"]},
                    depends_on=["focus-own-window"],
                ),
            )
        orchestrator = OrchestratorAgent()
        plan = Plan(task_id=task.id, goal=task.user_goal, steps=steps)
        for step in steps:
            step.task_id = task.id
        orchestrator.planning_handler._annotate_plan_tool_contracts(
            plan, [tool.name for tool in orchestrator.registry.list()]
        )
        db.upsert_model("tasks", task)
        db.upsert_model("plans", plan)
        approvals = ["write-notepad", "save-notepad"]
        if input_method == "keyboard":
            approvals.insert(0, "select-own-text")
        for expected in approvals:
            await orchestrator._process_steps(task, plan)
            if task.execution_stage != ExecutionStage.AWAITING_APPROVAL:
                calls = db.list_tool_calls_for_task(task.id)
                details = []
                for call in calls:
                    for result in db.fetch_many("tool_results", "tool_call_id = ?", (call["id"],)):
                        output = result.get("output") or {}
                        value = output.get("value")
                        details.append(
                            {
                                "tool": call["tool_name"],
                                "ok": result.get("ok"),
                                "error": result.get("error"),
                                "error_code": output.get("error_code"),
                                "reason": output.get("error"),
                                "value_length": len(value) if isinstance(value, str) else None,
                                "line_endings": {"cr": value.count("\r"), "lf": value.count("\n")}
                                if isinstance(value, str)
                                else None,
                            }
                        )
                pytest.fail(
                    json.dumps(
                        {
                            "expected_approval": expected,
                            "status": str(task.status),
                            "calls": details,
                        },
                        ensure_ascii=True,
                    )
                )
            pending = [
                Approval.model_validate(row)
                for row in db.fetch_many("approvals", "task_id = ?", (task.id,), limit=10)
                if row["status"] == ApprovalStatus.PENDING.value
            ]
            assert len(pending) == 1 and pending[0].step_id == expected
            assert pending[0].diff_preview.get("_resource_state")
            assert document["path"].read_text(encoding="utf-8") == document["initial_text"], (
                "File changed before Save approval"
            )
            approved = approve_approval(pending[0].id)
            task = await orchestrator.execute_approved_step(approved)
            assert db.fetch_one("approvals", approved.id)["consumed_at"]
            plan = orchestrator._latest_plan_for_task(task.id)
        await orchestrator._process_steps(task, plan)
        assert task.status == TaskStatus.COMPLETED, task.final_summary
        assert all(step.status == StepStatus.SUCCEEDED for step in plan.steps)
        with document["path"].open("r", encoding="utf-8-sig", newline="") as saved:
            assert saved.read() == source_text
        assert keyboard_writes == ([len(source_text)] if input_method == "keyboard" else [])
        # Close the now-saved temporary tab through another scoped approval;
        # closing it cannot prompt to save a different restored document.
        closing = PlanStep(
            id="close-own-tab",
            task_id=task.id,
            agent_name="ComputerAgent",
            tool_name="ui_automation.hotkey",
            description="Close the saved temporary tab",
            args={**editor, "text_contains": document["token"], "keys": ["ctrl", "w"]},
        )
        close_task = Task(
            user_goal="Close the saved temporary Notepad tab", mode="efficiency", status=TaskStatus.REVIEWING_PLAN
        )
        closing.task_id = close_task.id
        close_plan = Plan(task_id=close_task.id, goal=close_task.user_goal, steps=[closing])
        orchestrator.planning_handler._annotate_plan_tool_contracts(
            close_plan, [tool.name for tool in orchestrator.registry.list()]
        )
        db.upsert_model("tasks", close_task)
        db.upsert_model("plans", close_plan)
        await orchestrator._process_steps(close_task, close_plan)
        pending = db.fetch_many("approvals", "task_id = ?", (close_task.id,), limit=10)
        assert len(pending) == 1
        approved = approve_approval(pending[0]["id"])
        closed_task = await orchestrator.execute_approved_step(approved)
        assert closed_task.status == TaskStatus.COMPLETED, closed_task.final_summary
