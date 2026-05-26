from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import StepPhase, set_step_status
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionStore, PermissionTimeWindow
from app.policy.risk import RiskLevel, SafetyVerdict
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
            trust_tier="builtin",
            effects=["read"],
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
    set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor="Test")
    task.execution_stage = ExecutionStage.AWAITING_APPROVAL
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    approval_preview: dict[str, Any] = {}
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve large result",
        diff_preview=approval_preview,
        tool_name=step.tool_name,
        risk_level=RiskLevel.R0_READ_ONLY.value,
        args_binding_hmac=args_binding_hmac(step.tool_name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(approval_preview),
        settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
        status=ApprovalStatus.APPROVED,
    )
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
    assert refreshed_plan.steps[0].step_phase == StepPhase.SUCCEEDED


def test_runtime_blocks_requires_authorized_path_tool_outside_allowed_directories(tmp_path: Path):
    calls: list[dict[str, Any]] = []
    outside = tmp_path / "outside" / "blocked.txt"

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.authorized_path_required",
            description="authorized path required",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=True,
            execute=execute,
            trust_tier="builtin",
            effects=["read"],
        )
    )
    task, plan, step = _task_plan_step("test.authorized_path_required", {"path": str(outside)})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == []
    assert step.status == StepStatus.DENIED
    assert task.status == TaskStatus.DENIED


def test_runtime_blocks_requires_authorized_path_tool_nested_outside_allowed_directories(tmp_path: Path):
    calls: list[dict[str, Any]] = []
    outside = tmp_path / "outside" / "nested.txt"

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.authorized_path_required_nested",
            description="authorized path required nested",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=True,
            execute=execute,
            trust_tier="builtin",
            effects=["read"],
        )
    )
    task, _plan, step = _task_plan_step(
        "test.authorized_path_required_nested",
        {"batch": [{"file_path": str(outside)}]},
    )

    asyncio.run(orchestrator._process_steps(task, _plan))

    assert calls == []
    assert step.status == StepStatus.DENIED
    assert task.status == TaskStatus.DENIED


def test_runtime_allows_requires_authorized_path_tool_inside_allowed_directories(tmp_path: Path):
    calls: list[dict[str, Any]] = []
    inside = tmp_path / "workspace" / "allowed.txt"

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.authorized_path_allowed",
            description="authorized path allowed",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=True,
            execute=execute,
            trust_tier="builtin",
            effects=["read"],
        )
    )
    task, plan, step = _task_plan_step("test.authorized_path_allowed", {"path": str(inside)})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == [{"path": str(inside)}]
    assert step.status == StepStatus.SUCCEEDED
    assert task.status == TaskStatus.COMPLETED


def test_pre_execute_hook_cannot_mutate_args_or_runtime_after_review(tmp_path: Path):
    workspace = tmp_path / "workspace"
    allowed = workspace / "allowed.txt"
    outside = tmp_path / "outside" / "blocked.txt"
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append({"args": dict(args), "allowed_directories": list(context["allowed_directories"])})
        return {"ok": True, "path": args["path"]}

    def pre_execute(args, context):  # noqa: ANN001, ANN202
        with pytest.raises(TypeError):
            args["path"] = str(outside)
        with pytest.raises(AttributeError):
            context["allowed_directories"].append(str(tmp_path / "outside"))

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.hook_readonly_snapshot",
            description="hook readonly snapshot",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=True,
            execute=execute,
            pre_execute=pre_execute,
            trust_tier="builtin",
            effects=["read"],
        )
    )
    task, plan, step = _task_plan_step("test.hook_readonly_snapshot", {"path": str(allowed)})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == [{"args": {"path": str(allowed)}, "allowed_directories": [str(workspace)]}]
    assert step.status == StepStatus.SUCCEEDED
    assert task.status == TaskStatus.COMPLETED


def test_progress_publish_failure_does_not_mask_successful_tool_execution(monkeypatch):
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    orchestrator.registry.register(
        ToolDefinition(
            name="test.progress_failure_is_audit_only",
            description="progress failure is audit only",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            trust_tier="builtin",
            effects=["read"],
        )
    )
    original_publish_text = orchestrator.bus.publish_text

    def flaky_publish_text(*args, **kwargs):  # noqa: ANN001, ANN202
        if (kwargs.get("metadata") or {}).get("event_type") == "tool.progress":
            raise RuntimeError("progress channel unavailable")
        return original_publish_text(*args, **kwargs)

    monkeypatch.setattr(orchestrator.bus, "publish_text", flaky_publish_text)
    task, plan, step = _task_plan_step("test.progress_failure_is_audit_only", {"value": "kept"})

    asyncio.run(orchestrator._process_steps(task, plan))

    assert calls == [{"value": "kept"}]
    assert step.status == StepStatus.SUCCEEDED
    assert task.status == TaskStatus.COMPLETED


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
        trust_tier="builtin",
        effects=["read"],
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


