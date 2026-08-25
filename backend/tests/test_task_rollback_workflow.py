from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import db
from app.core.errors import AppError
from app.core.schemas import Task, ToolCall, ToolResult
from app.orchestration.resource_state import resource_state
from app.orchestration.task_phase import TaskPhase
from app.orchestration.task_rollback_workflow import (
    RollbackSource,
    TaskRollbackRequest,
    execute_task_rollback,
)
from app.orchestration.tool_runtime_execution import hold_shared_path_locks
from app.orchestration.tool_runtime_paths import (
    FILESYSTEM_WRITE_BARRIER_KEY,
    normalize_lock_path,
    write_lock_keys,
)
from app.orchestration.tool_runtime_support import _sanitize_tool_rollback_evidence
from app.policy.risk import RiskLevel
from app.tools import rollback_tools
from app.tools.managed_backups import create_managed_backup
from app.tools.rollback_locking import rollback_write_lock_keys
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


def _failed_task(task_id: str) -> Task:
    task = Task(id=task_id, user_goal="rollback workflow", status=TaskPhase.FAILED)
    db.upsert_model("tasks", task)
    return task


def _snapshot(task_id: str, target: Path) -> rollback_tools.RollbackSnapshot:
    result = ToolResult(
        id=f"result-{target.name}",
        tool_call_id=f"call-{target.name}",
        ok=True,
        rollback_info={"trash_created_file": str(target), "_post_resource_state": []},
    )
    return rollback_tools.RollbackSnapshot(
        task_id=task_id,
        entries=(
            rollback_tools.RollbackSnapshotEntry(
                tool_call_id=result.tool_call_id,
                effect_at=result.created_at,
                stable_id=result.id,
                result=result,
            ),
        ),
        journal_hmac=rollback_tools.rollback_journal_hmac(task_id),
    )


def _verified_success(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "executed": [],
        "count": 0,
        "state": "succeeded",
        "attempted": 0,
        "succeeded": 0,
        "verified": 0,
        "verification_failed": 0,
        "failed": 0,
        "manual_required": 0,
        "unrecoverable": 0,
    }


@pytest.mark.asyncio
async def test_runtime_rollback_waits_for_cleanup_filesystem_barrier_and_records_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _failed_task("task-runtime-lock")
    cleanup_root = tmp_path / "cleanup-root"
    snapshot = _snapshot(task.id, cleanup_root / "nested" / "shared.txt")
    expected_hmac = rollback_tools.rollback_snapshot_hmac(snapshot)
    executed = asyncio.Event()

    monkeypatch.setattr(rollback_tools, "load_rollback_snapshot", lambda _task_id: snapshot)

    def execute(task_id: str, *, heartbeat, **_kwargs):  # noqa: ANN001, ANN202
        assert heartbeat() is True
        executed.set()
        return _verified_success(task_id)

    monkeypatch.setattr(rollback_tools, "execute_rollback", execute)
    rollback_lock_keys = rollback_write_lock_keys(snapshot)
    cleanup_tool = ToolDefinition(
        name="file.cleanup_execute",
        description="test cleanup",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        agent_owner="FileAgent",
        supports_dry_run=True,
        requires_authorized_path=True,
        execute=lambda _args, _context: {},
        read_only=False,
        concurrency_safe=True,
        capabilities=["filesystem"],
        effects=["delete"],
        resource_kinds=["file", "directory"],
    )
    cleanup_lock_keys = write_lock_keys(
        cleanup_tool,
        {
            "roots": [str(cleanup_root)],
            "plan_id": "cleanup-plan",
            "content_hash": "content-hash",
            "selected_item_ids": ["dynamic-descendant"],
        },
    )
    assert FILESYSTEM_WRITE_BARRIER_KEY in rollback_lock_keys
    assert FILESYSTEM_WRITE_BARRIER_KEY in cleanup_lock_keys
    assert normalize_lock_path(cleanup_root) in cleanup_lock_keys
    assert set(rollback_lock_keys).intersection(cleanup_lock_keys) == {FILESYSTEM_WRITE_BARRIER_KEY}

    async with hold_shared_path_locks(cleanup_lock_keys):
        pending = asyncio.create_task(
            execute_task_rollback(
                TaskRollbackRequest(
                    task_id=task.id,
                    source=RollbackSource.RUNTIME_RECOVERY,
                    confirmation_id="runtime-recovery:shared-lock",
                    expected_inventory_hmac=expected_hmac,
                    actor="RecoveryHandler",
                )
            )
        )
        await asyncio.sleep(0.1)
        assert executed.is_set() is False

    run = await pending

    assert executed.is_set() is True
    assert run.task.status is TaskPhase.FAILED
    claim = run.task.metadata["rollback_claim"]
    assert claim["state"] == "completed"
    assert claim["confirmation_source"] == RollbackSource.RUNTIME_RECOVERY.value
    audits = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=100)
    claimed = next(item for item in audits if item["event_type"] == "task.rollback_claimed")
    assert claimed["payload"]["confirmation_source"] == RollbackSource.RUNTIME_RECOVERY.value


