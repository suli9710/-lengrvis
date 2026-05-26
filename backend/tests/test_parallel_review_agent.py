from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.parallel_review_agent import ParallelReviewAgent
from app.core import db
from app.core.schemas import Plan, PlanStep, SafetyReview, StepStatus, Task, TaskStatus
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.handlers.step_scheduler_handler import StepSchedulerHandler
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


class MiniRegistry:
    def __init__(self, *tools: ToolDefinition) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]


class RecordingSafety:
    def __init__(self, verdict: SafetyVerdict) -> None:
        self.verdict = verdict
        self.calls: list[dict[str, Any]] = []

    def review_parallel_batch(self, task_id: str, steps: list[PlanStep], registry: Any) -> SafetyReview:  # noqa: ARG002
        self.calls.append({"task_id": task_id, "step_ids": [step.id for step in steps], "registry": registry})
        return SafetyReview(
            task_id=task_id,
            target_type="parallel_batch",
            verdict=self.verdict,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["recorded"],
        )


def _noop(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    return {"ok": True}


def _tool(
    name: str,
    *,
    risk: RiskLevel = RiskLevel.R0_READ_ONLY,
    effects: list[str] | None = None,
    concurrency_safe: bool | None = True,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="FileAgent",
        supports_dry_run=risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM},
        requires_authorized_path=False,
        execute=_noop,
        read_only=risk == RiskLevel.R0_READ_ONLY,
        concurrency_safe=concurrency_safe,
        effects=effects or ["read"],
        trust_tier="builtin",
        fast_path_eligible=risk == RiskLevel.R0_READ_ONLY,
    )


def _step(step_id: str, tool_name: str, *, risk: RiskLevel = RiskLevel.R0_READ_ONLY) -> PlanStep:
    return PlanStep(
        id=step_id,
        task_id="task_parallel",
        order=1,
        agent_name="FileAgent",
        tool_name=tool_name,
        description=f"Run {tool_name}",
        args={},
        expected_observation="ok",
        risk_level=risk,
    )


def test_parallel_review_agent_allows_only_readonly_concurrency_safe_batches() -> None:
    registry = MiniRegistry(_tool("test.read"))
    steps = [_step("A", "test.read"), _step("B", "test.read")]

    review = ParallelReviewAgent().review_parallel_batch("task_parallel", steps, registry)

    assert review.verdict == SafetyVerdict.ALLOW
    assert review.target_type == "parallel_batch"
    assert review.risk_level == RiskLevel.R0_READ_ONLY
    assert "approved 2 concurrency-safe read-only" in review.reasons[0]


def test_parallel_review_agent_revises_write_like_parallel_batches() -> None:
    write_tool = _tool(
        "test.write_file",
        risk=RiskLevel.R2_REVERSIBLE_MODIFY,
        effects=["write"],
        concurrency_safe=True,
    )
    registry = MiniRegistry(write_tool)
    steps = [
        _step("A", "test.write_file", risk=RiskLevel.R2_REVERSIBLE_MODIFY),
        _step("B", "test.write_file", risk=RiskLevel.R2_REVERSIBLE_MODIFY),
    ]

    review = ParallelReviewAgent().review_parallel_batch("task_parallel", steps, registry)

    assert review.verdict == SafetyVerdict.REVISE_PLAN
    assert review.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert any("write-like effects" in reason for reason in review.reasons)
    assert any("not eligible for parallel execution" in reason for reason in review.reasons)
    assert "serially" in review.safe_alternative.lower()


def test_parallel_review_agent_revises_unknown_parallel_batch_tool() -> None:
    registry = MiniRegistry()
    steps = [_step("A", "test.missing"), _step("B", "test.missing")]

    review = ParallelReviewAgent().review_parallel_batch("task_parallel", steps, registry)

    assert review.verdict == SafetyVerdict.REVISE_PLAN
    assert review.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF
    assert any("unavailable tool" in reason for reason in review.reasons)


