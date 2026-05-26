from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.agents.orchestrator_agent as orchestrator_module
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import AgentAction, Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.main import create_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import set_step_status
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.services import task_recording_service
from app.services.task_recording_service import list_recording_frames, persist_recording_frame, read_recording_image
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    db.init_db()
    register_all_tools()
    yield


@pytest.fixture
def fake_capture(monkeypatch: pytest.MonkeyPatch):
    counter = 0

    def capture(task_id: str, step_id: str, phase: str):
        nonlocal counter
        counter += 1
        file_name = f"{step_id}-{phase}.png"
        frame = {
            "kind": "step_screenshot",
            "task_id": task_id,
            "step_id": step_id,
            "phase": phase,
            "ok": True,
            "enabled": True,
            "captured_at": f"2026-05-25T00:00:{counter:02d}+00:00",
            "file_name": file_name,
            "path": "",
            "url": f"/api/tasks/{task_id}/recordings/{file_name}",
            "mime_type": "image/png",
            "width": 1,
            "height": 1,
            "error": "",
        }
        recording_id = persist_recording_frame(frame, _tiny_png())
        return {**frame, "recording_id": recording_id}

    monkeypatch.setattr(orchestrator_module, "capture_step_screenshot", capture)
    return capture


class PassthroughAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return AgentAction(kind="propose_tool", tool_name=step.tool_name, args=dict(step.args))

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "ok"


def _tool(name: str, calls: list[dict[str, Any]], *, risk: RiskLevel = RiskLevel.R0_READ_ONLY):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        calls.append(dict(args))
        return {"ok": True}

    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="FileAgent",
        supports_dry_run=risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM},
        requires_authorized_path=False,
        execute=execute,
        effects=["read"],
        resource_kinds=["test"],
        fast_path_eligible=True,
        trust_tier="builtin",
    )


def _task_and_plan(tool_name: str, *, risk: RiskLevel = RiskLevel.R0_READ_ONLY):
    task = Task(user_goal="record this step", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name=tool_name,
        description="Record step",
        args={},
        risk_level=risk,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    return task, plan, step


def test_step_execution_records_before_and_after_screenshots(fake_capture):
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_tool("test.recording", calls))
    task, plan, step = _task_and_plan("test.recording")

    asyncio.run(orchestrator._process_steps(task, plan))

    messages = orchestrator.bus.get_messages(task.id)
    payloads = [m.structured_payload for m in messages if m.structured_payload.get("kind") == "step_screenshot"]
    assert len(payloads) == 1
    assert payloads[0]["step_id"] == step.id
    assert [frame["phase"] for frame in payloads[0]["frames"]] == ["before", "after"]


def test_timeline_exposes_recordings_and_image_route(fake_capture):
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_tool("test.recording_route", calls))
    task, plan, _step = _task_and_plan("test.recording_route")

    asyncio.run(orchestrator._process_steps(task, plan))

    client = TestClient(create_app())
    timeline = client.get(f"/api/tasks/{task.id}/timeline")
    assert timeline.status_code == 200
    recordings = timeline.json()["recordings"]
    assert recordings
    frames = recordings[0]["frames"]
    assert {frame["phase"] for frame in frames} == {"before", "after"}

    image = client.get(frames[0]["url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")


def test_recording_frames_are_stored_as_sqlite_blobs(fake_capture):
    task_id = "task_blob"
    step_id = "step_blob"

    before = fake_capture(task_id, step_id, "before")
    after = fake_capture(task_id, step_id, "after")

    frames = list_recording_frames(task_id)
    assert [frame["phase"] for frame in frames] == ["before", "after"]
    assert frames[0]["recording_id"] == before["recording_id"]
    assert frames[1]["recording_id"] == after["recording_id"]

    image, mime_type = read_recording_image(task_id, before["file_name"])
    assert mime_type == "image/png"
    assert image == _tiny_png()


def test_capture_step_screenshot_persists_png_blob(monkeypatch: pytest.MonkeyPatch):
    from PIL import Image

    monkeypatch.setenv("MARVIS_TASK_RECORDING_FORCE", "1")
    monkeypatch.setattr(task_recording_service, "_grab_screen", lambda: Image.new("RGB", (2, 1), "red"))

    frame = task_recording_service.capture_step_screenshot("task_capture", "step_capture", "before")

    assert frame["ok"] is True
    assert frame["recording_id"]
    assert frame["width"] == 2
    assert frame["height"] == 1
    image, mime_type = read_recording_image("task_capture", frame["file_name"])
    assert mime_type == "image/png"
    assert image.startswith(b"\x89PNG")


def test_approved_step_records_approved_before_and_after(fake_capture):
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_tool("test.approved_recording", calls))
    task, plan, step = _task_and_plan("test.approved_recording")
    task.execution_stage = ExecutionStage.AWAITING_APPROVAL
    db.upsert_model("tasks", task)
    set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor="Test")
    db.upsert_model("plans", plan)
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    preview: dict[str, Any] = {"ok": True}
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve",
        diff_preview=preview,
        tool_name=step.tool_name,
        risk_level=RiskLevel.R0_READ_ONLY.value,
        args_binding_hmac=args_binding_hmac(step.tool_name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
        status=ApprovalStatus.APPROVED,
    )
    db.upsert_model("approvals", approval)

    asyncio.run(orchestrator.execute_approved_step(approval))

    messages = orchestrator.bus.get_messages(task.id)
    payloads = [m.structured_payload for m in messages if m.structured_payload.get("kind") == "step_screenshot"]
    assert payloads
    assert [frame["phase"] for frame in payloads[-1]["frames"]] == ["before_approved", "after_approved"]


def _tiny_png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8ffff3f0005fe02fea73581"
        "840000000049454e44ae426082"
    )
