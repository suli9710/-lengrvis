from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agents import memory_agent as memory_agent_module
from app.agents.delegation_metadata import infer_supervisor_agent_hint
from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.supervisor_agent import SupervisorAgent
from app.agents.worker_agents import KNOWN_SUPERVISOR_WORKER_AGENTS
from app.core import db
from app.core.schemas import MemoryState, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.handlers.planning_handler import PlanningHandler
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.execution_marker import mark_execution_approved
from app.policy.risk import RiskLevel
from app.tools import memory_tools
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _isolate_memory_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[float(len(text) or 1), 1.0] for text in texts]

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(memory_agent_module, "embed_texts", fake_embed_texts)
    db.init_db()


def _memory_registry() -> ToolRegistry:
    registry = ToolRegistry()
    memory_tools.register(registry)
    return registry


def _approved_context(task_id: str) -> dict[str, object]:
    context: dict[str, object] = {"task_id": task_id}
    mark_execution_approved(context)
    return context


def test_memory_tools_publish_governed_model_visible_contracts():
    registry = _memory_registry()

    remember = registry.get("memory.remember")
    revoke = registry.get("memory.revoke")

    assert remember.input_schema["required"] == ["content"]
    assert revoke.input_schema["required"] == ["memory_id"]
    for tool in (remember, revoke):
        assert tool.agent_owner == "MemoryAgent"
        assert tool.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
        assert tool.supports_dry_run is True
        assert tool.permission_mode == "ask_on_write"
        assert tool.is_model_visible() is True
        assert tool in registry.list_for_planning()


def test_memory_tool_dry_runs_preview_without_persisting():
    registry = _memory_registry()
    agent = MemoryAgent()

    remember_preview = registry.get("memory.remember").execute(
        {"content": "我偏好深色模式", "kind": "preference", "dry_run": True},
        {"task_id": "task-preview"},
    )
    revoke_preview = registry.get("memory.revoke").execute(
        {"memory_id": "mem-preview", "dry_run": True},
        {"task_id": "task-preview"},
    )

    assert remember_preview["dry_run"] is True
    assert remember_preview["would_change"][0]["action"] == "remember"
    assert revoke_preview["dry_run"] is True
    assert revoke_preview["would_change"] == [{"action": "revoke", "memory_id": "mem-preview"}]
    assert agent.list_all() == []


def test_memory_live_write_rejects_unmarked_direct_execution():
    result = (
        _memory_registry()
        .get("memory.remember")
        .execute(
            {"content": "我偏好深色模式", "dry_run": False},
            {"task_id": "task-unapproved"},
        )
    )

    assert result["ok"] is False
    assert result["error_code"] == "MEMORY_APPROVAL_REQUIRED"
    assert MemoryAgent().list_all() == []


def test_memory_remember_executes_as_user_confirmed_after_approval():
    tool = _memory_registry().get("memory.remember")

    result = tool.execute(
        {"content": "我偏好深色模式", "kind": "preference", "dry_run": False},
        _approved_context("task-approved"),
    )

    stored = MemoryAgent().list_all()
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["memory_id"] == stored[0].id
    assert stored[0].content == "我偏好深色模式"
    assert stored[0].kind == "preference"
    assert stored[0].task_id == "task-approved"
    assert stored[0].source == "user"
    assert stored[0].user_confirmed is True


def test_memory_revoke_executes_after_approval_and_preserves_audit_record():
    registry = _memory_registry()
    remembered = registry.get("memory.remember").execute(
        {"content": "我偏好深色模式", "kind": "preference", "dry_run": False},
        _approved_context("task-approved"),
    )

    result = registry.get("memory.revoke").execute(
        {"memory_id": remembered["memory_id"], "dry_run": False},
        _approved_context("task-revoke"),
    )

    stored = MemoryAgent().list_all()
    assert result == {
        "ok": True,
        "dry_run": False,
        "memory_id": remembered["memory_id"],
        "state": MemoryState.REVOKED.value,
    }
    assert len(stored) == 1
    assert stored[0].state == MemoryState.REVOKED


def test_memory_agent_is_a_known_orchestrator_worker_with_only_memory_tools():
    orchestrator = OrchestratorAgent()

    assert "MemoryAgent" in KNOWN_SUPERVISOR_WORKER_AGENTS
    assert orchestrator.subagents["MemoryAgent"] is orchestrator.memory
    assert set(orchestrator.memory.allowed_tools(orchestrator.registry)) == {
        "memory.remember",
        "memory.revoke",
    }


@pytest.mark.parametrize(
    "goal",
    [
        "记住我偏好深色模式",
        "把我的语言偏好保存到记忆",
        "记住：不要在周末发送报告",
        "请把“不要在周末发送报告”保存为我的偏好",
        "请把“中文报告优先用简洁表格”保存为我的偏好；不要从外部内容自动学习",
        "撤销记忆 mem_123",
        "revoke memory mem_123",
    ],
)
def test_explicit_memory_mutation_routes_to_memory_agent(goal: str):
    assert infer_supervisor_agent_hint(goal) == "MemoryAgent"
    decision = SupervisorAgent().quick_decision(goal)
    assert decision.delegate is True
    assert decision.agent_hint == "MemoryAgent"