@pytest.mark.asyncio
async def test_managed_backup_rollback_uses_stable_journal_binding_after_backup_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _failed_task("task-managed-backup-stable-journal")
    original = tmp_path / "settings.json"
    original.write_text("before", encoding="utf-8")
    before = [resource_state(original)]
    backup = create_managed_backup(original)
    original.write_text("after", encoding="utf-8")
    changed_paths, rollback_info = _sanitize_tool_rollback_evidence(
        {"changed_paths": [str(original)], "rollback_info": {"backup": backup}},
        pre_resource_state=before,
        post_resource_state=[resource_state(original)],
        tool_origin="builtin",
        tool_trust_tier="builtin",
        data_dir=tmp_path / "data",
    )
    call = ToolCall(
        id="call-managed-backup-stable-journal",
        task_id=task.id,
        step_id="step-managed-backup-stable-journal",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2026-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-managed-backup-stable-journal",
            tool_call_id=call.id,
            ok=True,
            changed_paths=changed_paths,
            rollback_info=rollback_info,
            runtime_review_id="review-managed-backup-stable-journal",
            runtime_review_verdict="allow",
            runtime_review_completed=True,
            created_at="2026-01-01T00:00:01+00:00",
        ),
    )

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    snapshot = rollback_tools.load_rollback_snapshot(task.id)
    backup_path = Path(rollback_info["backup"]["path"])
    assert snapshot.entries[0].blocker == ""
    assert snapshot.journal_hmac == rollback_tools.rollback_journal_hmac(task.id)

    run = await execute_task_rollback(
        TaskRollbackRequest(
            task_id=task.id,
            source=RollbackSource.RUNTIME_RECOVERY,
            confirmation_id="runtime-recovery:managed-backup-stable-journal",
            expected_inventory_hmac=rollback_tools.rollback_snapshot_hmac(snapshot),
            actor="RecoveryHandler",
        )
    )

    assert run.task.status is TaskPhase.FAILED
    assert original.read_text(encoding="utf-8") == "before"
    assert backup_path.exists() is False
    assert run.task.metadata["rollback_claim"].get("inventory_changed_after_snapshot") is not True
    assert run.task.metadata["rollback"].get("inventory_changed_after_snapshot") is not True


@pytest.mark.asyncio
async def test_durable_journal_change_during_rollback_forces_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _failed_task("task-final-journal-change")
    snapshot = _snapshot(task.id, tmp_path / "journal-target.txt")
    monkeypatch.setattr(rollback_tools, "load_rollback_snapshot", lambda _task_id: snapshot)

    def execute(task_id: str, **_kwargs):  # noqa: ANN202
        db.upsert_model(
            "tool_calls",
            ToolCall(
                id="call-added-during-rollback",
                task_id=task_id,
                step_id="step-added-during-rollback",
                tool_name="file.read_text",
                risk_level=RiskLevel.R0_READ_ONLY,
                status="created",
            ),
        )
        return _verified_success(task_id)

    monkeypatch.setattr(rollback_tools, "execute_rollback", execute)

    run = await execute_task_rollback(
        TaskRollbackRequest(
            task_id=task.id,
            source=RollbackSource.RUNTIME_RECOVERY,
            confirmation_id="runtime-recovery:final-journal-change",
            expected_inventory_hmac=rollback_tools.rollback_snapshot_hmac(snapshot),
            actor="RecoveryHandler",
        )
    )

    assert run.task.status is TaskPhase.REPAIR_REQUIRED
    assert run.task.metadata["rollback_claim"]["inventory_changed_after_snapshot"] is True
    assert run.task.metadata["rollback"]["inventory_changed_after_snapshot"] is True


