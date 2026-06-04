from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.agents.orchestrator_agent import OrchestratorAgent
from app.api import routes_approvals
from app.api import routes_runtime
from app.core import db
from app.core.schemas import AgentAction, Approval, ApprovalStatus, Plan, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.runtime_context import TaskRuntimeContext
from app.policy.approval_binding import approval_secret, args_binding_hmac, binding_preview, permission_policy_version, preview_hmac, redacted_preview, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE
from app.services import mobile_pairing_service
from app.services.mobile_pairing_service import approve_approval
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    db.init_db()
    yield


class DoneAgent:
    name = "FileAgent"

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return None

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "reflected"


class ActionAgent:
    name = "FileAgent"

    def __init__(self, action: AgentAction) -> None:
        self.action = action

    async def act(self, step: PlanStep, context, observation=None, *, provider=None):  # noqa: ARG002
        return self.action

    async def reflect(self, step: PlanStep, result, *, provider=None):  # noqa: ARG002
        return "reflected"


def _setup_bound_approval(
    *,
    args: dict[str, Any] | None = None,
    preview: dict[str, Any] | None = None,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
):
    calls: list[dict[str, Any]] = []

    def execute(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(tool_args))
        return {"ok": True, "approved": tool_args.get("approved"), "approval_id": tool_args.get("approval_id")}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    tool = ToolDefinition(
        name="test.bound_write",
        description="bound write",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        tool_version="v1",
    )
    orchestrator.registry.register(tool)
    task = Task(user_goal="approval binding", mode="efficiency", status=TaskStatus.WAITING_USER_APPROVAL)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name=tool.name,
        description="bound write",
        args=args or {"path": "a.txt"},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status=StepStatus.WAITING_USER_APPROVAL,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    runtime = TaskRuntimeContext.from_task(task, orchestrator.step_execution_handler._runtime_context(task).settings, orchestrator.bus)
    preview = preview or {"ok": True, "diff_preview": [{"action": "write", "path": "a.txt"}]}
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="approve",
        status=status,
        tool_name=tool.name,
        risk_level=tool.risk_level.value,
        args_binding_hmac=args_binding_hmac(tool.name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version=tool.tool_version,
        diff_preview=preview,
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return orchestrator, task, plan, step, approval, calls


def test_bound_approval_executes_once_and_marks_consumed():
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()

    asyncio.run(orchestrator.execute_approved_step(approval))
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))

    assert calls and calls[0]["approved"] is True
    assert refreshed.consumed_at


