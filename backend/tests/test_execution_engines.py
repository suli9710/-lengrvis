from __future__ import annotations

import sys

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.config import AppSettings
from app.core import db
from app.core.schemas import AgentAction, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.lengrvis_code_config import LengrvisCodeConfig, default_allowed_tools
from app.orchestration.developer_engine import DeveloperExecutionEngine
from app.orchestration.engine_router import EngineRouter, configured_default_engine, configured_max_turns, route_engine
from app.orchestration.execution_engine import InMemoryRunStore
from app.orchestration.execution_models import RunPhase, RunState
from app.orchestration.os_execution_engine import OSExecutionEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def test_route_engine_auto_selects_developer_for_read_only_repo_goals() -> None:
    decision = route_engine("inspect repository git status")

    assert decision.selected_engine == "developer"
    assert decision.requested_engine == "auto"


@pytest.mark.parametrize(
    "goal",
    [
        "fix failing backend pytest around planner imports",
        "make failing pytest pass",
        "address failing backend tests",
        "rename module import",
    ],
)
def test_route_engine_auto_selects_os_for_write_intent_repo_goals(goal: str) -> None:
    decision = route_engine(goal)

    assert decision.selected_engine == "os"
    assert decision.requested_engine == "auto"
    assert "write-intent" in decision.reason


def test_route_engine_auto_write_intent_ignores_developer_fallback() -> None:
    decision = route_engine("implement missing backend test coverage", fallback_engine="developer")

    assert decision.selected_engine == "os"
    assert decision.requested_engine == "auto"


def test_route_engine_auto_selects_os_for_browser_goals() -> None:
    decision = route_engine("open the browser and click the account settings")

    assert decision.selected_engine == "os"


def test_route_engine_auto_selects_os_for_chinese_system_diagnostics() -> None:
    decision = route_engine("帮我检查这台电脑", fallback_engine="developer")

    assert decision.selected_engine == "os"
    assert decision.requested_engine == "auto"
    assert "system diagnostics" in decision.reason


def test_route_engine_explicit_override_wins() -> None:
    decision = route_engine("fix backend tests", requested_engine="os")

    assert decision.selected_engine == "os"
    assert decision.reason == "explicit engine override"


def test_default_engine_env_hooks_accept_agent_loop_names() -> None:
    env = {
        "LENGRVIS_DEFAULT_ENGINE": "developer",
        "LENGRVIS_AGENT_LOOP_MAX_TURNS": "5",
    }

    assert configured_default_engine(env) == "developer"
    assert configured_max_turns(env) == 5


def test_default_engine_env_keeps_legacy_agent_loop_name() -> None:
    assert configured_default_engine({"LENGRVIS_AGENT_LOOP_DEFAULT_ENGINE": "developer"}) == "developer"


