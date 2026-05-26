from __future__ import annotations

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.config import AppSettings
from app.core import db
from app.core.schemas import AgentAction, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.developer_engine import DeveloperExecutionEngine, readonly_developer_tool_names
from app.orchestration.engine_router import EngineRouter, configured_default_engine, configured_max_turns, route_engine
from app.orchestration.execution_engine import InMemoryRunStore
from app.orchestration.execution_models import RunPhase
from app.orchestration.os_execution_engine import OSExecutionEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def test_route_engine_auto_selects_developer_for_repo_goals() -> None:
    decision = route_engine("fix failing backend pytest around planner imports")

    assert decision.selected_engine == "developer"
    assert decision.requested_engine == "auto"


def test_route_engine_auto_selects_os_for_browser_goals() -> None:
    decision = route_engine("open the browser and click the account settings")

    assert decision.selected_engine == "os"


def test_route_engine_explicit_override_wins() -> None:
    decision = route_engine("fix backend tests", requested_engine="os")

    assert decision.selected_engine == "os"
    assert decision.reason == "explicit engine override"


def test_default_engine_env_hooks_accept_agent_loop_names() -> None:
    env = {
        "MARVIS_DEFAULT_ENGINE": "developer",
        "MARVIS_AGENT_LOOP_MAX_TURNS": "5",
    }

    assert configured_default_engine(env) == "developer"
    assert configured_max_turns(env) == 5


def test_default_engine_env_keeps_legacy_agent_loop_name() -> None:
    assert configured_default_engine({"MARVIS_AGENT_LOOP_DEFAULT_ENGINE": "developer"}) == "developer"


@pytest.mark.asyncio
async def test_developer_engine_run_turn_uses_readonly_tools(tmp_path) -> None:
    (tmp_path / "sample.py").write_text("def sample():\n    return 'goal-token'\n", encoding="utf-8")
    store = InMemoryRunStore()
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)]),
        store=store,
    )

    state = await engine.start_run("inspect goal-token implementation", "privacy", "developer")
    result = await engine.run_turn(state)

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert result.state.current_plan["writes_enabled"] is False
    assert result.state.current_plan["allowed_tools"] == list(readonly_developer_tool_names())
    assert {observation.source for observation in result.state.observations} == {
        "dev.git_status",
        "dev.diff_preview",
        "dev.pytest_inventory",
        "dev.grep",
    }


@pytest.mark.asyncio
async def test_engine_router_resumes_and_cancels_by_run_id(tmp_path) -> None:
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)]),
        store=InMemoryRunStore(),
    )
    router = EngineRouter({"developer": engine}, default_engine="developer")

    state = await router.start_run("inspect repository", engine="developer")
    resumed = await router.resume_run(state.run_id)
    cancelled = await router.cancel_run(state.run_id)

    assert resumed.run_id == state.run_id
    assert cancelled.phase == RunPhase.CANCELLED


class PassthroughAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return AgentAction(kind="propose_tool", tool_name=step.tool_name, args=dict(step.args))

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "ok"


class RecoveryAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        if observation and not observation.ok:
            return AgentAction(kind="propose_tool", tool_name="test.recovery_ok", args={"label": "recovery"})
        return AgentAction(kind="propose_tool", tool_name=step.tool_name, args=dict(step.args))

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "ok"


def _runtime_tool(name: str, calls: list[dict], *, risk: RiskLevel = RiskLevel.R0_READ_ONLY, ok: bool = True) -> ToolDefinition:
    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        if not ok:
            return {"error": "planned failure"}
        if args.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "label": args.get("label", name)}
        return {"ok": True, "label": args.get("label", name)}

    return ToolDefinition(
        name=name,
        description=name,
        input_schema={},
        output_schema={},
        risk_level=risk,
        agent_owner="FileAgent",
        supports_dry_run=risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM},
        requires_authorized_path=False,
        execute=execute,
        trust_tier="builtin",
        effects=["read"] if risk == RiskLevel.R0_READ_ONLY else ["write"],
        resource_kinds=["test"],
        fast_path_eligible=True,
    )


def _task_plan(tool_name: str, *, risk: RiskLevel = RiskLevel.R0_READ_ONLY):
    task = Task(user_goal="os engine", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name=tool_name,
        description="Run OS engine test step",
        args={"label": "primary"},
        risk_level=risk,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    return task, plan, step


@pytest.mark.asyncio
async def test_os_engine_turn_emits_structured_outputs_and_uses_tool_runtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    calls: list[dict] = []
    events: list[tuple[str, dict]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_runtime_tool("test.os_turn", calls))
    task, plan, step = _task_plan("test.os_turn")
    engine = OSExecutionEngine(orchestrator, store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan, event_hook=lambda name, payload: events.append((name, payload)))

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert step.status == StepStatus.SUCCEEDED
    assert calls == [{"label": "primary"}]
    assert result.outputs["selected_step_ids"] == [step.id]
    assert any(name == "step.selected" for name, _payload in events)
    assert any(observation.payload["step_id"] == step.id for observation in result.state.observations)


@pytest.mark.asyncio
async def test_os_engine_waiting_approval_stays_inside_tool_runtime_safety_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    calls: list[dict] = []
    events: list[str] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_runtime_tool("test.os_write", calls, risk=RiskLevel.R2_REVERSIBLE_MODIFY))
    task, plan, step = _task_plan("test.os_write", risk=RiskLevel.R2_REVERSIBLE_MODIFY)
    engine = OSExecutionEngine(orchestrator, store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan, event_hook=lambda name, payload: events.append(name))

    approvals = db.fetch_many("approvals", "task_id = ? AND step_id = ?", (task.id, step.id), limit=10)
    assert result.finished is True
    assert result.state.phase == RunPhase.AWAITING_APPROVAL
    assert step.status == StepStatus.WAITING_USER_APPROVAL
    assert calls == [{"label": "primary", "dry_run": True}]
    assert approvals
    assert "approval.needed" in events


@pytest.mark.asyncio
async def test_os_engine_preserves_cancelled_task_phase(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="cancelled task", mode="efficiency", status=TaskStatus.CANCELLED)
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[])
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    events: list[str] = []
    engine = OSExecutionEngine(store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan, event_hook=lambda name, payload: events.append(name))

    assert result.finished is True
    assert result.state.phase == RunPhase.CANCELLED
    assert result.outputs["outcome"] == "cancelled"
    assert "run.cancelled" in events


@pytest.mark.asyncio
async def test_os_engine_recovery_step_runs_through_same_runtime_safety_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    calls: list[dict] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = RecoveryAgent()
    orchestrator.registry.register(_runtime_tool("test.primary_fail", calls, ok=False))
    orchestrator.registry.register(_runtime_tool("test.recovery_ok", calls))
    task, plan, step = _task_plan("test.primary_fail")
    engine = OSExecutionEngine(orchestrator, store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan)

    reviews = db.fetch_many("safety_reviews", "task_id = ?", (task.id,), limit=50)
    reviewed_step_ids = {review["step_id"] for review in reviews if review["target_type"] == "tool_call"}
    recovery_step = next(item for item in plan.steps if item.id != step.id)
    assert result.state.phase == RunPhase.COMPLETED
    assert step.status == StepStatus.SKIPPED
    assert recovery_step.status == StepStatus.SUCCEEDED
    assert calls == [{"label": "primary"}, {"label": "recovery"}]
    assert {step.id, recovery_step.id}.issubset(reviewed_step_ids)
