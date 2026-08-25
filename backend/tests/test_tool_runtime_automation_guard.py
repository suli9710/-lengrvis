from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.automation import intent_capsule as intent_capsule_module
from app.automation.intent_capsule import issue_intent_capsule, revoke_intent_capsule
from app.automation.models import BudgetConsumeRequest, RunBudgetLimits
from app.automation.run_budget import consume_run_budget, create_run_budget, get_run_budget
from app.core import db
from app.core.content_provenance import (
    content_binding_payload,
    create_content_envelope,
    revalidate_content_envelope,
)
from app.core.schemas import (
    Approval,
    ApprovalStatus,
    ExecutionStage,
    Plan,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    now_iso,
)
from app.orchestration.automation_runtime_guard import authorize_automation_execution
from app.orchestration.tool_runtime import ToolRuntime
from app.orchestration.tool_runtime_dry_run_execution import build_approval_dry_run_preview_result
from app.policy.approval_binding import permission_policy_version
from app.policy.effective_risk_binding import build_effective_risk_binding
from app.policy.permissions import PermissionPolicy, PermissionStore
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_runtime_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    db.init_db(force=True)


def _task_step(tool_name: str, args: dict[str, Any] | None = None) -> tuple[Task, PlanStep]:
    task = Task(user_goal="run the approved automation", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name=tool_name,
        description="execute automated action",
        args=dict(args or {}),
        expected_observation="automation action completed",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", Plan(task_id=task.id, goal=task.user_goal, steps=[step]))
    return task, step


def _automation_runtime(
    orchestrator: OrchestratorAgent,
    task: Task,
    *,
    run_id: str,
    token: str,
    policy_version: str | None = None,
):
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.extra_context.update(
        {
            "automation_run_id": run_id,
            "automation_intent_capsule_token": token,
            "automation_plan_revision": 1,
            "automation_policy_version": policy_version or _current_policy_version(),
        }
    )
    return runtime


def _current_policy_version() -> str:
    return permission_policy_version(PermissionStore().updated_at())


def _tool(
    name: str,
    execute,
    *,
    pre_execute=None,
    effects: list[str] | None = None,
    capabilities: list[str] | None = None,
    external_network: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="automation runtime integration tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        pre_execute=pre_execute,
        read_only=False,
        concurrency_safe=False,
        destructive=False,
        capabilities=list(capabilities or []),
        effects=list(effects or ["write"]),
        resource_kinds=["external_service"] if external_network else ["system"],
        trust_tier="builtin",
        external_network=external_network,
    )


def _execute_with_consumed_approval(
    orchestrator: OrchestratorAgent,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
    runtime,
    *,
    threaded_tools: bool = False,
):
    tool_runtime = ToolRuntime(orchestrator)
    reviews, declared_risk = tool_runtime._fresh_execution_reviews(
        task,
        step,
        tool,
        runtime,
        dict(step.args),
    )
    binding = build_effective_risk_binding(declared_risk, reviews)
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve automation runtime test action.",
        tool_name=tool.name,
        risk_level=binding["effective_risk_level"],
        engineering_boundary={"risk_provenance": binding},
        status=ApprovalStatus.APPROVED,
        consumed_at=now_iso(),
    )
    db.upsert_model("approvals", approval)
    return asyncio.run(
        tool_runtime.execute_allowed(
            task,
            step,
            tool,
            runtime,
            approved_args=dict(step.args),
            approval_id=approval.id,
            threaded_tools=threaded_tools,
        )
    )


def test_core_runtime_receives_server_issued_capsule_and_budget() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    orchestrator._supervise_new_agent_messages = lambda *args, **kwargs: True  # type: ignore[method-assign]
    task, step = _task_step("test.manual_write")
    tool = _tool("test.manual_write", lambda args, context: calls.append("execute") or {"ok": True})
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "succeeded"
    assert calls == ["execute"]
    ledger = get_run_budget(f"core:{task.id}")
    assert ledger is not None
    assert ledger.usage.tool_calls == 1
    assert ledger.usage.writes == 1
    assert runtime.extra_context["core_runtime_authorization"] is True
    assert runtime.extra_context["automation_intent_capsule_id"]