def test_approval_for_execution_preserves_mobile_claim_denial_reason():
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_missing_grant", device_name="Missing Grant")
    approval = Approval(
        task_id="task_missing_grant_binding",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input without grant binding",
        source_device_id="mobile_missing_grant",
        allowed_device_ids=["mobile_missing_grant"],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    with pytest.raises(HTTPException) as exc_info:
        routes_approvals.approval_for_execution(
            approval.id,
            {
                "device_id": "mobile_missing_grant",
                "scope": REMOTE_INPUT_SCOPE,
                "source": "remote_input_grant",
                "grant_id": "rig_claimed",
            },
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Remote input approval is missing a grant binding."


@pytest.mark.parametrize("terminal_status", [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_approved_step_cannot_execute_after_task_leaves_approval_state(terminal_status):
    orchestrator, task, plan, _step, approval, calls = _setup_bound_approval()
    task.status = terminal_status
    task.phase = terminal_status
    task.execution_stage = ExecutionStage.IDLE
    db.upsert_model("tasks", task)

    asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None
    assert refreshed_plan.steps[0].status == StepStatus.DENIED
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    assert any(event["event_type"] == "approval.state_mismatch" for event in events)


def test_bound_approval_keeps_task_running_when_ready_steps_remain():
    orchestrator, task, plan, step, approval, _calls = _setup_bound_approval()
    follow_up = PlanStep(
        id="follow_up",
        task_id=task.id,
        order=2,
        agent_name="FileAgent",
        tool_name="test.bound_write",
        description="follow-up read-only check",
        args={"path": "a.txt"},
        risk_level=RiskLevel.R0_READ_ONLY,
        depends_on=[step.id],
    )
    plan.steps.append(follow_up)
    db.upsert_model("plans", plan)

    asyncio.run(orchestrator.execute_approved_step(approval))
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))

    assert refreshed.status == TaskStatus.EXECUTION
    assert refreshed.execution_stage == ExecutionStage.STEP_RUNNING
    assert "continuing remaining plan steps" in refreshed.final_summary.lower()


def test_consumed_approval_cannot_execute_twice():
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()

    asyncio.run(orchestrator.execute_approved_step(approval))
    plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    plan.steps[0].status = StepStatus.WAITING_USER_APPROVAL
    db.upsert_model("plans", plan)
    task.execution_stage = ExecutionStage.AWAITING_APPROVAL
    db.upsert_model("tasks", task)
    asyncio.run(orchestrator.execute_approved_step(approval))

    assert len(calls) == 1
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert "already been consumed" in refreshed.final_summary.lower()


@pytest.mark.parametrize(
    ("action", "reason_fragment"),
    [
        (AgentAction(kind="done", rationale="Already done."), "already done"),
        (AgentAction(kind="request_revision", follow_up_question="Revise this."), "plan revision"),
        (AgentAction(kind="propose_tool", tool_name="test.bound_write", args={"path": "changed.txt"}), "different tool call"),
    ],
)
def test_preexecution_subagent_stop_expires_approved_approval(action, reason_fragment):
    orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval()
    orchestrator.subagents["FileAgent"] = ActionAgent(action)

    asyncio.run(orchestrator.execute_approved_step(approval))

    refreshed_approval_data = db.fetch_one("approvals", approval.id)
    refreshed_approval = Approval.model_validate(refreshed_approval_data)
    assert calls == []
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None
    assert reason_fragment in (refreshed_approval_data.get("expired_reason") or "").lower()


def test_approval_route_returns_conflict_when_execution_expires_approval(monkeypatch: pytest.MonkeyPatch):
    orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval(status=ApprovalStatus.PENDING)
    orchestrator.subagents["FileAgent"] = ActionAgent(AgentAction(kind="done", rationale="Already done."))

    app = FastAPI()
    app.include_router(routes_approvals.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)

    with TestClient(app) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 409
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.EXPIRED
    assert calls == []
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None


def test_approval_route_can_retry_approved_unconsumed_approval(monkeypatch: pytest.MonkeyPatch):
    orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval(status=ApprovalStatus.PENDING)
    attempts = {"count": 0}

    async def execute_once_then_retry(approval_arg):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return None
        return await original_execute(approval_arg)

    app = FastAPI()
    app.include_router(routes_approvals.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)
    original_execute = orchestrator.execute_approved_step
    monkeypatch.setattr(orchestrator, "execute_approved_step", execute_once_then_retry)

    with TestClient(app) as client:
        first = client.post(f"/api/approvals/{approval.id}/approve")
        second = client.post(f"/api/approvals/{approval.id}/approve")

    assert first.status_code == 503
    assert first.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert first.json()["detail"]["approval"]["consumed_at"] is None
    assert second.status_code == 200
    assert len(calls) == 1
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed_approval.status == ApprovalStatus.APPROVED
    assert refreshed_approval.consumed_at is not None


def test_approved_step_retry_keeps_waiting_state_when_execution_raises(monkeypatch: pytest.MonkeyPatch):
    orchestrator, task, plan, _step, approval, calls = _setup_bound_approval(status=ApprovalStatus.PENDING)
    attempts = {"count": 0}

    original_execute_step = orchestrator.step_execution_handler.execute_approved_step

    async def execute_once_then_retry(approval_arg):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient execution failure")
        return await original_execute_step(approval_arg)

    app = FastAPI()
    app.include_router(routes_approvals.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)
    monkeypatch.setattr(orchestrator.step_execution_handler, "execute_approved_step", execute_once_then_retry)

    with TestClient(app) as client:
        first = client.post(f"/api/approvals/{approval.id}/approve")
        retry_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
        refreshed_task = Task.model_validate(db.fetch_one("tasks", task.id))
        refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
        second = client.post(f"/api/approvals/{approval.id}/approve")

    assert first.status_code == 503
    assert first.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert first.json()["detail"]["approval"]["consumed_at"] is None
    assert retry_approval.status == ApprovalStatus.APPROVED
    assert retry_approval.consumed_at is None
    assert refreshed_task.status == TaskStatus.EXECUTION
    assert refreshed_task.execution_stage == ExecutionStage.AWAITING_APPROVAL
    assert refreshed_plan.steps[0].status == StepStatus.WAITING_USER_APPROVAL
    assert second.status_code == 200
    assert len(calls) == 1


def test_runtime_continue_returns_conflict_when_execution_expires_approval(monkeypatch: pytest.MonkeyPatch):
    orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval()
    orchestrator.subagents["FileAgent"] = ActionAgent(AgentAction(kind="done", rationale="Already done."))

    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)

    with TestClient(app) as client:
        response = client.post(f"/api/runtime/approvals/{approval.id}/continue")

    assert response.status_code == 409
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.EXPIRED
    assert calls == []
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None


def test_runtime_continue_requires_consumed_approval_after_execution(monkeypatch: pytest.MonkeyPatch):
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()

    async def execute_without_consuming(approval_arg):  # noqa: ARG001
        return None

    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)
    monkeypatch.setattr(orchestrator, "execute_approved_step", execute_without_consuming)

    with TestClient(app) as client:
        response = client.post(f"/api/runtime/approvals/{approval.id}/continue")

    assert response.status_code == 503
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["task_id"] == task.id
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert calls == []
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed_approval.status == ApprovalStatus.APPROVED
    assert refreshed_approval.consumed_at is None


