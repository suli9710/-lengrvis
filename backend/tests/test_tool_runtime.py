from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.risk import RiskLevel
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    db.init_db()
    register_all_tools()
    yield


class DoneAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return None

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "reflected"


def _task_plan_step(tool_name: str, args: dict[str, Any] | None = None):
    task = Task(user_goal="runtime", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name=tool_name,
        description="runtime step",
        args=args or {},
        expected_observation="runtime ok",
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    plan = Plan(task_id=task.id, goal="runtime", steps=[step])
    db.upsert_model("plans", plan)
    return task, plan, step


def test_tool_runtime_validation_failure_blocks_execution():
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    def validate(args, context):  # noqa: ANN001, ANN202, ARG001
        raise ValueError("missing required runtime field")

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.runtime_validate",
            description="runtime validate",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            validate_input=validate,
        )
    )
    task, plan, step = _task_plan_step("test.runtime_validate")

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == []
    assert step.status == StepStatus.FAILED
    assert task.status == TaskStatus.FAILED


def test_tool_runtime_persists_large_result_preview(tmp_path: Path):
    large_text = "x" * 500

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        return {"blob": large_text}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.large_result",
            description="large result",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            max_result_size=100,
        )
    )
    task, plan, step = _task_plan_step("test.large_result")

    asyncio.run(orchestrator._process_steps(task, plan))

    rows = db.fetch_many("tool_results", limit=10)
    result = next(row for row in rows if row["tool_call_id"].startswith("tool_"))
    output = result["output"]
    assert output["persisted_result"] is True
    assert Path(output["path"]).exists()
    assert output["original_size"] > 100
    assert step.status == StepStatus.SUCCEEDED


def test_approved_tool_runtime_persists_large_result_preview(tmp_path: Path):
    large_text = "approved-output-" * 60

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        return {"blob": large_text, "approved": args.get("approved"), "approval_id": args.get("approval_id")}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.approved_large_result",
            description="approved large result",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            max_result_size=120,
        )
    )
    task, plan, step = _task_plan_step("test.approved_large_result")
    step.status = StepStatus.WAITING_USER_APPROVAL
    db.upsert_model("plans", plan)
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve large result")
    db.upsert_model("approvals", approval)

    asyncio.run(orchestrator.execute_approved_step(approval))

    rows = db.fetch_many("tool_results", limit=10)
    result = next(row for row in rows if row["tool_call_id"].startswith("tool_"))
    output = result["output"]
    assert output["persisted_result"] is True
    assert Path(output["path"]).exists()
    assert output["original_size"] > 120
    refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    assert refreshed_plan.steps[0].status == StepStatus.SUCCEEDED


def test_write_locks_are_shared_across_runtime_instances(tmp_path: Path):
    events: list[tuple[str, str, float]] = []
    target = tmp_path / "workspace" / "same.txt"
    target.parent.mkdir(parents=True, exist_ok=True)

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        import time

        label = str(args["label"])
        events.append((label, "start", time.perf_counter()))
        time.sleep(0.05)
        events.append((label, "end", time.perf_counter()))
        return {"ok": True, "changed_paths": [str(args["path"])]}

    tool = ToolDefinition(
        name="test.shared_write_lock",
        description="shared write lock",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        concurrency_key="shared-write",
    )
    first = OrchestratorAgent()
    second = OrchestratorAgent()
    task_a, _plan_a, step_a = _task_plan_step("test.shared_write_lock", {"label": "A", "path": str(target)})
    task_b, _plan_b, step_b = _task_plan_step("test.shared_write_lock", {"label": "B", "path": str(target)})
    runtime_a = TaskRuntimeContext.from_task(task_a, first.step_execution_handler._runtime_context(task_a).settings, first.bus)
    runtime_b = TaskRuntimeContext.from_task(task_b, second.step_execution_handler._runtime_context(task_b).settings, second.bus)

    async def run_both():
        await asyncio.gather(
            ToolRuntime(first).execute_tool_with_locks(tool, step_a, step_a.args, runtime_a.tool_context(), threaded=True),
            ToolRuntime(second).execute_tool_with_locks(tool, step_b, step_b.args, runtime_b.tool_context(), threaded=True),
        )

    asyncio.run(run_both())

    starts = {label: timestamp for label, phase, timestamp in events if phase == "start"}
    ends = {label: timestamp for label, phase, timestamp in events if phase == "end"}
    assert starts["B"] >= ends["A"] or starts["A"] >= ends["B"]
