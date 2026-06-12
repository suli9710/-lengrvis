"""Verify API/engine cancel_run cancels in-flight parallel step tasks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep, Task, TaskStatus, ToolResult
from app.orchestration.execution_engine import InMemoryRunStore
from app.orchestration.execution_models import RunPhase, RunState
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.os_execution_engine import OSExecutionEngine
from app.policy.risk import RiskLevel


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    """Write audit/run events to a per-test SQLite file, not the shared dev DB."""
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


@pytest.mark.asyncio
async def test_cancel_run_cancels_registered_parallel_step_tasks() -> None:
    cancelled: list[str] = []
    release = asyncio.Event()

    async def fake_execute(
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(step.id)
            raise
        return StepExecutionOutcome("succeeded", ToolResult(tool_call_id=step.id, ok=True, observation="ok"))

    task = Task(id="task_cancel", user_goal="cancel drain", mode="efficiency", status=TaskStatus.EXECUTION)
    steps = [
        PlanStep(
            id="slow_a",
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
            id="slow_b",
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
    plan = Plan(task_id=task.id, goal="cancel drain", steps=steps)
    orchestrator = SimpleNamespace(
        _execute_step=fake_execute,
        _dependency_observation=lambda step, observations: None,
        recovery_handler=SimpleNamespace(recover_failed_step=lambda *args, **kwargs: None),
        name="TestOrchestrator",
        _set_status=lambda *args, **kwargs: None,
        _friendly_tool_error=lambda error: str(error),
    )
    engine = OSExecutionEngine(orchestrator=orchestrator, store=InMemoryRunStore())

    run_id = "run_cancel_drain"
    engine.store.put(
        RunState(
            run_id=run_id,
            engine="os",
            phase=RunPhase.RUNNING,
            goal="cancel drain",
            task_id="",
            current_plan=plan.model_dump(),
        )
    )

    execution = asyncio.create_task(
        engine._execute_selected_steps(
            task,
            plan,
            steps,
            {"task_id": task.id, "run_id": run_id},
            {},
            threaded_tools=True,
        )
    )
    await asyncio.sleep(0.05)

    cancelled_state = await engine.cancel_run(run_id)

    assert cancelled_state.phase == RunPhase.CANCELLED
    assert set(cancelled) == {"slow_a", "slow_b"}

    results = await asyncio.wait_for(execution, timeout=2)
    assert len(results) == 2
