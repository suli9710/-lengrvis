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


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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

    async def _consult_subagent(self, task, step, *, observation=None):  # noqa: ARG002
        return self.action

    async def _execute_step(self, task, plan, step, context, observation, *, threaded_tools=False):  # noqa: ARG002
        self.executed_recovery_steps.append(step)
        step.status = StepStatus.SUCCEEDED
        return StepExecutionOutcome(
            "succeeded",
            ToolResult(tool_call_id=f"{step.id}_call", ok=True, observation="recovered"),
        )

    def _persist_plan_update(self, plan, content):
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
    handler.register(orchestrator.dispatcher)
    task = Task(id="task_1", user_goal="read file")
    step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name="file.read", description="read original")
    plan = Plan(id="plan_1", task_id=task.id, goal=task.user_goal, steps=[step])
    failed = ToolResult(tool_call_id="call_1", ok=False, error="missing file")

    outcome = asyncio.run(handler.recover_failed_step(task, plan, step, failed, {}, None))

    assert outcome.kind == "recovered"
    assert step.status == StepStatus.SKIPPED
    assert len(plan.steps) == 2
    assert orchestrator.executed_recovery_steps[0].args["path"] == "C:/tmp/fallback.txt"
    events = [message for message in orchestrator.bus.get_messages(task.id) if message.message_type == MessageType.NOTIFICATION]
    assert any(message.structured_payload.get("event_type") == "tool.failed" for message in events)


def test_recovery_handler_rolls_back_when_no_alternative(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str):
        rollback_calls.append(task_id)
        return {"task_id": task_id, "executed": [], "count": 0}

    monkeypatch.setattr("app.orchestration.handlers.recovery_handler.rollback_tools.execute_rollback", fake_rollback)
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


def test_recovery_handler_retry_limit_applies_to_recovery_chain(monkeypatch):
    rollback_calls: list[str] = []

    def fake_rollback(task_id: str):
        rollback_calls.append(task_id)
        return {"task_id": task_id, "executed": [], "count": 0}

    class AlwaysFailingRecoveryOrchestrator(OrchestratorStub):
        async def _execute_step(self, task, plan, step, context, observation, *, threaded_tools=False):  # noqa: ARG002
            self.executed_recovery_steps.append(step)
            step.status = StepStatus.FAILED
            return StepExecutionOutcome(
                "failed",
                ToolResult(tool_call_id=f"{step.id}_call", ok=False, error="recovery failed"),
            )

    monkeypatch.setattr("app.orchestration.handlers.recovery_handler.rollback_tools.execute_rollback", fake_rollback)
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