def test_structural_plan_revision_expires_existing_approval() -> None:
    orchestrator = OrchestratorAgent()
    task, _step = _task_step("test.plan_revision")
    plan = orchestrator._latest_plan_for_task(task.id)
    approval = Approval(
        task_id=task.id,
        step_id=plan.steps[0].id,
        message="approve original revision",
        status=ApprovalStatus.APPROVED,
    )
    db.upsert_model("approvals", approval)

    orchestrator._persist_plan_update(plan, "Changed executable plan.", revision_change=True)

    stored = db.fetch_one("approvals", approval.id)
    assert plan.version == 2
    assert stored is not None
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert "Plan revision changed" in stored["expired_reason"]


def test_tainted_content_blocks_side_effect_tool_before_execution() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.tainted_write")
    envelope = create_content_envelope(
        "external value",
        source_kind="browser",
        source_id="page-1",
        trust_level="untrusted",
        taint_flags=["external_content", "web_content"],
        task_scope=task.id,
    )
    step.args = {"value": "external value", "content_envelope": envelope.model_dump(mode="json")}
    tool = _tool("test.tainted_write", lambda args, context: calls.append("execute") or {"ok": True})
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "revalidation" in outcome.result.error
    assert calls == []


def test_server_propagated_tainted_content_blocks_write_without_embedded_envelope() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.propagated_tainted_write", {"value": "external value"})
    envelope = create_content_envelope(
        {"value": "external value"},
        source_kind="browser",
        source_id="page-1",
        trust_level="untrusted",
        taint_flags=["external_content", "web_content"],
        task_scope=task.id,
    )
    tool = _tool("test.propagated_tainted_write", lambda args, context: calls.append("execute") or {"ok": True})
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.extra_context["upstream_content_envelopes"] = [envelope.model_dump(mode="json")]

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "revalidation" in outcome.result.error
    assert calls == []


def test_revalidated_benign_envelope_cannot_authorize_different_payload() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.payload_swap")
    envelope = create_content_envelope(
        "benign value",
        source_kind="browser",
        source_id="page-1",
        trust_level="untrusted",
        taint_flags=["external_content", "web_content"],
        task_scope=task.id,
    )
    confirmed = revalidate_content_envelope(
        envelope,
        content_binding_payload({"value": "benign value"}),
        task_scope=task.id,
    )
    step.args = {"value": "malicious value", "content_envelope": confirmed.model_dump(mode="json")}
    tool = _tool("test.payload_swap", lambda args, context: calls.append("execute") or {"ok": True})
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "does not match the executable payload" in outcome.result.error
    assert calls == []


def test_task_scoped_content_revalidation_allows_side_effect_tool() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    orchestrator._supervise_new_agent_messages = lambda *args, **kwargs: True  # type: ignore[method-assign]
    task, step = _task_step("test.revalidated_write")
    envelope = create_content_envelope(
        "external value",
        source_kind="browser",
        source_id="page-1",
        trust_level="untrusted",
        taint_flags=["external_content", "web_content"],
        task_scope=task.id,
    )
    args = {"value": "external value", "content_envelope": envelope.model_dump(mode="json")}
    confirmed = revalidate_content_envelope(envelope, content_binding_payload(args), task_scope=task.id)
    step.args = {**args, "content_envelope": confirmed.model_dump(mode="json")}
    tool = _tool("test.revalidated_write", lambda args, context: calls.append("execute") or {"ok": True})
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "succeeded"
    assert calls == ["execute"]


def test_repeated_read_only_actions_do_not_consume_duplicate_side_effect_budget() -> None:
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.read_only", {"query": "status"})
    step.risk_level = RiskLevel.R0_READ_ONLY
    tool = ToolDefinition(
        name="test.read_only",
        description="read automation state",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: {"ok": True},
        read_only=True,
        concurrency_safe=True,
        effects=["read"],
        resource_kinds=["runtime"],
        trust_tier="builtin",
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
    )
    create_run_budget(
        "run-read-repeat",
        limits=RunBudgetLimits(max_tool_calls=10, max_duplicate_actions=1),
    )
    runtime = _automation_runtime(orchestrator, task, run_id="run-read-repeat", token=issued.token)

    for _ in range(2):
        authorization = authorize_automation_execution(
            task=task,
            step=step,
            tool=tool,
            runtime=runtime,
            args=step.args,
        )
        assert authorization is not None

    ledger = get_run_budget("run-read-repeat")
    assert ledger is not None
    assert ledger.status == "active"
    assert ledger.usage.tool_calls == 2
    assert ledger.usage.duplicate_actions == {}


