from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import AgentAction, MessageType, Plan, PlanStep, StepStatus, Task, ToolResult
from app.orchestration.agent_bus import AgentBus
from app.orchestration.dispatcher import EventDispatcher
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.handlers.recovery_handler import RecoveryHandler
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


class OrchestratorStub:
    name = "OrchestratorAgent"

    def __init__(self, action: AgentAction | None):
        self.bus = AgentBus()
        self.dispatcher = EventDispatcher(self.bus)
        self.action = action
        self.executed_recovery_steps: list[PlanStep] = []
        self.persist_messages: list[str] = []
        self.registry = ToolRegistry()

    async def _consult_subagent(self, task, step, *, observation=None):  # noqa: ARG002
        return self.action

    async def _execute_step(self, task, plan, step, context, observation, *, threaded_tools=False):  # noqa: ARG002
        self.executed_recovery_steps.append(step)
        from app.orchestration.step_phase import set_step_status

        set_step_status(step, StepStatus.SUCCEEDED, actor="Test")
        return StepExecutionOutcome(
            "succeeded",
            ToolResult(tool_call_id=f"{step.id}_call", ok=True, observation="recovered"),
        )

    def _persist_plan_update(self, plan, content, *, revision_change=False):
        if revision_change:
            plan.version += 1
        db.upsert_model("plans", plan)
        self.persist_messages.append(content)

    def _set_status(self, task, status, *, final_summary=None):
        task.status = status
        task.phase = status if isinstance(status, TaskPhase) else task.phase
        if final_summary is not None:
            task.final_summary = final_summary
        db.upsert_model("tasks", task)
        return task

    def _friendly_tool_error(self, error: str) -> str:
        return error


def _register_write_tool(orchestrator: OrchestratorStub, *, safe_to_retry_errors: list[str]) -> None:
    orchestrator.registry.register(
        ToolDefinition(
            name="file.write",
            description="Write a file.",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="FileAgent",
            supports_dry_run=True,
            requires_authorized_path=True,
            execute=lambda args, context: {"ok": True},
            read_only=False,
            concurrency_safe=False,
            effects=["write"],
            resource_kinds=["file"],
            trust_tier="builtin",
            safe_to_retry_errors=safe_to_retry_errors,
        )
    )


def _verified_empty_rollback(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "executed": [],
        "count": 0,
        "state": "succeeded",
        "attempted": 0,
        "succeeded": 0,
        "verified": 0,
        "verification_failed": 0,
        "failed": 0,
        "manual_required": 0,
        "unrecoverable": 0,
    }


def test_recovery_handler_creates_and_executes_recovery_step():
    orchestrator = OrchestratorStub(
        AgentAction(
            kind="propose_tool",
            tool_name="file.read",
            args={"path": "C:/tmp/fallback.txt"},
            rationale="Read fallback file.",
        )
    )
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_1", user_goal="read file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.read", description="read original")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing file")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "recovered"
    assert step.status == StepStatus.SKIPPED
    assert len(plan.steps) == 2
    assert orchestrator.executed_recovery_steps[0].args["path"] == "C:/tmp/fallback.txt"
    events = [
        message
        for message in orchestrator.bus.get_messages(task.id)
        if message.message_type == MessageType.NOTIFICATION
    ]
    assert any(message.structured_payload.get("event_type") == "tool.failed" for message in events)


