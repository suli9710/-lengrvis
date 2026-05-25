from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.planner_agent import PlannerAgent
from app.core import db
from app.core.schemas import AgentAction, Plan, PlanStep, StepStatus, Task, TaskStatus
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


class PassthroughAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return AgentAction(kind="propose_tool", tool_name=step.tool_name, args=dict(step.args))

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "ok"


def _recording_tool(
    name: str,
    events: list[dict[str, Any]],
    *,
    sleep_seconds: float,
    risk: RiskLevel = RiskLevel.R0_READ_ONLY,
):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        label = str(args["label"])
        events.append({"label": label, "phase": "start", "time": time.perf_counter()})
        time.sleep(sleep_seconds)
        events.append({"label": label, "phase": "end", "time": time.perf_counter()})
        return {"ok": True, "label": label, "changed_paths": [str(args["path"])] if args.get("path") else []}

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
    )


def _step(step_id: str, tool_name: str, *, depends_on: list[str] | None = None, args: dict[str, Any] | None = None):
    return PlanStep(
        id=step_id,
        task_id="",
        order=int(step_id[1:]) if step_id[1:].isdigit() else 0,
        agent_name="FileAgent",
        tool_name=tool_name,
        description=f"Run {step_id}",
        args=args or {"label": step_id},
        expected_observation=f"{step_id} complete",
        risk_level=RiskLevel.R0_READ_ONLY,
        depends_on=depends_on or [],
    )


def _task_and_plan(steps: list[PlanStep]):
    task = Task(user_goal="parallel steps", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    db.upsert_model("tasks", task)
    for index, step in enumerate(steps, start=1):
        step.task_id = task.id
        step.order = index
    plan = Plan(task_id=task.id, goal="parallel steps", steps=steps)
    db.upsert_model("plans", plan)
    return task, plan


def test_dependency_scheduler_runs_ready_steps_in_parallel_then_dependents():
    events: list[dict[str, Any]] = []
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(_recording_tool("test.parallel", events, sleep_seconds=0.08))
    task, plan = _task_and_plan(
        [
            _step("A", "test.parallel", args={"label": "A"}),
            _step("B", "test.parallel", args={"label": "B"}),
            _step("C", "test.parallel", depends_on=["A"], args={"label": "C"}),
            _step("D", "test.parallel", depends_on=["B", "C"], args={"label": "D"}),
        ]
    )

    asyncio.run(orchestrator._process_steps(task, plan))

    starts = {event["label"]: event["time"] for event in events if event["phase"] == "start"}
    ends = {event["label"]: event["time"] for event in events if event["phase"] == "end"}

    assert task.status == TaskStatus.COMPLETED
    assert {step.id: step.status for step in plan.steps} == {
        "A": StepStatus.SUCCEEDED,
        "B": StepStatus.SUCCEEDED,
        "C": StepStatus.SUCCEEDED,
        "D": StepStatus.SUCCEEDED,
    }
    assert starts["A"] < ends["B"]
    assert starts["B"] < ends["A"]
    assert starts["C"] >= ends["A"]
    assert starts["D"] >= ends["B"]
    assert starts["D"] >= ends["C"]


def test_planner_payload_preserves_step_ids_and_dependencies():
    plan = PlannerAgent()._payload_to_plan(
        "task-deps",
        {
            "goal": "dependency metadata",
            "steps": [
                {
                    "id": "A",
                    "agent_name": "FileAgent",
                    "tool_name": "file.search_by_name",
                    "description": "A",
                    "args": {"query": "a"},
                    "risk_level": "R0_READ_ONLY",
                    "depends_on": [],
                },
                {
                    "id": "B",
                    "agent_name": "FileAgent",
                    "tool_name": "file.search_by_name",
                    "description": "B",
                    "args": {"query": "b"},
                    "risk_level": "R0_READ_ONLY",
                    "depends_on": ["A"],
                },
            ],
        },
    )

    assert [step.id for step in plan.steps] == ["A", "B"]
    assert plan.steps[1].depends_on == ["A"]


def test_write_steps_for_same_directory_are_serialized(tmp_path: Path):
    events: list[dict[str, Any]] = []
    workspace = tmp_path / "workspace"
    same_dir = workspace / "reports"
    same_dir.mkdir(parents=True, exist_ok=True)
    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = PassthroughAgent()
    orchestrator.registry.register(
        _recording_tool(
            "test.write",
            events,
            sleep_seconds=0.08,
        )
    )
    first = same_dir / "one.txt"
    second = same_dir / "two.txt"
    task, plan = _task_and_plan(
        [
            _step(
                "A",
                "test.write",
                args={"label": "A", "path": str(first), "dry_run": False},
            ),
            _step(
                "B",
                "test.write",
                args={"label": "B", "path": str(second), "dry_run": False},
            ),
        ]
    )

    asyncio.run(orchestrator._process_steps(task, plan))

    starts = {event["label"]: event["time"] for event in events if event["phase"] == "start"}
    ends = {event["label"]: event["time"] for event in events if event["phase"] == "end"}

    assert task.status == TaskStatus.COMPLETED
    assert starts["B"] >= ends["A"] or starts["A"] >= ends["B"]
