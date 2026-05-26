"""P0 T01: orchestrator routes steps through owning subagent act()."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.agents.base import AgentContext
from app.agents.file_agent import FileAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import AgentAction, Approval, ApprovalStatus, MessageType, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.tools.registry import ToolRegistry, register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    db.init_db()
    register_all_tools()
    yield


class RecordingAgent:
    name = "FileAgent"

    def __init__(self, action: AgentAction) -> None:
        self.action = action
        self.calls: list[tuple[PlanStep, AgentContext, Any]] = []

    async def act(self, step: PlanStep, context: AgentContext, observation=None, *, provider=None):  # noqa: ARG002
        self.calls.append((step, context, observation))
        return self.action

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return f"reflected {step.tool_name}"


def _tool(name: str, calls: list[dict[str, Any]], *, owner: str = "FileAgent"):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        calls.append({"tool": name, "args": dict(args)})
        return {"ok": True, "tool": name, "args": dict(args)}

    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner=owner,
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=execute,
    )


def _schema_tool(name: str, calls: list[dict[str, Any]], schema: dict[str, Any], *, owner: str = "FileAgent"):
    tool = _tool(name, calls, owner=owner)
    tool.input_schema = schema
    return tool


def _task_and_plan(tool_name: str, agent_name: str = "FileAgent", args: dict[str, Any] | None = None):
    task = Task(user_goal="probe", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=0,
        agent_name=agent_name,
        tool_name=tool_name,
        description="Probe step for act routing test",
        args=args or {},
        risk_level=RiskLevel.R0_READ_ONLY,
        expected_observation="ok",
    )
    plan = Plan(task_id=task.id, goal="act-routing probe", steps=[step])
    db.upsert_model("plans", plan)
    return task, plan, step


def test_file_owned_step_calls_file_agent_act_before_tool_execute():
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.file_probe", calls))
    agent = RecordingAgent(AgentAction(kind="propose_tool", tool_name="test.file_probe", args={"path": "a.txt"}))
    orchestrator.subagents["FileAgent"] = agent
    task, plan, _step = _task_and_plan("test.file_probe")

    asyncio.run(orchestrator._process_steps(task, plan))

    assert len(agent.calls) == 1
    assert calls == [{"tool": "test.file_probe", "args": {"path": "a.txt"}}]


def test_real_subagent_fast_path_uses_orchestrator_registry():
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry = ToolRegistry()
    tool = _schema_tool(
        "test.local_only",
        calls,
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
    tool.fast_path_eligible = True
    orchestrator.registry.register(tool)
    orchestrator.subagents["FileAgent"] = FileAgent(orchestrator.bus)
    task, plan, step = _task_and_plan("test.local_only", args={"query": "invoice"})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == [{"tool": "test.local_only", "args": {"query": "invoice"}}]
    assert step.status == StepStatus.SUCCEEDED
    assert not any(
        message.message_type == MessageType.REVISION
        and (message.structured_payload or {}).get("revision_requested")
        for message in orchestrator.bus.get_messages(task.id)
    )


def test_propose_tool_can_correct_final_tool_and_args_before_safety_and_execute():
    original_calls: list[dict[str, Any]] = []
    final_calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.original", original_calls))
    orchestrator.registry.register(
        _schema_tool(
            "test.corrected",
            final_calls,
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
    )
    orchestrator.subagents["FileAgent"] = RecordingAgent(
        AgentAction(kind="propose_tool", tool_name="test.corrected", args={"query": "invoice"})
    )
    task, plan, step = _task_and_plan("test.original", args={"query": "old"})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert original_calls == []
    assert final_calls == [{"tool": "test.corrected", "args": {"query": "invoice"}}]
    assert step.tool_name == "test.corrected"
    persisted_plan = db.fetch_one("plans", plan.id)
    assert persisted_plan["steps"][0]["tool_name"] == "test.corrected"
    assert persisted_plan["steps"][0]["args"] == {"query": "invoice"}
    review_rows = db.fetch_many("safety_reviews", "task_id = ? AND step_id = ?", (task.id, step.id), limit=100)
    assert any(row["target_type"] == "tool_call" and row["risk_level"] == "R0_READ_ONLY" for row in review_rows)


def test_request_revision_publishes_bus_message_and_does_not_execute_or_loop():
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.file_probe", calls))
    agent = RecordingAgent(
        AgentAction(
            kind="request_revision",
            rationale="Missing destination",
            follow_up_question="Which folder should receive the file?",
        )
    )
    orchestrator.subagents["FileAgent"] = agent
    task, plan, step = _task_and_plan("test.file_probe")

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == []
    assert len(agent.calls) == 1
    assert step.status == StepStatus.SKIPPED
    assert task.status == TaskPhase.EXECUTION
    assert task.execution_stage == ExecutionStage.PAUSED
    messages = orchestrator.bus.get_messages(task.id)
    assert any(
        m.message_type == MessageType.REVISION
        and m.to_agent == "PlannerAgent"
        and "Which folder" in m.content
        for m in messages
    )
    assert any(
        (m.structured_payload or m.metadata.get("structured_payload") or {}).get("loop_guard") == "single_step_pause"
        for m in messages
    )


def test_request_revision_pauses_before_later_plan_steps_execute():
    first_calls: list[dict[str, Any]] = []
    second_calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.first", first_calls))
    orchestrator.registry.register(_tool("test.second", second_calls))
    orchestrator.subagents["FileAgent"] = RecordingAgent(
        AgentAction(kind="request_revision", follow_up_question="Need a corrected first step.")
    )
    task, plan, _step = _task_and_plan("test.first")
    plan.steps.append(
        PlanStep(
            task_id=task.id,
            order=1,
            agent_name="FileAgent",
            tool_name="test.second",
            description="Later step should wait for revision",
            args={},
            risk_level=RiskLevel.R0_READ_ONLY,
        )
    )
    db.upsert_model("plans", plan)

    asyncio.run(orchestrator._process_steps(task, plan))

    assert first_calls == []
    assert second_calls == []
    assert task.status == TaskPhase.EXECUTION
    assert task.execution_stage == ExecutionStage.PAUSED


def test_done_action_skips_step_safely():
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.file_probe", calls))
    orchestrator.subagents["FileAgent"] = RecordingAgent(
        AgentAction(kind="done", rationale="Previous observation already satisfied this step.")
    )
    task, plan, step = _task_and_plan("test.file_probe")

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == []
    assert step.status == StepStatus.SKIPPED
    assert task.status == TaskPhase.COMPLETED


def test_routed_step_produces_observation_from_owning_subagent():
    orchestrator = OrchestratorAgent()
    task, plan, _step = _task_and_plan("system.get_info", agent_name="UnknownAgent")

    asyncio.run(orchestrator._process_steps(task, plan))

    messages = orchestrator.bus.get_messages(task.id)
    assert any(
        m.from_agent == "ComputerAgent"
        and m.message_type == MessageType.OBSERVATION
        for m in messages
    )


def test_approved_step_pauses_when_subagent_changes_approved_tool_call():
    calls: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.registry.register(_tool("test.file_probe", calls))
    orchestrator.subagents["FileAgent"] = RecordingAgent(
        AgentAction(kind="propose_tool", tool_name="test.file_probe", args={"path": "changed.txt"})
    )
    task, plan, step = _task_and_plan("test.file_probe", args={"path": "approved.txt"})
    step.status = StepStatus.WAITING_USER_APPROVAL
    db.upsert_model("plans", plan)
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    preview: dict[str, Any] = {"ok": True}
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve test.file_probe",
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

    updated = asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []
    assert updated.status == TaskPhase.EXECUTION
    assert updated.execution_stage == ExecutionStage.PAUSED
    messages = orchestrator.bus.get_messages(task.id)
    assert any(
        m.message_type == MessageType.REVIEW and m.to_agent == "PlannerAgent"
        for m in messages
    )