def test_recovery_handler_rolls_back_when_no_alternative(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = OrchestratorStub(None)
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_1", user_goal="write file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.write", description="write")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="disk full")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert rollback_calls == [task.id]
    assert step.status == StepStatus.FAILED
    assert task.status == TaskPhase.FAILED


def test_recovery_handler_blocks_unclassified_high_risk_failure_before_consulting(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = OrchestratorStub(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="retry")
    )
    _register_write_tool(orchestrator, safe_to_retry_errors=[])
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_high_risk_blocked", user_goal="write file")
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="file.write",
        description="write",
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    plan = Plan(id="plan_high_risk_blocked", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(
        tool_call_id="call_high_risk_blocked",
        ok=False,
        error="temporary lock",
        output={"error_code": "TEMPORARY_LOCK"},
    )

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert orchestrator.executed_recovery_steps == []
    assert len(plan.steps) == 1
    assert rollback_calls == [task.id]


def test_recovery_handler_allows_explicitly_classified_high_risk_retry(monkeypatch):
    monkeypatch.setattr(
        "app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback",
        lambda task_id, **_kwargs: _verified_empty_rollback(task_id),
    )
    orchestrator = OrchestratorStub(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="retry")
    )
    _register_write_tool(orchestrator, safe_to_retry_errors=["TEMPORARY_LOCK"])
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_high_risk_retry", user_goal="write file")
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="file.write",
        description="write",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    plan = Plan(id="plan_high_risk_retry", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(
        tool_call_id="call_high_risk_retry",
        ok=False,
        error="temporary lock",
        output={"error_code": "TEMPORARY_LOCK"},
    )

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "recovered"
    assert len(orchestrator.executed_recovery_steps) == 1
    assert len(plan.steps) == 2


def test_recovery_handler_blocks_high_risk_retry_when_registry_lookup_fails(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = OrchestratorStub(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="retry")
    )
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_missing_retry_contract", user_goal="write file")
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="missing.high_risk_tool",
        description="write",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    plan = Plan(id="plan_missing_retry_contract", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(
        tool_call_id="call_missing_retry_contract",
        ok=False,
        error="temporary lock",
        output={"error_code": "TEMPORARY_LOCK"},
    )

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert orchestrator.executed_recovery_steps == []
    assert len(plan.steps) == 1
    assert rollback_calls == [task.id]


def test_recovery_handler_retry_limit_applies_to_recovery_chain(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    class AlwaysFailingRecoveryOrchestrator(OrchestratorStub):
        async def _execute_step(self, task, plan, step, context, observation, *, threaded_tools=False):  # noqa: ARG002
            self.executed_recovery_steps.append(step)
            from app.orchestration.step_phase import set_step_status

            set_step_status(step, StepStatus.FAILED, actor="Test")
            return StepExecutionOutcome(
                "failed",
                ToolResult(tool_call_id=f"{step.id}_call", ok=False, error="recovery failed"),
            )

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = AlwaysFailingRecoveryOrchestrator(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="try fallback")
    )
    handler = RecoveryHandler(orchestrator, max_retries=1)
    task = Task(id="task_1", user_goal="read file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.read", description="read")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing file")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert len(orchestrator.executed_recovery_steps) == 1
    assert len(plan.steps) == 2
    assert rollback_calls == [task.id]


def test_recovery_handler_stops_repeated_identical_recovery_attempt(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    class AlwaysFailingRecoveryOrchestrator(OrchestratorStub):
        async def _execute_step(self, task, plan, step, context, observation, *, threaded_tools=False):  # noqa: ARG002
            self.executed_recovery_steps.append(step)
            from app.orchestration.step_phase import set_step_status

            set_step_status(step, StepStatus.FAILED, actor="Test")
            return StepExecutionOutcome(
                "failed",
                ToolResult(tool_call_id=f"{step.id}_call", ok=False, error="recovery failed"),
            )

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = AlwaysFailingRecoveryOrchestrator(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="try fallback")
    )
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_1", user_goal="read file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.read", description="read")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing file")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert len(orchestrator.executed_recovery_steps) == 1
    assert len(plan.steps) == 2
    assert rollback_calls == [task.id]


def test_recovery_handler_rejects_same_tool_subset_args(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = OrchestratorStub(
        AgentAction(
            kind="propose_tool",
            tool_name="search.query",
            args={"dry_run": True},
            rationale="retry same search without adding information",
        )
    )
    handler = RecoveryHandler(orchestrator)
    task = Task(id="task_search", user_goal="search web")
    step = PlanStep(
        task_id=task.id,
        agent_name="SearchAgent",
        tool_name="search.query",
        description="search",
        args={"query": "latest news", "dry_run": True},
    )
    plan = Plan(id="plan_search", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing provider")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert orchestrator.executed_recovery_steps == []
    assert len(plan.steps) == 1
    assert rollback_calls == [task.id]


def test_recovery_handler_zero_retries_rolls_back_without_consulting(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str, **_kwargs):
        rollback_calls.append(task_id)
        return _verified_empty_rollback(task_id)

    monkeypatch.setattr("app.orchestration.task_rollback_workflow.rollback_tools.execute_rollback", fake_rollback)
    orchestrator = OrchestratorStub(
        AgentAction(kind="propose_tool", tool_name="file.read", args={"path": "fallback"}, rationale="try fallback")
    )
    handler = RecoveryHandler(orchestrator, max_retries=0)
    task = Task(id="task_1", user_goal="read file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.read", description="read")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing file")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "fatal_failed"
    assert orchestrator.executed_recovery_steps == []
    assert len(plan.steps) == 1
    assert rollback_calls == [task.id]


def test_cleanup_task_removes_retry_entries_for_completed_task():
    handler = RecoveryHandler(OrchestratorStub(None))
    handler._increment_retry_count(("task_a", "step_1"))
    handler._increment_retry_count(("task_b", "step_1"))

    handler.cleanup_task("task_a")

    assert handler._get_retry_count(("task_a", "step_1")) == 0
    assert handler._get_retry_count(("task_b", "step_1")) == 1