@pytest.mark.asyncio
async def test_developer_engine_run_turn_uses_lengrvis_code_adapter(tmp_path) -> None:
    fake_cli = tmp_path / "fake_lengrvis.py"
    fake_cli.write_text(
        """
from __future__ import annotations

import json

print(json.dumps({"type": "system", "subtype": "init", "tools": ["Read"]}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Working"}, {"type": "tool_use", "name": "Read", "input": {"file_path": "sample.py"}}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Fake developer result"}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    store = InMemoryRunStore()
    engine = DeveloperExecutionEngine(
        settings=AppSettings(
            allowed_directories=[str(tmp_path)],
            api_key="test-api-key",
            model="openai/gpt-5",
        ),
        store=store,
        lengrvis_code_config=LengrvisCodeConfig(command=(sys.executable, "-u", str(fake_cli)), max_turns=2),
        use_lengrvis_code=True,
    )

    state = await engine.start_run("inspect goal-token implementation", "efficiency", "developer")
    result = await engine.run_turn(state)

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert result.state.current_plan["writes_enabled"] is False
    assert result.state.current_plan["allowed_tools"] == list(default_allowed_tools())
    assert result.outputs["lengrvis_code"]["ok"] is True
    assert result.outputs["lengrvis_code"]["tool_events"][0]["name"] == "Read"
    assert result.state.observations[0].source == "lengrvis_code.stream_json"


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_tool", ["Edit", "Agent"])
async def test_developer_engine_rejects_write_capable_tools_before_launch(tmp_path, monkeypatch, unsafe_tool: str) -> None:
    async def fail_run_lengrvis_code(*args, **kwargs):  # noqa: ANN001, ANN202, ARG001
        raise AssertionError("unsafe developer tool allowlists must be rejected before launch")

    monkeypatch.setattr("app.orchestration.developer_engine.run_lengrvis_code", fail_run_lengrvis_code)
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)], api_key="test-api-key"),
        store=InMemoryRunStore(),
        lengrvis_code_config=LengrvisCodeConfig(command=(sys.executable, "-c", "print('should not run')"), allowed_tools=("Read", unsafe_tool)),
    )

    state = await engine.start_run("edit repository files", "efficiency", "developer")
    result = await engine.run_turn(state)

    assert state.phase == RunPhase.FAILED
    assert state.current_plan["allowed_tools"] == []
    assert state.current_plan["writes_enabled"] is False
    assert state.current_plan["lengrvis_code_enabled"] is False
    assert state.current_plan["steps"][0]["status"] == "failed"
    assert "Unsafe Lengrvis Code allowedTools" in state.current_plan["safety_error"]
    assert result.finished is True
    assert result.state.phase == RunPhase.FAILED
    assert result.message == "Run is already failed."


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED, RunPhase.DENIED])
async def test_developer_engine_does_not_execute_non_executable_phases(tmp_path, monkeypatch, phase) -> None:
    async def fail_run_lengrvis_code(*args, **kwargs):  # noqa: ANN001, ANN202, ARG001
        raise AssertionError("non-executable developer phases must not invoke Lengrvis Code")

    monkeypatch.setattr("app.orchestration.developer_engine.run_lengrvis_code", fail_run_lengrvis_code)
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)], api_key="test-api-key"),
        store=InMemoryRunStore(),
    )
    state = RunState(run_id=f"devrun_{phase.value}", engine="developer", phase=phase, goal="inspect repository")

    result = await engine.run_turn(state)

    assert result.finished is True
    assert result.state.phase == phase
    assert result.message == f"Run is already {phase.value}."


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED])
async def test_engine_router_preserves_non_executable_phase_at_max_turns(tmp_path, phase) -> None:
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)], api_key="test-api-key"),
        store=InMemoryRunStore(),
    )
    router = EngineRouter({"developer": engine}, default_engine="developer", max_turns=1)
    state = RunState(
        run_id=f"devrun_router_{phase.value}",
        engine="developer",
        phase=phase,
        goal="inspect repository",
        turn_count=1,
    )

    result = await router.run_turn(state)

    assert result.finished is True
    assert result.state.phase == phase
    assert result.message == f"Run is already {phase.value}."


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


class NoRecoveryAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return AgentAction(kind="propose_tool", tool_name=step.tool_name, args=dict(step.args))

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "ok"


class DoneOnlyAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
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


@pytest.mark.asyncio
async def test_os_engine_reflection_adds_read_before_retry_for_resource_state_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_RECOVERY_MAX_RETRIES", "0")
    db.init_db()
    target = tmp_path / "edit.txt"
    target.write_text("alpha beta", encoding="utf-8")
    events: list[str] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneOnlyAgent()
    calls: list[dict] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {
            "error": "Existing files must be read before writing.",
            "error_code": "READ_STATE_REQUIRED",
            "resource_state_error": True,
            "replan_recommended": True,
            "missing_read_state": [{"path": str(target), "exists": True}],
        }

    orchestrator.registry.register(
        ToolDefinition(
            name="test.resource_guard",
            description="resource guard",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            trust_tier="builtin",
            effects=["read"],
            fast_path_eligible=True,
        )
    )
    task = Task(user_goal="edit stale file", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="test.resource_guard",
        description="Trigger resource guard",
        args={"path": str(target)},
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    engine = OSExecutionEngine(orchestrator, store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan, event_hook=lambda name, payload: events.append(name))

    assert result.finished is False
    assert result.state.phase == RunPhase.RUNNING
    assert result.outputs["outcome"] == "reflected"
    assert "os.reflection.started" in events
    assert step.status == StepStatus.SKIPPED
    assert result.outputs["step_outcomes"][0]["kind"] == "failed"
    added = [item for item in plan.steps if item.id != step.id]
    assert [item.tool_name for item in added] == ["file.read_text", "test.resource_guard"]
    assert added[1].depends_on == [added[0].id]
    assert result.state.recovery_count_by_step[step.id] == 1


@pytest.mark.asyncio
async def test_os_engine_reflection_limit_pauses_low_information_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_RECOVERY_MAX_RETRIES", "0")
    db.init_db()
    calls: list[dict] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = NoRecoveryAgent()
    orchestrator.registry.register(_runtime_tool("test.primary_fail", calls, ok=False))
    task, plan, step = _task_plan("test.primary_fail")
    state = RunState(
        run_id=f"os_{task.id}",
        engine="os",
        phase=RunPhase.RUNNING,
        task_id=task.id,
        goal=task.user_goal,
        mode=task.mode,
        current_plan={"task_id": task.id, "plan_id": plan.id, "steps": [step.model_dump(mode="json")]},
        recovery_count_by_step={step.id: 1, "__os_reflection_run__": 1},
    )
    engine = OSExecutionEngine(orchestrator, store=InMemoryRunStore())

    result = await engine.run_plan_turn(task, plan, state=state)

    assert result.finished is True
    assert result.state.phase == RunPhase.FAILED
    assert step.status == StepStatus.FAILED
    assert calls == [{"label": "primary"}]


def test_os_reflection_decider_respects_configured_limits() -> None:
    from app.orchestration.os_reflection import OSReflectionDecider, OSReflectionInput

    task = Task(user_goal="os engine", mode="efficiency", status=TaskStatus.EXECUTION)
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[])

    def _input(run_count: int) -> OSReflectionInput:
        return OSReflectionInput(
            task=task,
            plan=plan,
            turn=1,
            run_reflection_count=run_count,
            step_reflection_counts={},
            graph_error="graph build failed",
        )

    # Default cap of 2 stops reflecting once the run has reflected twice.
    assert OSReflectionDecider().should_reflect(_input(2)) is False
    # A higher per-run cap keeps reflecting at the same point.
    assert OSReflectionDecider(max_per_run=3).should_reflect(_input(2)) is True
    # A zero cap disables reflection entirely, even on a graph error.
    assert OSReflectionDecider(max_per_run=0).should_reflect(_input(0)) is False

