from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.content_provenance import collect_content_envelopes, create_content_envelope
from app.core.errors import AppError, StateTransitionError
from app.core.schemas import Plan, PlanStep, Run, RunEngine, RunPhase, StepStatus, Task, ToolCall, ToolResult
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.step_phase import set_step_status
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

        def submit_nowait(self, submitted_task: Task, runner):  # noqa: ANN001
            submitted.append({"task": submitted_task, "runner": runner})
            return None

    monkeypatch.setattr(task_service, "get_pool", lambda: Pool())

    async def run_resume() -> Task:
        resumed = task_service.resume_task(task.id)
        return resumed

    resumed = asyncio.run(run_resume())

    assert resumed.id == task.id
    assert resumed.status == TaskPhase.EXECUTION
    assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
    assert len(submitted) == 1
    assert submitted[0]["task"].id == task.id
    assert submitted[0]["runner"].__name__ == "_resume_task_through_orchestrator"


def test_existing_plan_resume_restores_multi_parent_provenance_from_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Task(
        user_goal="continue after approved and read-only parent steps",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
    parent_steps = [
        PlanStep(
            id="approved-parent",
            task_id=task.id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.approved_parent",
            description="approved parent",
            args={},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_approval=True,
            status=StepStatus.SUCCEEDED,
        ),
        PlanStep(
            id="read-parent",
            task_id=task.id,
            order=2,
            agent_name="DocumentAgent",
            tool_name="test.read_parent",
            description="read parent",
            args={},
            risk_level=RiskLevel.R0_READ_ONLY,
            status=StepStatus.SUCCEEDED,
        ),
    ]
    child = PlanStep(
        id="child",
        task_id=task.id,
        order=3,
        agent_name="FileAgent",
        tool_name="test.child",
        description="consume both parents",
        args={},
        risk_level=RiskLevel.R0_READ_ONLY,
        status=StepStatus.PENDING,
        depends_on=[step.id for step in parent_steps],
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[*parent_steps, child])
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)

    for index, step in enumerate(parent_steps, start=1):
        call = ToolCall(
            id=f"tool-{step.id}",
            task_id=task.id,
            step_id=step.id,
            tool_name=step.tool_name,
            risk_level=step.risk_level,
            execution_key=f"execution:{step.id}",
            status="committed",
            approval_id="approval-approved-parent" if step.id == "approved-parent" else "",
            committed_at=f"2026-07-11T00:00:0{index}+00:00",
            dry_run=False,
        )
        result = ToolResult(
            tool_call_id=call.id,
            ok=True,
            output={"parent": step.id},
            content_envelope=create_content_envelope(
                step.id,
                source_kind="tool_result",
                source_id=step.id,
                task_scope=task.id,
            ),
        )
        db.upsert_model("tool_calls", call)
        db.upsert_model("tool_results", result)

    orchestrator = OrchestratorAgent()
    captured_contexts: list[dict[str, Any]] = []

    async def execute_child(
        task_arg: Task,
        plan_arg: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool = False,
    ) -> StepExecutionOutcome:
        assert task_arg.id == task.id
        assert plan_arg.task_id == task.id
        assert step.id == child.id
        assert observation is not None
        assert threaded_tools is False
        captured_contexts.append(context)
        set_step_status(step, StepStatus.SUCCEEDED, actor="test")
        return StepExecutionOutcome(
            "succeeded",
            ToolResult(tool_call_id="tool-child", ok=True, output={"ok": True}),
        )

    async def skip_duplicate_finalize(task_arg: Task, plan_arg: Plan) -> Task:  # noqa: ARG001
        return task_arg

    monkeypatch.setattr(orchestrator, "_execute_step", execute_child)
    monkeypatch.setattr(orchestrator.completion_handler, "finalize", skip_duplicate_finalize)
    monkeypatch.setattr(task_service, "OrchestratorAgent", lambda: orchestrator)
    submitted: list[tuple[Task, Any]] = []

    class Pool:
        def active_task(self, task_id: str) -> Task | None:  # noqa: ARG002
            return None

        def submit_nowait(self, submitted_task: Task, runner: Any) -> None:
            submitted.append((submitted_task, runner))

    monkeypatch.setattr(task_service, "get_pool", lambda: Pool())
    orchestrator_registry.release_task(task.id)
    try:

        async def resume_and_run() -> None:
            resumed = task_service.resume_task(task.id)
            assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
            assert len(submitted) == 1
            submitted_task, runner = submitted[0]
            await runner(submitted_task)

        asyncio.run(resume_and_run())
    finally:
        orchestrator_registry.release_task(task.id)

    assert len(captured_contexts) == 1
    envelopes = collect_content_envelopes(captured_contexts[0].get("upstream_content_envelopes"))
    assert {envelope.source_id for envelope in envelopes} == {"approved-parent", "read-parent"}