def test_expired_capsule_denies_before_lifecycle_hook_or_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.expired_automation")
    tool = _tool(
        "test.expired_automation",
        lambda args, context: calls.append("execute") or {"ok": True},
        pre_execute=lambda args, context: calls.append("pre_execute"),
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
        ttl_seconds=60,
    )
    create_run_budget("run-expired")
    runtime = _automation_runtime(orchestrator, task, run_id="run-expired", token=issued.token)
    expired_now = datetime.now(UTC) + timedelta(hours=2)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return expired_now if tz is not None else expired_now.replace(tzinfo=None)

    monkeypatch.setattr(intent_capsule_module, "datetime", ExpiredClock)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "expired" in outcome.result.error
    assert calls == []
    assert step.status == StepStatus.DENIED
    assert runtime.abort_requested is True
    assert db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=10) == []
    assert get_run_budget("run-expired").usage.tool_calls == 0  # type: ignore[union-attr]
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=20)
    assert any(event["event_type"] == "automation_runtime.intent_denied" for event in events)


def test_hard_budget_denial_occurs_before_lifecycle_hook_or_tool_execution() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.budgeted_write")
    tool = _tool(
        "test.budgeted_write",
        lambda args, context: calls.append("execute") or {"ok": True},
        pre_execute=lambda args, context: calls.append("pre_execute"),
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
    )
    create_run_budget("run-no-writes", limits=RunBudgetLimits(max_writes=0))
    runtime = _automation_runtime(orchestrator, task, run_id="run-no-writes", token=issued.token)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    ledger = get_run_budget("run-no-writes")
    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "write budget" in outcome.result.error
    assert calls == []
    assert step.status == StepStatus.DENIED
    assert runtime.abort_requested is True
    assert db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=10) == []
    assert ledger is not None
    assert ledger.status == "hard_stopped"
    assert ledger.usage.tool_calls == 1
    assert ledger.usage.writes == 1
    assert len(ledger.usage.duplicate_actions) == 1
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=20)
    assert any(event["event_type"] == "automation_runtime.budget_hard_stopped" for event in events)


def test_soft_budget_pause_blocks_execution_without_hard_stopping_runtime() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.soft_budget_write")
    tool = _tool(
        "test.soft_budget_write",
        lambda args, context: calls.append("execute") or {"ok": True},
        pre_execute=lambda args, context: calls.append("pre_execute"),
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
    )
    create_run_budget(
        "run-soft-pause",
        limits=RunBudgetLimits(max_tool_calls=5, max_duplicate_actions=10),
    )
    for _ in range(3):
        assert (
            consume_run_budget(
                "run-soft-pause",
                BudgetConsumeRequest(kind="tool_call"),
            ).allowed
            is True
        )
    runtime = _automation_runtime(orchestrator, task, run_id="run-soft-pause", token=issued.token)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    ledger = get_run_budget("run-soft-pause")
    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "near limit" in outcome.result.error
    assert calls == []
    assert task.execution_stage == ExecutionStage.PAUSED
    assert step.status == StepStatus.DENIED
    assert runtime.abort_requested is False
    assert ledger is not None
    assert ledger.status == "soft_exceeded"
    assert ledger.usage.tool_calls == 4
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=20)
    assert any(event["event_type"] == "automation_runtime.budget_paused" for event in events)