def test_approval_route_reports_consumed_execution_failure(monkeypatch: pytest.MonkeyPatch):
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval(status=ApprovalStatus.PENDING)

    def fail_execute(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(tool_args))
        return {"error": "disk write failed"}

    orchestrator.registry.get("test.bound_write").execute = fail_execute

    app = FastAPI()
    app.include_router(routes_approvals.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)

    with TestClient(app) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert response.json()["detail"]["approval"]["consumed_at"] is not None
    assert calls and calls[0]["approved"] is True
    refreshed_task = Task.model_validate(db.fetch_one("tasks", task.id))
    assert refreshed_task.status == TaskStatus.FAILED
    assert "disk write failed" in refreshed_task.final_summary


def test_runtime_continue_reports_consumed_execution_failure(monkeypatch: pytest.MonkeyPatch):
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()

    def fail_execute(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(tool_args))
        return {"error": "runtime write failed"}

    orchestrator.registry.get("test.bound_write").execute = fail_execute

    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")
    monkeypatch.setattr(routes_approvals, "OrchestratorAgent", lambda: orchestrator)

    with TestClient(app) as client:
        response = client.post(f"/api/runtime/approvals/{approval.id}/continue")

    assert response.status_code == 503
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["task_id"] == task.id
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert calls and calls[0]["approved"] is True
    refreshed_task = Task.model_validate(db.fetch_one("tasks", task.id))
    assert refreshed_task.status == TaskStatus.FAILED
    assert "runtime write failed" in refreshed_task.final_summary


@pytest.mark.parametrize("status", [ApprovalStatus.PENDING, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED])
def test_runtime_continue_rejects_nonapproved_approval_without_execution(status: ApprovalStatus):
    _orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval(status=status)

    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")

    with TestClient(app) as client:
        response = client.post(f"/api/runtime/approvals/{approval.id}/continue")

    assert response.status_code == 409
    assert response.json()["detail"]["approval"]["status"] == status
    assert calls == []


def test_runtime_continue_rejects_consumed_approval_without_reporting_ok():
    _orchestrator, _task, _plan, _step, approval, calls = _setup_bound_approval()
    approval.consumed_at = "2026-06-01T00:00:00+00:00"
    db.upsert_model("approvals", approval, status=approval.status)

    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")

    with TestClient(app) as client:
        response = client.post(f"/api/runtime/approvals/{approval.id}/continue")

    assert response.status_code == 409
    assert response.json()["detail"]["approval"]["status"] == ApprovalStatus.APPROVED
    assert response.json()["detail"]["approval"]["consumed_at"] == approval.consumed_at
    assert calls == []


def test_approval_args_mismatch_blocks_execution():
    orchestrator, task, plan, step, approval, calls = _setup_bound_approval()
    step.args = {"path": "different.txt"}
    db.upsert_model("plans", plan)

    asyncio.run(orchestrator.execute_approved_step(approval))
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    refreshed_approval_data = db.fetch_one("approvals", approval.id)
    refreshed_approval = Approval.model_validate(refreshed_approval_data)

    assert calls == []
    assert "fresh preview" in refreshed.final_summary.lower()
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None
    assert "approved arguments" in (refreshed_approval_data.get("expired_reason") or "").lower()
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    assert any(event["event_type"] == "approval.binding_mismatch" for event in events)


def test_approval_preview_mismatch_blocks_execution():
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()
    approval.diff_preview = {"ok": True, "diff_preview": [{"action": "write", "path": "tampered.txt"}]}
    db.upsert_model("approvals", approval, status=approval.status)

    asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []


def test_approval_tool_version_mismatch_blocks_execution():
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()
    approval.tool_version = "older-tool"
    db.upsert_model("approvals", approval, status=approval.status)

    asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    assert any(
        event["event_type"] == "approval.binding_mismatch"
        and "tool version" in event["payload"].get("reason", "")
        for event in events
    )


