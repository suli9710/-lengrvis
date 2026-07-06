from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep, Run, RunEngine, RunPhase, StepStatus, Task
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel
from app.services import run_service, task_pool, task_service


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()
    yield


def test_resume_task_submits_existing_plan_to_background_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[dict[str, Any]] = []
    task = Task(
        user_goal="resume existing plan",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
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
        def active_task(self, task_id: str) -> Task | None:
            return None

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
    task = Task(
        user_goal="resume from sync endpoint",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
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


def test_duplicate_sync_resume_uses_existing_external_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    started: list[dict[str, Any]] = []
    task = Task(
        user_goal="resume duplicate sync endpoint",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
    db.upsert_model("tasks", task)

    class Thread:
        def __init__(self, *, target, name: str, daemon: bool):  # noqa: ANN001
            started.append({"target": target, "name": name, "daemon": daemon})

        def start(self) -> None:
            started[-1]["started"] = True

    monkeypatch.setattr(task_service, "get_pool", lambda: pool)
    monkeypatch.setattr(task_service.threading, "Thread", Thread)

    first = task_service.resume_task(task.id)
    second = task_service.resume_task(task.id)

    assert first.id == task.id
    assert second.id == task.id
    assert len(started) == 1
    assert pool.active_task(task.id) is True


def test_schedule_resume_claims_active_slot_before_background_start(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduled: list[object] = []
    run = Run(
        id="osrun_resume_singleflight",
        message="resume once",
        mode="efficiency",
        requested_engine=RunEngine.OS,
        engine=RunEngine.OS,
        phase=RunPhase.PAUSED,
        state={
            "run_id": "osrun_resume_singleflight",
            "engine": "os",
            "phase": "paused",
            "goal": "resume once",
            "mode": "efficiency",
        },
    )
    db.upsert_model("runs", run)

    class Pending:
        def done(self) -> bool:
            return False

        def cancel(self) -> bool:
            return True

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        scheduled.append(coro)
        try:
            duplicate = run_service._schedule_resume(run_service.get_run(run.id))
            assert duplicate.id == run.id
        finally:
            coro.close()
        return Pending()

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

    try:
        resumed = run_service._schedule_resume(run)

        assert resumed.phase == RunPhase.RUNNING
        assert len(scheduled) == 1
    finally:
        run_service._untrack_active_run(run.id)


def test_untrack_active_run_ignores_stale_owner() -> None:
    run_id = "osrun_stale_owner"
    stale = concurrent.futures.Future()
    current = concurrent.futures.Future()
    run_service._track_active_run(run_id, stale)
    run_service._track_active_run(run_id, current)

    try:
        assert run_service._untrack_active_run(run_id, stale) is False
        assert run_service._run_active(run_id) is True
        assert run_service._untrack_active_run(run_id, current) is True
        assert run_service._run_active(run_id) is False
    finally:
        run_service._untrack_active_run(run_id)


def test_cancel_task_cancels_pool_worker_and_bound_run(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    task = Task(
        user_goal="cancel active task",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)
    run = Run(
        id="osrun_task_cancel_bound",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=RunEngine.OS,
        engine=RunEngine.OS,
        phase=RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_task_cancel_bound",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)
    active = concurrent.futures.Future()
    run_service._track_active_run(run.id, active)

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        coro.close()
        done = concurrent.futures.Future()
        done.set_result(None)
        return done

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

    async def main() -> asyncio.Task:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def runner(submitted: Task) -> Task:
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return submitted

        spawned = await pool.submit(task, runner)
        await asyncio.wait_for(started.wait(), timeout=1)

        cancelled_task = await task_service.cancel_task(task.id)

        assert cancelled_task.status == TaskPhase.CANCELLED
        assert cancelled.is_set()
        assert spawned.cancelled()
        return spawned

    try:
        asyncio.run(main())

        assert active.cancelled()
        assert pool.status()["completed"][task.id] == "cancelled"
        assert run_service.get_run(run.id).phase == RunPhase.CANCELLED
    finally:
        run_service._untrack_active_run(run.id)
        task_pool.reset_pool_for_tests()


def test_approval_resume_defers_when_active_run_row_is_still_running(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduled: list[str] = []
    active = concurrent.futures.Future()
    task = Task(
        user_goal="approve while active turn is still unwinding",
        mode="privacy",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    db.upsert_model("tasks", task)
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                task_id=task.id,
                order=1,
                agent_name="DeveloperExecutionEngine",
                tool_name="developer.lengrvis_code",
                description="developer approval",
                args={},
                risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                requires_approval=True,
                status=StepStatus.WAITING_USER_APPROVAL,
            )
        ],
    )
    db.upsert_model("plans", plan)
    run = Run(
        message=task.user_goal,
        mode=task.mode,
        requested_engine=RunEngine.DEVELOPER,
        engine=RunEngine.DEVELOPER,
        phase=RunPhase.RUNNING,
        task_id=task.id,
        state={},
    )
    db.upsert_model("runs", run)

    def schedule_spy(resumed: Run) -> Run:
        scheduled.append(resumed.id)
        return resumed

    monkeypatch.setattr(run_service, "_schedule_resume", schedule_spy)
    run_service._track_active_run(run.id, active)
    try:
        resumed = run_service.resume_runs_for_task(task.id, include_approval_continuations=True)
        assert [item.id for item in resumed] == [run.id]
        assert scheduled == []

        active.set_result(None)

        assert scheduled == [run.id]
    finally:
        run_service._untrack_active_run(run.id)