def test_resume_task_without_running_loop_fails_closed() -> None:
    task_pool.reset_pool_for_tests(max_concurrent=1)
    task = Task(
        user_goal="resume from sync endpoint",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
    db.upsert_model("tasks", task)

    with pytest.raises(AppError) as excinfo:
        task_service.resume_task(task.id)

    assert excinfo.value.code == "task_runtime_unavailable"
    assert excinfo.value.status_code == 503
    persisted = task_service.get_task(task.id)
    assert persisted.status == TaskPhase.EXECUTION
    assert persisted.execution_stage == ExecutionStage.PAUSED


def test_resume_task_rejects_completed_task_without_starting_work() -> None:
    task_pool.reset_pool_for_tests(max_concurrent=1)
    task = Task(
        user_goal="do not resume a completed task",
        mode="efficiency",
        status=TaskPhase.COMPLETED,
    )
    db.upsert_model("tasks", task)

    with pytest.raises(StateTransitionError):
        task_service.resume_task(task.id)

    assert task_pool.get_pool().active_task(task.id) is False
    assert task_service.get_task(task.id).status == TaskPhase.COMPLETED


def test_duplicate_resume_uses_pool_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    started: list[str] = []
    release = asyncio.Event()
    task = Task(
        user_goal="resume duplicate endpoint",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
    db.upsert_model("tasks", task)

    async def hold_resume(submitted: Task) -> None:
        started.append(submitted.id)
        await release.wait()

    monkeypatch.setattr(task_service, "get_pool", lambda: pool)
    monkeypatch.setattr(task_service, "_run_existing_plan", hold_resume)

    async def main() -> tuple[Task, Task]:
        first = task_service.resume_task(task.id)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        second = task_service.resume_task(task.id)
        assert pool.active_task(task.id) is True
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return first, second

    first, second = asyncio.run(main())
    assert first.id == task.id
    assert second.id == task.id
    assert len(started) == 1


def test_pause_immediately_after_resume_cancels_the_registered_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    task = Task(
        user_goal="pause before resumed work can execute",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.PAUSED,
    )
    db.upsert_model("tasks", task)
    started = asyncio.Event()

    async def should_not_run(_task: Task) -> None:
        started.set()

    monkeypatch.setattr(task_service, "get_pool", lambda: pool)
    monkeypatch.setattr(task_service, "_run_existing_plan", should_not_run)

    async def main() -> None:
        resumed = task_service.resume_task(task.id)
        assert resumed.execution_stage == ExecutionStage.STEP_RUNNING
        paused = await task_service.pause_task(task.id)
        assert paused.execution_stage == ExecutionStage.PAUSED
        await asyncio.sleep(0)
        assert not started.is_set()
        assert pool.active_task(task.id) is False

    asyncio.run(main())


def test_pause_run_can_skip_redundant_task_status_write(monkeypatch: pytest.MonkeyPatch) -> None:
    task = Task(
        user_goal="pause only the bound run",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)
    run = Run(
        message=task.user_goal,
        mode=task.mode,
        requested_engine=RunEngine.OS,
        engine=RunEngine.OS,
        phase=RunPhase.RUNNING,
        task_id=task.id,
        state={},
    )
    db.upsert_model("runs", run)
    status_writes: list[tuple[str, str]] = []

    def status_spy(task_id: str, status, **_kwargs):  # noqa: ANN001
        status_writes.append((task_id, str(status)))
        return task_service.get_task(task_id)

    monkeypatch.setattr(run_service, "set_task_status", status_spy)

    paused = run_service.pause_run(run.id, update_task_status=False)

    assert paused.phase == RunPhase.PAUSED
    assert status_writes == []


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
        cancel_events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=100)
        completed = [event for event in cancel_events if event["event_type"] == "task.cancel_completed"]
        assert len(completed) == 1
        assert completed[0]["payload"]["elapsed_ms"] < 1000
        return spawned

    try:
        asyncio.run(main())

        assert active.cancelled()
        assert pool.status()["completed"][task.id] == "cancelled"
        assert run_service.get_run(run.id).phase == RunPhase.CANCELLED
    finally:
        run_service._untrack_active_run(run.id)
        task_pool.reset_pool_for_tests()


def test_pause_task_stops_active_worker_and_preserves_paused_state() -> None:
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    task = Task(
        user_goal="pause active task",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)
    run = Run(
        id="osrun_task_pause_bound",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=RunEngine.OS,
        engine=RunEngine.OS,
        phase=RunPhase.RUNNING,
        task_id=task.id,
        state={"run_id": "osrun_task_pause_bound", "engine": "os", "phase": "running", "task_id": task.id},
    )
    db.upsert_model("runs", run)
    active_run = concurrent.futures.Future()
    run_service._track_active_run(run.id, active_run)

    async def main() -> None:
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

        paused = await task_service.pause_task(task.id)

        assert paused.status == TaskPhase.EXECUTION
        assert paused.execution_stage == ExecutionStage.PAUSED
        assert cancelled.is_set()
        assert spawned.cancelled()
        persisted = task_service.get_task(task.id)
        assert persisted.status == TaskPhase.EXECUTION
        assert persisted.execution_stage == ExecutionStage.PAUSED
        assert run_service.get_run(run.id).phase == RunPhase.PAUSED
        assert active_run.cancelled()

    try:
        asyncio.run(main())
    finally:
        run_service._untrack_active_run(run.id)


def test_stale_resume_worker_cannot_overwrite_paused_task() -> None:
    task = Task(
        user_goal="keep the pause requested while a worker unwinds",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", Plan(task_id=task.id, goal=task.user_goal, steps=[]))
    stale_worker_task = task_service.get_task(task.id)
    task_service.set_task_status(task.id, "paused", strict=True)

    asyncio.run(task_service._run_existing_plan(stale_worker_task))

    persisted = task_service.get_task(task.id)
    assert persisted.status == TaskPhase.EXECUTION
    assert persisted.execution_stage == ExecutionStage.PAUSED


def test_stale_resume_failure_cannot_overwrite_cancelled_task(monkeypatch: pytest.MonkeyPatch) -> None:
    task = Task(
        user_goal="keep cancellation after a late worker failure",
        mode="efficiency",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)
    stale_worker_task = task_service.get_task(task.id)
    task_service.set_task_status(task.id, TaskPhase.CANCELLED, strict=True)

    async def fail_late(_task: Task) -> None:
        raise RuntimeError("late worker failure")

    monkeypatch.setattr(task_service, "_run_existing_plan", fail_late)

    with pytest.raises(RuntimeError, match="late worker failure"):
        asyncio.run(task_service._resume_task_through_orchestrator(stale_worker_task))

    assert task_service.get_task(task.id).status == TaskPhase.CANCELLED


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
