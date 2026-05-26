from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep, StepStatus, Task
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel
from app.services import task_service


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    yield


def test_resume_task_submits_existing_plan_to_background_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[dict[str, Any]] = []
    task = Task(user_goal="resume existing plan", mode="efficiency", status=TaskPhase.EXECUTION, execution_stage=ExecutionStage.PAUSED)
    db.upsert_model("tasks", task)
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                task_id=task.id,
                order=1,
                agent_name="FileAgent",
                tool_name="test.read",
                description="pending read",
                args={},
                risk_level=RiskLevel.R0_READ_ONLY,
                status=StepStatus.PENDING,
            )
        ],
    )
    db.upsert_model("plans", plan)

    class Pool:
        async def submit(self, submitted_task: Task, runner):  # noqa: ANN001
            submitted.append({"task": submitted_task, "runner": runner})
            return None

    monkeypatch.setattr(task_service, "get_pool", lambda: Pool())

    async def run_resume() -> Task:
        resumed = task_service.resume_task(task.id)
        await asyncio.sleep(0)
        return resumed

    resumed = asyncio.run(run_resume())

    assert resumed.id == task.id
    assert resumed.status == TaskPhase.EXECUTION
    assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
    assert len(submitted) == 1
    assert submitted[0]["task"].id == task.id
    assert submitted[0]["runner"].__name__ == "_resume_task_through_orchestrator"


def test_resume_task_without_running_loop_starts_background_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[dict[str, Any]] = []
    task = Task(user_goal="resume from sync endpoint", mode="efficiency", status=TaskPhase.EXECUTION, execution_stage=ExecutionStage.PAUSED)
    db.upsert_model("tasks", task)

    class Thread:
        def __init__(self, *, target, name: str, daemon: bool):  # noqa: ANN001
            started.append({"target": target, "name": name, "daemon": daemon})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(task_service.threading, "Thread", Thread)

    resumed = task_service.resume_task(task.id)

    assert resumed.id == task.id
    assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
    assert len(started) == 1
    assert started[0]["name"] == f"task-resume-{task.id}"
    assert started[0]["daemon"] is True
    assert started[0]["started"] is True