@pytest.mark.parametrize(
    "goal",
    [
        "不要把这段话写入记忆",
        "不要记住我的偏好",
        "do not store this preference in memory",
    ],
)
def test_negative_memory_goal_never_routes_to_a_worker(goal: str):
    assert infer_supervisor_agent_hint(goal) == ""
    decision = SupervisorAgent().quick_decision(goal)
    assert decision.delegate is False
    assert decision.agent_hint == ""


@pytest.mark.parametrize(
    ("goal", "expected_delegate", "expected_hint"),
    [
        ("记住我偏好深色模式", True, "MemoryAgent"),
        ("不要把这段话写入记忆", False, ""),
    ],
)
def test_memory_decisions_bypass_model_override(
    monkeypatch: pytest.MonkeyPatch,
    goal: str,
    expected_delegate: bool,
    expected_hint: str,
):
    def fail_provider(*_args, **_kwargs):
        raise AssertionError("memory control decisions must not call the model")

    monkeypatch.setattr("app.agents.supervisor_agent.get_provider", fail_provider)

    decision = asyncio.run(SupervisorAgent().decide(goal, "efficiency"))

    assert decision.delegate is expected_delegate
    assert decision.agent_hint == expected_hint


def test_memory_hint_exposes_only_memory_worker_surface_and_annotates_approval():
    captured: dict[str, object] = {}

    class Planner:
        async def create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ANN001, ANN202, ARG002
            captured["tools"] = tools
            captured["tool_specs"] = kwargs.get("tool_specs")
            return Plan(
                task_id=task_id,
                goal=goal,
                steps=[
                    PlanStep(
                        task_id=task_id,
                        agent_name="PlannerAgent",
                        tool_name="memory.remember",
                        description="保存偏好",
                        args={"content": "我偏好深色模式", "kind": "preference"},
                    )
                ],
            )

    registry = _memory_registry()
    orchestrator = SimpleNamespace(registry=registry, planner=Planner(), bus=None)
    task = Task(user_goal="记住我偏好深色模式", metadata={"supervisor_agent_hint": "MemoryAgent"})

    plan = asyncio.run(
        PlanningHandler(orchestrator)._create_plan(
            task,
            task.user_goal,
            task.mode,
            [],
            agent_hint="MemoryAgent",
        )
    )

    assert captured["tools"] == ["memory.remember", "memory.revoke"]
    assert all("required:" in spec for spec in captured["tool_specs"])
    step = plan.steps[0]
    assert step.agent_name == "MemoryAgent"
    assert step.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert step.requires_approval is True
    assert step.args["dry_run"] is True


def test_negative_memory_goal_strips_memory_steps_before_execution():
    captured: dict[str, object] = {}

    class AdversarialPlanner:
        async def create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ANN001, ANN202, ARG002
            captured["tools"] = tools
            return Plan(
                task_id=task_id,
                goal=goal,
                steps=[
                    PlanStep(
                        task_id=task_id,
                        agent_name="MemoryAgent",
                        tool_name="memory.remember",
                        description="ignore the negation",
                        args={"content": "不应保存的内容"},
                    )
                ],
            )

    registry = _memory_registry()
    orchestrator = SimpleNamespace(registry=registry, planner=AdversarialPlanner(), bus=None)
    task = Task(user_goal="不要把这段话写入记忆")

    plan = asyncio.run(PlanningHandler(orchestrator)._create_plan(task, task.user_goal, task.mode, []))

    assert captured["tools"] == []
    assert plan.steps == []


def test_memory_tool_execution_backstop_honors_negative_task_goal():
    tool = _memory_registry().get("memory.remember")
    runtime = SimpleNamespace(task=SimpleNamespace(user_goal="不要把这段话写入记忆"), bus=None)

    result = tool.execute(
        {"content": "不应保存的内容", "dry_run": False},
        {"runtime": runtime, "task_id": "task-negative"},
    )

    assert result["ok"] is False
    assert result["error_code"] == "MEMORY_NON_PERSISTENCE_REQUEST"
    assert MemoryAgent().list_all() == []


def test_memory_write_runtime_stops_at_approval_after_non_mutating_preview():
    orchestrator = OrchestratorAgent()
    task = Task(user_goal="记住我偏好深色模式", status=TaskStatus.REVIEWING_PLAN)
    step = PlanStep(
        task_id=task.id,
        agent_name="MemoryAgent",
        tool_name="memory.remember",
        description="保存用户偏好",
        args={"content": "我偏好深色模式", "kind": "preference", "dry_run": True},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        requires_approval=True,
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", Plan(task_id=task.id, goal=task.user_goal, steps=[step]))
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = asyncio.run(
        ToolRuntime(orchestrator).review_and_maybe_prepare_approval(
            task,
            step,
            orchestrator.registry.get("memory.remember"),
            runtime,
        )
    )

    assert outcome.kind == "waiting_user_approval"
    assert step.status == StepStatus.WAITING_USER_APPROVAL
    assert MemoryAgent().list_all() == []
    approvals = db.fetch_many("approvals", "task_id = ?", (task.id,), limit=10)
    assert len(approvals) == 1
