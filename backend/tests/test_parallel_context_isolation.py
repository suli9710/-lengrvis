from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.handlers.step_scheduler_handler import StepSchedulerHandler, _ScheduleState
from app.orchestration import resource_state as rs
from app.orchestration.os_execution_engine import OSExecutionEngine
from app.policy.risk import RiskLevel, SafetyVerdict


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    """Write audit/run events to a per-test SQLite file, not the shared dev DB."""
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def _read_tool() -> Any:
    return SimpleNamespace(
        name="file.read_text",
        effects=["read"],
        is_read_only=lambda: True,
    )


def _file_state(path: str) -> dict[str, Any]:
    normalized = rs.normalize_path_key(path)
    return {
        "path": path,
        "normalized_path": normalized,
        "exists": True,
        "is_file": True,
        "size": 4,
        "mtime_ns": 1,
        "sha256": "abc",
    }


@pytest.fixture(autouse=True)
def _clear_read_states() -> None:
    rs.clear_task_read_states("task_iso")
    yield
    rs.clear_task_read_states("task_iso")


@pytest.mark.asyncio
async def test_parallel_scheduler_steps_receive_isolated_context_dicts() -> None:
    contexts_seen: list[dict[str, Any]] = []

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        contexts_seen.append(context)
        context["step_marker"] = step.id
        context.setdefault("nested", {})["from_step"] = step.id
        await asyncio.sleep(0.01)
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_iso", user_goal="parallel isolation", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = [
        PlanStep(
            id="A",
            task_id=task.id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="A",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
        PlanStep(
            id="B",
            task_id=task.id,
            order=2,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="B",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
    ]
    plan = Plan(task_id=task.id, goal="parallel isolation", steps=steps)
    orchestrator = SimpleNamespace(_execute_step=fake_execute)
    handler = StepSchedulerHandler(orchestrator)
    shared_context: dict[str, Any] = {"task_id": task.id, "nested": {"shared": True}}
    state = _ScheduleState(pending={"A", "B"}, by_id={step.id: step for step in steps})

    handler._launch_ready_steps(task, plan, steps, shared_context, state, threaded_tools=True)
    await asyncio.gather(*state.running.keys(), return_exceptions=True)

    assert len(contexts_seen) == 2
    assert contexts_seen[0] is not contexts_seen[1]
    assert {ctx["step_marker"] for ctx in contexts_seen} == {"A", "B"}
    assert shared_context.get("step_marker") is None
    assert shared_context["nested"] == {"shared": True}
    assert all(ctx["nested"] is not shared_context["nested"] for ctx in contexts_seen)


def test_read_states_are_scoped_per_step_not_shared_across_parallel_steps() -> None:
    tool = _read_tool()
    path = "/tmp/workspace/isolated.txt"
    state = _file_state(path)
    key = rs.normalize_path_key(path)

    rs.remember_read_states_for_tool(
        tool,
        {},
        {"_resource_state": [state]},
        {"task_id": "task_iso", "step_id": "step_a"},
    )

    context_a = {"task_id": "task_iso", "step_id": "step_a"}
    context_b = {"task_id": "task_iso", "step_id": "step_b"}

    cached_a = rs._read_state_for_path(key, context_a)
    cached_b = rs._read_state_for_path(key, context_b)

    assert cached_a is not None
    assert cached_a["state"]["path"] == path
    assert cached_b is None

    rs.clear_task_read_states("task_iso")
    assert rs._read_state_for_path(key, context_a) is None


def test_prior_step_read_visible_to_later_write_step_with_include_prior_steps() -> None:
    tool = _read_tool()
    path = "/tmp/workspace/sequential.txt"
    state = _file_state(path)
    key = rs.normalize_path_key(path)

    rs.remember_read_states_for_tool(
        tool,
        {},
        {"_resource_state": [state]},
        {"task_id": "task_seq", "step_id": "step_read"},
    )

    write_context = {"task_id": "task_seq", "step_id": "step_write"}
    found = rs._read_state_for_path(key, write_context, include_prior_steps=True)
    assert found is not None
    assert found["state"]["path"] == path

    # Parallel isolation: same path on a different step must not inherit without include_prior_steps.
    assert rs._read_state_for_path(key, write_context, include_prior_steps=False) is None

    rs.clear_task_read_states("task_seq")


@pytest.mark.asyncio
async def test_os_engine_parallel_steps_receive_isolated_context_dicts() -> None:
    contexts_seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        contexts_seen.append((step.id, context))
        context["step_marker"] = step.id
        await asyncio.sleep(0.01)
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_iso", user_goal="os parallel isolation", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = [
        PlanStep(
            id="A",
            task_id=task.id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="A",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
        PlanStep(
            id="B",
            task_id=task.id,
            order=2,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="B",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
    ]
    plan = Plan(task_id=task.id, goal="os parallel isolation", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _dependency_observation=lambda step, observations: None,
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
    )
    engine = OSExecutionEngine(orchestrator=orchestrator)
    shared_context: dict[str, Any] = {"task_id": task.id, "run_id": "osrun_test", "nested": {"shared": True}}

    await engine._execute_selected_steps(
        task,
        plan,
        steps,
        shared_context,
        {},
        threaded_tools=True,
    )

    assert len(contexts_seen) == 2
    ids = {step_id for step_id, _ctx in contexts_seen}
    assert ids == {"A", "B"}
    ctx_a = next(ctx for step_id, ctx in contexts_seen if step_id == "A")
    ctx_b = next(ctx for step_id, ctx in contexts_seen if step_id == "B")
    assert ctx_a is not ctx_b
    assert ctx_a["step_marker"] == "A"
    assert ctx_b["step_marker"] == "B"
    assert shared_context.get("step_marker") is None
    assert ctx_a["nested"] is not shared_context["nested"]
    assert ctx_b["nested"] is not shared_context["nested"]


def _parallel_steps(task_id: str) -> list[PlanStep]:
    return [
        PlanStep(
            id="A",
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="A",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
        PlanStep(
            id="B",
            task_id=task_id,
            order=2,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="B",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
    ]


@pytest.mark.asyncio
async def test_parallel_scheduler_steps_execute_on_isolated_step_snapshots_and_write_back() -> None:
    received: dict[str, PlanStep] = {}

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        received[step.id] = step
        real = next(item for item in plan.steps if item.id == step.id)
        # Executors must get an isolated snapshot, never the shared plan step.
        assert real is not step
        assert real.status == StepStatus.PENDING
        step.args["touched_by"] = step.id
        step.description = f"{step.id} executed"
        step.status = StepStatus.SUCCEEDED
        await asyncio.sleep(0.01)
        # Mid-flight mutations stay invisible on the real plan until write-back.
        assert real.args.get("touched_by") is None
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_snap", user_goal="snapshot isolation", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = _parallel_steps(task.id)
    plan = Plan(task_id=task.id, goal="snapshot isolation", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _tool_context=lambda: {"task_id": task.id},
        _set_status=lambda *args, **kwargs: None,
        _persist_plan_update=lambda *args, **kwargs: None,
        name="TestOrchestrator",
        parallel_review=SimpleNamespace(
            review_parallel_batch=lambda *args, **kwargs: SimpleNamespace(verdict=SafetyVerdict.ALLOW, reasons=[]),
        ),
        registry=SimpleNamespace(),
        safety=SimpleNamespace(),
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
    )
    handler = StepSchedulerHandler(orchestrator)

    await handler.process_steps(task, plan)

    assert set(received) == {"A", "B"}
    for step in plan.steps:
        assert received[step.id] is not step
        assert step.status == StepStatus.SUCCEEDED
        assert step.args["touched_by"] == step.id
        assert step.description == f"{step.id} executed"


@pytest.mark.asyncio
async def test_os_engine_parallel_steps_execute_on_isolated_step_snapshots_and_write_back() -> None:
    received: dict[str, PlanStep] = {}

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        received[step.id] = step
        real = next(item for item in plan.steps if item.id == step.id)
        assert real is not step
        assert real.status == StepStatus.PENDING
        step.args["touched_by"] = step.id
        step.status = StepStatus.SUCCEEDED
        await asyncio.sleep(0.01)
        assert real.args.get("touched_by") is None
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_snap_os", user_goal="os snapshot isolation", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = _parallel_steps(task.id)
    plan = Plan(task_id=task.id, goal="os snapshot isolation", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _dependency_observation=lambda step, observations: None,
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
    )
    engine = OSExecutionEngine(orchestrator=orchestrator)

    results = await engine._execute_selected_steps(
        task,
        plan,
        steps,
        {"task_id": task.id, "run_id": "osrun_snap"},
        {},
        threaded_tools=True,
    )

    assert set(received) == {"A", "B"}
    # Results must pair outcomes with the *real* plan steps after write-back.
    assert {id(step) for step, _outcome in results} == {id(step) for step in steps}
    for step in plan.steps:
        assert received[step.id] is not step
        assert step.status == StepStatus.SUCCEEDED
        assert step.args["touched_by"] == step.id


@pytest.mark.asyncio
async def test_scheduler_fatal_outcome_cancels_parallel_siblings() -> None:
    events: list[tuple[str, str]] = []

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        events.append((step.id, "start"))
        if step.id == "A":
            return StepExecutionOutcome(
                "fatal_denied",
                ToolResult(tool_call_id=step.id, ok=False, observation="denied"),
            )
        await asyncio.sleep(5)
        events.append((step.id, "end"))
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_fatal", user_goal="fatal cancel", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = _parallel_steps(task.id)
    plan = Plan(task_id=task.id, goal="fatal cancel", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _tool_context=lambda: {"task_id": task.id},
        _set_status=lambda *args, **kwargs: None,
        _persist_plan_update=lambda *args, **kwargs: None,
        name="TestOrchestrator",
        parallel_review=SimpleNamespace(
            review_parallel_batch=lambda *args, **kwargs: SimpleNamespace(verdict=SafetyVerdict.ALLOW, reasons=[]),
        ),
        registry=SimpleNamespace(),
        safety=SimpleNamespace(),
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
    )
    handler = StepSchedulerHandler(orchestrator)

    started = time.monotonic()
    await handler.process_steps(task, plan)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert ("A", "start") in events
    assert ("B", "start") in events
    assert ("B", "end") not in events


@pytest.mark.asyncio
async def test_os_engine_fatal_outcome_cancels_parallel_siblings() -> None:
    events: list[tuple[str, str]] = []

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        events.append((step.id, "start"))
        if step.id == "A":
            return StepExecutionOutcome(
                "fatal_denied",
                ToolResult(tool_call_id=step.id, ok=False, observation="denied"),
            )
        await asyncio.sleep(5)
        events.append((step.id, "end"))
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_fatal_os", user_goal="os fatal cancel", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = _parallel_steps(task.id)
    plan = Plan(task_id=task.id, goal="os fatal cancel", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _dependency_observation=lambda step, observations: None,
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
    )
    engine = OSExecutionEngine(orchestrator=orchestrator)

    started = time.monotonic()
    await engine._execute_selected_steps(
        task,
        plan,
        steps,
        {"task_id": task.id, "run_id": "osrun_fatal"},
        {},
        threaded_tools=True,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert ("A", "start") in events
    assert ("B", "start") in events
    assert ("B", "end") not in events


@pytest.mark.asyncio
async def test_cancel_running_steps_writes_back_completed_sibling() -> None:
    from app.orchestration.step_phase import set_step_status

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        if step.id == "fast":
            set_step_status(step, StepStatus.SUCCEEDED, actor="Test")
            step.description = "fast-done"
            return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))
        await asyncio.sleep(10)
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="late"))

    task = Task(id="task_cancel_wb", user_goal="cancel writeback", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = [
        PlanStep(
            id="fast",
            task_id=task.id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="fast",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
        PlanStep(
            id="slow",
            task_id=task.id,
            order=2,
            agent_name="FileAgent",
            tool_name="test.tool",
            description="slow",
            args={},
            expected_observation="",
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
    ]
    plan = Plan(task_id=task.id, goal="cancel writeback", steps=steps)
    orchestrator = SimpleNamespace(name="TestOrchestrator", _execute_step=fake_execute)
    handler = StepSchedulerHandler(orchestrator)
    shared_context: dict[str, Any] = {"task_id": task.id}
    state = _ScheduleState(pending={"fast", "slow"}, by_id={step.id: step for step in steps})
    handler._launch_ready_steps(task, plan, steps, shared_context, state, threaded_tools=True)
    await asyncio.sleep(0.05)
    await handler._cancel_running_steps(task, state)

    fast = next(step for step in plan.steps if step.id == "fast")
    slow = next(step for step in plan.steps if step.id == "slow")
    assert fast.status == StepStatus.SUCCEEDED
    assert fast.description == "fast-done"
    assert slow.status == StepStatus.FAILED