def test_approval_settings_mismatch_blocks_execution():
    orchestrator, task, _plan, _step, approval, calls = _setup_bound_approval()
    approval.settings_fingerprint = "settings:stale"
    db.upsert_model("approvals", approval, status=approval.status)

    asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []
    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    assert any(
        event["event_type"] == "approval.binding_mismatch"
        and "settings changed" in event["payload"].get("reason", "")
        for event in events
    )


def test_legacy_approval_without_binding_is_not_executable():
    orchestrator, task, plan, step, _approval, calls = _setup_bound_approval()
    legacy = Approval(task_id=task.id, step_id=step.id, message="legacy", status=ApprovalStatus.APPROVED)
    db.upsert_model("approvals", legacy, status=legacy.status)

    asyncio.run(orchestrator.execute_approved_step(legacy))

    assert calls == []


def test_redeciding_approval_is_rejected():
    _orchestrator, _task, _plan, _step, approval, _calls = _setup_bound_approval()

    with pytest.raises(HTTPException) as exc_info:
        approve_approval(approval.id)

    assert exc_info.value.status_code == 409


def test_tool_call_agent_message_redacts_sensitive_args():
    calls: list[dict[str, Any]] = []

    def execute(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(tool_args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    tool = ToolDefinition(
        name="test.secret_write",
        description="secret write",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=execute,
        sensitive_arg_keys=["custom_secret"],
        fast_path_eligible=True,
        trust_tier="builtin",
        effects=["read"],
        resource_kinds=["test"],
    )
    task = Task(user_goal="secret args", mode="efficiency", status=TaskStatus.EXECUTING_TOOL)
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name=tool.name,
        description="call secret tool",
        args={"custom_secret": "super-secret-value", "url": "https://example.com/?secret=abc1234567890"},
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    runtime = orchestrator.step_execution_handler._runtime_context(task)

    asyncio.run(orchestrator.step_execution_handler.tool_runtime.execute_allowed(task, step, tool, runtime))

    assert calls and calls[0]["custom_secret"] == "super-secret-value"
    messages = db.fetch_many("agent_messages", "task_id = ?", (task.id,), limit=20)
    serialized = json.dumps(messages, ensure_ascii=False)
    assert "super-secret-value" not in serialized
    assert "abc1234567890" not in serialized


def test_approval_secret_is_generated_in_data_dir(tmp_path: Path):
    first = approval_secret()
    second = approval_secret()

    assert first == second
    assert len(first) >= 32
    assert (tmp_path / "approval_hmac.secret").exists()


def test_redacted_preview_hides_resource_state_but_binding_keeps_it():
    preview = {
        "dry_run": True,
        "diff_preview": [{"action": "trash", "path": "a.txt"}],
        "_resource_state": [{"path": "a.txt", "sha256": "abc"}],
    }

    assert "_resource_state" not in redacted_preview(preview)
    assert binding_preview(preview)["_resource_state"][0]["sha256"] == "abc"


def test_approval_resource_state_mismatch_blocks_execution():
    calls: list[dict[str, Any]] = []
    state = {"value": "before"}

    def execute(tool_args, context):  # noqa: ANN001, ANN202, ARG001
        if tool_args.get("dry_run", True):
            return {"ok": True, "dry_run": True, "diff_preview": [{"action": "write"}], "_resource_state": [dict(state)]}
        calls.append(dict(tool_args))
        return {"ok": True}

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    tool = ToolDefinition(
        name="test.stateful_write",
        description="stateful write",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        tool_version="v1",
    )
    orchestrator.registry.register(tool)
    task = Task(user_goal="approval state", mode="efficiency", status=TaskStatus.WAITING_USER_APPROVAL)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name=tool.name,
        description="stateful write",
        args={"path": "state.txt"},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status=StepStatus.WAITING_USER_APPROVAL,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    preview = binding_preview(execute({**step.args, "dry_run": True}, runtime.tool_context()))
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="approve",
        status=ApprovalStatus.APPROVED,
        tool_name=tool.name,
        risk_level=tool.risk_level.value,
        args_binding_hmac=args_binding_hmac(tool.name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version=tool.tool_version,
        diff_preview=preview,
    )
    db.upsert_model("approvals", approval, status=approval.status)

    state["value"] = "after"
    asyncio.run(orchestrator.execute_approved_step(approval))

    assert calls == []
    refreshed_approval_data = db.fetch_one("approvals", approval.id)
    refreshed_approval = Approval.model_validate(refreshed_approval_data)
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None
    assert "resource state" in (refreshed_approval_data.get("expired_reason") or "").lower()
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert "file state changed" in refreshed.final_summary.lower()