@pytest.mark.asyncio
async def test_runtime_rollback_inventory_change_under_lock_has_zero_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _failed_task("task-runtime-inventory-change")
    before = _snapshot(task.id, tmp_path / "before.txt")
    after = _snapshot(task.id, tmp_path / "after.txt")
    snapshots = iter((before, after))
    execute_calls = 0

    monkeypatch.setattr(rollback_tools, "load_rollback_snapshot", lambda _task_id: next(snapshots))

    def execute(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal execute_calls
        execute_calls += 1
        return _verified_success(task.id)

    monkeypatch.setattr(rollback_tools, "execute_rollback", execute)

    with pytest.raises(AppError) as error:
        await execute_task_rollback(
            TaskRollbackRequest(
                task_id=task.id,
                source=RollbackSource.RUNTIME_RECOVERY,
                confirmation_id="runtime-recovery:inventory-change",
                expected_inventory_hmac=rollback_tools.rollback_snapshot_hmac(before),
                actor="RecoveryHandler",
            )
        )

    assert error.value.code == "rollback_recovery_evidence_changed"
    assert execute_calls == 0
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status is TaskPhase.REPAIR_REQUIRED
    assert persisted.metadata["rollback_claim"]["state"] == "failed"


@pytest.mark.asyncio
async def test_running_rollback_crash_is_interrupted_and_permanently_blocks_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task = _failed_task("task-runtime-crash")
    snapshot = _snapshot(task.id, tmp_path / "crash.txt")
    expected_hmac = rollback_tools.rollback_snapshot_hmac(snapshot)
    execute_calls = 0

    monkeypatch.setattr(rollback_tools, "load_rollback_snapshot", lambda _task_id: snapshot)

    def crash(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal execute_calls
        execute_calls += 1
        raise RuntimeError("injected rollback crash")

    monkeypatch.setattr(rollback_tools, "execute_rollback", crash)
    request = TaskRollbackRequest(
        task_id=task.id,
        source=RollbackSource.RUNTIME_RECOVERY,
        confirmation_id="runtime-recovery:crash",
        expected_inventory_hmac=expected_hmac,
        actor="RecoveryHandler",
    )

    with pytest.raises(RuntimeError, match="injected rollback crash"):
        await execute_task_rollback(request)

    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status is TaskPhase.REPAIR_REQUIRED
    assert persisted.metadata["rollback_claim"]["state"] == "interrupted"
    with pytest.raises(AppError, match="previous rollback was interrupted"):
        await execute_task_rollback(request)
    assert execute_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_state"),
    [
        (
            {
                "executed": [
                    {
                        "tool_call_id": "blocker",
                        "ok": False,
                        "verified": False,
                        "requires_user_action": True,
                        "verification": {"status": "manual_required"},
                    }
                ],
                "state": "manual_required",
                "attempted": 1,
                "succeeded": 0,
                "verified": 0,
                "verification_failed": 0,
                "failed": 0,
                "manual_required": 1,
                "unrecoverable": 0,
            },
            TaskPhase.REPAIR_REQUIRED,
        ),
        (
            {
                "executed": [{"tool_call_id": "forged", "ok": True}],
                "state": "succeeded",
                "attempted": 1,
                "succeeded": 1,
                "verified": 1,
                "verification_failed": 0,
                "failed": 0,
                "manual_required": 0,
                "unrecoverable": 0,
            },
            TaskPhase.REPAIR_REQUIRED,
        ),
    ],
)
async def test_only_action_level_verified_success_reaches_safe_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: dict[str, object],
    expected_state: TaskPhase,
) -> None:
    task = _failed_task(f"task-outcome-{outcome['state']}-{len(outcome['executed'])}")
    snapshot = _snapshot(task.id, tmp_path / f"{task.id}.txt")
    monkeypatch.setattr(rollback_tools, "load_rollback_snapshot", lambda _task_id: snapshot)
    monkeypatch.setattr(rollback_tools, "execute_rollback", lambda *_args, **_kwargs: dict(outcome))

    run = await execute_task_rollback(
        TaskRollbackRequest(
            task_id=task.id,
            source=RollbackSource.RUNTIME_RECOVERY,
            confirmation_id=f"runtime-recovery:{task.id}",
            expected_inventory_hmac=rollback_tools.rollback_snapshot_hmac(snapshot),
            actor="RecoveryHandler",
        )
    )

    assert run.task.status is expected_state
    assert run.task.metadata["rollback_claim"]["state"] == "completed"
