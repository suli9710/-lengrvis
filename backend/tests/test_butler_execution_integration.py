from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
from app.core.schemas import Approval, Plan, PlanStep, Run, RunEngine, RunPhase, Task, TaskStatus
from app.services.butler_evidence import undo_available, verify_conditions


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["edit", "proactive_archive", "json"])
@pytest.mark.parametrize("restart", [False, True], ids=["live", "restored"])
async def test_scoped_file_write_uses_real_approval_execution_verification_and_undo(
    monkeypatch, tmp_path, operation, restart
):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.task_rollback_workflow import RollbackSource, TaskRollbackRequest, execute_task_rollback
    from app.policy.policy_engine import PolicyEngine

    original_init = PolicyEngine.__init__

    def consistent_review_clock(self, *args, **kwargs):
        kwargs["now_provider"] = lambda: datetime(2026, 9, 15, 12, tzinfo=UTC)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(PolicyEngine, "__init__", consistent_review_clock)

    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    archive = operation == "proactive_archive"
    structured = operation == "json"
    file = tmp_path / ("receipt.json" if structured else "document.txt")
    file.write_text("original content", encoding="utf-8")
    target = tmp_path / "archived.txt" if archive else file
    expected = "original content" if archive else "updated content"
    data = {"receipt": "业务回执 😀", "status": "confirmed"}
    if structured:
        expected = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    scope = store.save(
        AutonomyScope(label="可逆编辑", directories=[str(tmp_path)], allowed_actions=["observe", "file_modify"])
    )
    task = Task(
        user_goal="将文档更新为新的普通内容",
        status=TaskStatus.REVIEWING_PLAN,
        metadata={"butler_matter_id": "matter-owned", "butler_execution_ticket": "execution-owned"},
    )
    run = Run(message=task.user_goal, task_id=task.id, engine=RunEngine.OS, phase=RunPhase.AWAITING_APPROVAL)
    db.upsert_model("runs", run)
    matter = store.save(
        ButlerMatter(
            id="matter-owned",
            goal=task.user_goal,
            source="event" if archive else "user",
            active_task_id=task.id,
            active_run_id=run.id,
            run_ids=[run.id],
            task_ids=[task.id],
            status=MatterStatus.RUNNING,
            execution_ticket="execution-owned",
            scope_versions={scope.id: 1},
            completion_conditions=[
                {
                    "kind": "file_json",
                    "subject": str(target),
                    "checks": [{"label": key, "path": [key], "expected": value} for key, value in data.items()],
                }
                if structured
                else {"kind": "file_text", "subject": str(target), "expected": expected}
            ],
        )
    )
    step = PlanStep(
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="file.move" if archive else "file.write_json" if structured else "file.write_text",
        description="更新文档",
        args={"source": str(file), "destination": str(target), "dry_run": True}
        if archive
        else {"path": str(file), "data": data, "dry_run": True}
        if structured
        else {"path": str(file), "text": expected, "dry_run": True},
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    orchestrator = OrchestratorAgent()
    orchestrator.safety.policy.now_provider = lambda: datetime(2026, 9, 15, 12, tzinfo=UTC)
    orchestrator.planning_handler._annotate_plan_tool_contracts(
        plan, [tool.name for tool in orchestrator.registry.list()]
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    await orchestrator._process_steps(task, plan)
    approvals = db.fetch_many("approvals", "task_id = ?", (task.id,))
    assert len(approvals) == 1
    approval = Approval.model_validate(approvals[0])
    assert approval.status.value == "pending" and approval.source == "autonomy_scope"
    assert file.read_text() == "original content"
    from app.orchestration.orchestrator_registry import orchestrator_registry
    from app.services import run_service
    from app.services.butler_service import ButlerCoordinator

    wakes = []
    reconciles = []
    original_reconcile = run_service.reconcile_task_runs

    def reconcile(task_id):
        reconciles.append(task_id)
        return original_reconcile(task_id)

    monkeypatch.setattr(run_service, "reconcile_task_runs", reconcile)
    monkeypatch.setattr(run_service, "resume_runs_for_task", lambda task_id, **kwargs: wakes.append(task_id))
    if not restart:
        orchestrator_registry.bind(task_id=task.id, run_id=run.id, orchestrator=orchestrator)
    else:
        orchestrator_registry.release_task(task.id)
    try:
        coordinator = ButlerCoordinator()
        notifications = []
        monkeypatch.setattr(coordinator, "notify_once", lambda *args: notifications.append(args))
        # The run phase can be persisted before its worker has finished. During
        # that handoff, standing authorization must not become a human prompt.
        monkeypatch.setattr(run_service, "_run_active", lambda _: True)
        await coordinator.sync_run(matter)
        current = store.get(ButlerMatter, matter.id)
        assert current.status == MatterStatus.RUNNING and current.interaction is None
        assert not notifications and not wakes
        assert file.read_text() == "original content"
        monkeypatch.setattr(run_service, "_run_active", lambda _: False)
        await coordinator.sync_run(current)
    finally:
        orchestrator_registry.release_task(task.id)
    approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert approval.status.value == "approved" and approval.consumed_at
    assert wakes == [task.id]
    assert reconciles == [task.id]
    completed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert completed.status == TaskStatus.COMPLETED
    assert target.read_text(encoding="utf-8") == expected
    verified, evidence, reason = await verify_conditions(matter)
    assert verified, reason
    assert evidence
    assert undo_available(matter)
    from app.services.butler_events import enqueue_event, flush_events

    store.set_control(enabled=True)
    timestamp = datetime.now(UTC)
    enqueue_event("file_changed", str(target), {}, now=timestamp)
    assert flush_events(now=timestamp + timedelta(seconds=31)) == []
    undone = await execute_task_rollback(
        TaskRollbackRequest(
            task_id=task.id,
            source=RollbackSource.NATIVE_CONFIRMATION,
            confirmation_id="test-user-undo",
            actor="test-user",
        )
    )
    assert undone.outcome["state"] == "succeeded"
    assert file.read_text() == "original content"
    if archive:
        assert not target.exists()