def test_policy_change_invalidates_previously_issued_capsule_before_execution() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.stale_policy")
    tool = _tool(
        "test.stale_policy",
        lambda args, context: calls.append("execute") or {"ok": True},
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
    )
    create_run_budget("run-stale-policy")
    PermissionStore().save_policy(PermissionPolicy())
    runtime = _automation_runtime(orchestrator, task, run_id="run-stale-policy", token=issued.token)

    outcome = _execute_with_consumed_approval(orchestrator, task, step, tool, runtime)

    assert outcome.kind == "fatal_denied"
    assert outcome.result is not None
    assert "policy version" in outcome.result.error
    assert calls == []
    assert get_run_budget("run-stale-policy").usage.tool_calls == 0  # type: ignore[union-attr]


def test_dry_run_preview_validates_capsule_before_calling_tool() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    task, step = _task_step("test.revoked_preview")
    tool = _tool(
        "test.revoked_preview",
        lambda args, context: calls.append("execute") or {"ok": True, "dry_run": True},
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=[],
        data_egress_scope=[],
        policy_version=_current_policy_version(),
    )
    create_run_budget("run-revoked-preview")
    runtime = _automation_runtime(orchestrator, task, run_id="run-revoked-preview", token=issued.token)
    revoke_intent_capsule(issued.capsule.id)

    result = asyncio.run(
        build_approval_dry_run_preview_result(
            ToolRuntime(orchestrator),
            task,
            step,
            tool,
            runtime,
            threaded_tools=False,
        )
    )

    assert result.ok is False
    assert "revoked" in result.error
    assert calls == []
    assert runtime.abort_requested is True
    assert get_run_budget("run-revoked-preview").usage.tool_calls == 0  # type: ignore[union-attr]


def test_allowed_automation_consumes_all_classified_budget_before_execution() -> None:
    calls: list[str] = []
    orchestrator = OrchestratorAgent()
    orchestrator._supervise_new_agent_messages = lambda *args, **kwargs: True  # type: ignore[method-assign]
    args = {
        "url": "https://forms.example.test/form/1?session=secret",
        "to": "alice@example.test",
        "fields": {"name": "Alice", "department": "Operations"},
    }
    task, step = _task_step("test.full_automation", args)
    step.model_action = {"automation_retry": True, "retry_of_step_id": "step-prior"}
    tool = _tool(
        "test.full_automation",
        lambda call_args, context: calls.append("execute") or {"ok": True},
        effects=["write", "send", "input", "execute_subprocess"],
        capabilities=["subprocess"],
        external_network=True,
    )
    issued = issue_intent_capsule(
        task_id=task.id,
        user_goal=task.user_goal,
        plan_revision=1,
        allowed_tools=[tool.name],
        resource_scope=["https://forms.example.test/*"],
        data_egress_scope=[
            "origin:example.test",
            "origin:forms.example.test",
            "recipient:alice@example.test",
        ],
        policy_version=_current_policy_version(),
    )
    create_run_budget(
        "run-full",
        limits=RunBudgetLimits(
            max_tool_calls=5,
            max_writes=5,
            max_external_sends=5,
            max_recipients=5,
            max_domains=5,
            max_ui_inputs=5,
            max_retries=2,
            max_subprocesses=2,
            max_parallel_fanout=4,
            max_duplicate_actions=2,
        ),
    )
    runtime = _automation_runtime(orchestrator, task, run_id="run-full", token=issued.token)
    runtime.extra_context["automation_parallel_fanout"] = 3

    outcome = _execute_with_consumed_approval(
        orchestrator,
        task,
        step,
        tool,
        runtime,
        threaded_tools=True,
    )

    ledger = get_run_budget("run-full")
    assert outcome.kind == "succeeded"
    assert calls == ["execute"]
    assert ledger is not None
    assert ledger.usage.tool_calls == 1
    assert ledger.usage.writes == 1
    assert ledger.usage.external_sends == 1
    assert ledger.usage.ui_inputs == 2
    assert ledger.usage.retries == 1
    assert ledger.usage.subprocesses == 1
    assert ledger.usage.max_parallel_fanout_seen == 3
    assert len(ledger.usage.recipients) == 1
    assert len(ledger.usage.domains) == 2
    assert len(ledger.usage.duplicate_actions) == 1
    assert runtime.extra_context["automation_intent_capsule_id"] == issued.capsule.id
    assert runtime.extra_context["automation_budget_version"] == ledger.version
