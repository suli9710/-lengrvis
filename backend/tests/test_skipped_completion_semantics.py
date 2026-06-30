from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.handlers.step_scheduler_handler import StepSchedulerHandler, _ScheduleState
from app.policy.risk import RiskLevel


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    """Write audit/run events to a per-test SQLite file, not the shared dev DB."""
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def _step(step_id: str, *, status: StepStatus, skip_reason: str | None = None) -> PlanStep:
    model_action = {"scheduler": {"skip_reason": skip_reason, "blocked_by": ["upstream"]}} if skip_reason else {}
    return PlanStep(
        id=step_id,
        task_id="task_partial",
        order=1,
        agent_name="FileAgent",
        tool_name="test.tool",
        description=step_id,
        args={},
        expected_observation="",
        risk_level=RiskLevel.R0_READ_ONLY,
        status=status,
        model_action=model_action,
    )


def test_finalize_marks_failed_when_success_coexists_with_blocked_skips() -> None:
    task = Task(id="task_partial", user_goal="partial completion", mode="efficiency", status=TaskStatus.EXECUTION)
    plan = Plan(
        task_id=task.id,
        goal="partial completion",
        steps=[
            _step("A", status=StepStatus.SUCCEEDED),
            _step("B", status=StepStatus.SKIPPED, skip_reason="blocked_dependency"),
        ],
    )
    statuses: list[TaskStatus] = []

    def _set_status(bound_task: Task, status: TaskStatus, **kwargs: object) -> None:
        statuses.append(status)
        bound_task.status = status

    orchestrator = SimpleNamespace(
        _set_status=_set_status,
        _persist_plan_update=lambda *args, **kwargs: None,
        name="TestOrchestrator",
    )
    handler = StepSchedulerHandler(orchestrator)
    handler._finalize_plan_status(
        task, plan, _ScheduleState(pending=set(), by_id={step.id: step for step in plan.steps})
    )

    assert statuses == [TaskStatus.FAILED]