def test_scheduler_consults_safety_review_agent_before_parallelizing_ready_steps() -> None:
    safety = RecordingSafety(SafetyVerdict.ALLOW)
    registry = MiniRegistry(_tool("test.read"))
    task = Task(id="task_parallel", user_goal="run ready steps", status=TaskStatus.REVIEWING_PLAN)
    ready = [_step("A", "test.read"), _step("B", "test.read")]
    handler = StepSchedulerHandler(SimpleNamespace(safety=safety, parallel_review=safety, registry=registry))

    allowed = handler._parallel_batch_allowed(task, ready)

    assert allowed is True
    assert safety.calls == [{"task_id": "task_parallel", "step_ids": ["A", "B"], "registry": registry}]


def test_scheduler_downgrades_revised_parallel_batch_to_serial_execution() -> None:
    safety = RecordingSafety(SafetyVerdict.REVISE_PLAN)
    registry = MiniRegistry(_tool("test.write_file", risk=RiskLevel.R2_REVERSIBLE_MODIFY, effects=["write"]))
    task = Task(id="task_parallel", user_goal="run ready steps", status=TaskStatus.REVIEWING_PLAN)
    ready = [
        _step("A", "test.write_file", risk=RiskLevel.R2_REVERSIBLE_MODIFY),
        _step("B", "test.write_file", risk=RiskLevel.R2_REVERSIBLE_MODIFY),
    ]
    handler = StepSchedulerHandler(SimpleNamespace(safety=safety, parallel_review=safety, registry=registry))

    allowed = handler._parallel_batch_allowed(task, ready)

    assert allowed is False
    assert safety.calls == [{"task_id": "task_parallel", "step_ids": ["A", "B"], "registry": registry}]


@pytest.mark.asyncio
async def test_parallel_batch_denied_steps_do_not_enter_concurrent_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    safety = RecordingSafety(SafetyVerdict.DENY)
    registry = MiniRegistry(_tool("test.read"))
    task = Task(id="task_parallel", user_goal="run ready steps", status=TaskStatus.REVIEWING_PLAN)
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[_step("A", "test.read"), _step("B", "test.read")],
    )
    execution_log: list[tuple[str, bool]] = []
    active_steps: set[str] = set()
    max_active = 0
    create_task_calls = 0
    original_create_task = asyncio.create_task

    def tracked_create_task(*args: Any, **kwargs: Any):
        nonlocal create_task_calls
        create_task_calls += 1
        return original_create_task(*args, **kwargs)

    class Bus:
        def publish_text(self, *args: Any, **kwargs: Any) -> None:
            return None

    class RecoveryHandler:
        async def recover_failed_step(self, *args: Any, **kwargs: Any) -> StepExecutionOutcome:
            raise AssertionError("recovery should not run")

    class Orchestrator:
        name = "OrchestratorAgent"

        def __init__(self) -> None:
            self.safety = safety
            self.parallel_review = safety
            self.registry = registry
            self.bus = Bus()
            self.recovery_handler = RecoveryHandler()
            self.persisted: list[tuple[Plan, str]] = []

        def _tool_context(self) -> dict[str, Any]:
            return {"registry": self.registry}

        async def _execute_step(
            self,
            task: Task,
            plan: Plan,
            step: PlanStep,
            context: dict[str, Any],
            observation: Any,
            *,
            threaded_tools: bool = False,
        ) -> StepExecutionOutcome:
            nonlocal max_active
            execution_log.append((step.id, threaded_tools))
            active_steps.add(step.id)
            max_active = max(max_active, len(active_steps))
            await asyncio.sleep(0)
            active_steps.remove(step.id)
            step.status = StepStatus.SUCCEEDED
            return StepExecutionOutcome("succeeded")

        def _set_status(self, task: Task, status: Any, final_summary: str = "") -> None:
            task.status = status
            task.final_summary = final_summary

        def _persist_plan_update(self, plan: Plan, reason: str) -> None:
            self.persisted.append((plan, reason))

        def _friendly_tool_error(self, error: str) -> str:
            return error

    monkeypatch.setattr(asyncio, "create_task", tracked_create_task)
    await StepSchedulerHandler(Orchestrator()).process_steps(task, plan)

    assert safety.calls == [{"task_id": "task_parallel", "step_ids": ["A", "B"], "registry": registry}]
    assert create_task_calls == 0
    assert max_active == 1
    assert execution_log == [("A", False), ("B", False)]