def test_runtime_safety_review_uses_context_for_dynamic_risk():
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"preview": True, "dry_run": args.get("dry_run")}

    orchestrator = OrchestratorAgent()
    task, _plan, step = _task_plan_step("test.context_dynamic_risk", {"url": "https://example.com"})
    tool = ToolDefinition(
        name=step.tool_name,
        description="context sensitive open",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R1_OPEN_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        trust_tier="builtin",
        effects=["open"],
    )
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.extra_context["timestamp"] = datetime(2026, 5, 26, 2, 30)

    outcome = asyncio.run(
        ToolRuntime(orchestrator).review_and_maybe_prepare_approval(
            task,
            step,
            tool,
            runtime,
        )
    )

    reviews = db.fetch_many("safety_reviews", "task_id = ? AND step_id = ?", (task.id, step.id), limit=20)
    tool_call_review = next(review for review in reviews if review["target_type"] == "tool_call")
    assert outcome.kind == "waiting_user_approval"
    assert tool_call_review["verdict"] == SafetyVerdict.NEEDS_USER_APPROVAL
    assert tool_call_review["risk_level"] == RiskLevel.R2_REVERSIBLE_MODIFY
    assert "Deep-night operation increases review risk" in " ".join(tool_call_review["reasons"])
    assert calls == [{"url": "https://example.com", "dry_run": True}]


def test_runtime_denies_approval_when_tool_lacks_dry_run_after_dynamic_risk():
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    task, _plan, step = _task_plan_step("test.context_dynamic_risk_no_dry_run", {"url": "https://example.com"})
    tool = ToolDefinition(
        name=step.tool_name,
        description="context sensitive open without dry-run",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R1_OPEN_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=execute,
        trust_tier="builtin",
        effects=["open"],
    )
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.extra_context["timestamp"] = datetime(2026, 5, 26, 2, 30)

    outcome = asyncio.run(ToolRuntime(orchestrator).review_and_maybe_prepare_approval(task, step, tool, runtime))

    assert outcome.kind == "fatal_denied"
    assert calls == []
    assert step.status == StepStatus.DENIED
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert refreshed.status == TaskStatus.CANCELLED
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    assert any(event["event_type"] == "tool.approval_requires_dry_run" for event in events)


def test_runtime_denies_dry_run_preview_that_does_not_declare_dry_run():
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True, "diff_preview": [{"action": "write"}]}

    orchestrator = OrchestratorAgent()
    task, _plan, step = _task_plan_step("test.bad_dry_run_contract", {"path": "a.txt"})
    tool = ToolDefinition(
        name=step.tool_name,
        description="bad dry-run contract",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
    )
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = asyncio.run(ToolRuntime(orchestrator).review_and_maybe_prepare_approval(task, step, tool, runtime))

    assert outcome.kind == "fatal_denied"
    assert calls == [{"path": "a.txt", "dry_run": True}]
    assert step.status == StepStatus.DENIED
    assert db.fetch_many("approvals", "task_id = ?", (task.id,), limit=10) == []


def test_runtime_safety_review_uses_context_for_permission_policy():
    calls: list[dict[str, Any]] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True}

    PermissionStore().save_policy(
        PermissionPolicy(
            rules=[
                PermissionRule(
                    id="context_time_block",
                    effect="deny",
                    tools=["test.context_permission_policy"],
                    time_windows=[PermissionTimeWindow(days=[1], start="02:00", end="02:59")],
                    reason="Context timestamp window blocks this tool.",
                )
            ]
        )
    )
    orchestrator = OrchestratorAgent()
    task, _plan, step = _task_plan_step("test.context_permission_policy")
    tool = ToolDefinition(
        name=step.tool_name,
        description="context permission policy",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=execute,
    )
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.extra_context["timestamp"] = datetime(2026, 5, 26, 2, 30)

    outcome = asyncio.run(
        ToolRuntime(orchestrator).review_and_maybe_prepare_approval(
            task,
            step,
            tool,
            runtime,
        )
    )

    reviews = db.fetch_many("safety_reviews", "task_id = ? AND step_id = ?", (task.id, step.id), limit=20)
    tool_call_review = next(review for review in reviews if review["target_type"] == "tool_call")
    assert outcome.kind == "step_denied"
    assert step.status == StepStatus.DENIED
    assert tool_call_review["verdict"] == SafetyVerdict.DENY
    assert "context_time_block" in tool_call_review["reasons"][0]
    assert "Context timestamp window blocks this tool" in tool_call_review["reasons"][0]
    assert calls == []
