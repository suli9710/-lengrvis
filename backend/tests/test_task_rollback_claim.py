from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import db
from app.core.db_task_rollback import (
    claim_task_rollback,
    finish_task_rollback,
    heartbeat_task_rollback,
    maintain_task_rollback_lease,
    mark_task_rollback_running,
    recover_interrupted_task_rollbacks,
)
from app.core.schemas import Task
from app.orchestration.task_phase import TaskPhase


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.reset_init_db_cache()
    db.init_db()
    yield
    db.close_thread_connection()


def _completed_task(task_id: str = "task-rollback-claim") -> Task:
    task = Task(
        id=task_id,
        user_goal="write a report",
        status=TaskPhase.COMPLETED,
        phase=TaskPhase.COMPLETED,
    )
    db.upsert_model("tasks", task)
    return task


def _expire_claim(task_id: str) -> None:
    _set_claim_expiry(task_id, "2000-01-01T00:00:00+00:00")


def _set_claim_expiry(task_id: str, expires_at: str) -> None:
    task = Task.model_validate(db.fetch_one("tasks", task_id))
    claim = dict(task.metadata["rollback_claim"])
    claim["lease_expires_at"] = expires_at
    task.metadata = {**task.metadata, "rollback_claim": claim}
    db.upsert_model("tasks", task)


def test_task_rollback_claim_allows_only_one_concurrent_executor() -> None:
    task = _completed_task()

    def claim(index: int):
        try:
            return claim_task_rollback(
                task.id,
                f"confirmation-{index}",
                preview_hmac="preview-v1",
            )
        finally:
            db.close_thread_connection()

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(claim, (1, 2)))

    winners = [item for item, _reason in attempts if item is not None]
    losers = [reason for item, reason in attempts if item is None]
    assert len(winners) == 1
    assert losers == ["Rollback is already in progress for this task."]
    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.metadata["rollback_claim"]["claim_id"] == winners[0].claim_id
    assert stored.metadata["rollback_claim"]["preview_hmac"] == "preview-v1"


def test_task_rollback_finish_requires_active_claim_id() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation", preview_hmac="preview")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id) is True

    summary = {
        "state": "succeeded",
        "attempted": 1,
        "succeeded": 1,
        "verified": 1,
        "verification_failed": 0,
        "failed": 0,
        "manual_required": 0,
        "unrecoverable": 0,
    }
    assert (
        finish_task_rollback(
            task.id,
            "wrong-claim",
            phase=TaskPhase.ROLLED_BACK,
            rollback_metadata=summary,
            final_summary="done",
        )
        is None
    )
    finished = finish_task_rollback(
        task.id,
        claim.claim_id,
        phase=TaskPhase.ROLLED_BACK,
        rollback_metadata=summary,
        final_summary="done",
    )
    assert finished is not None
    assert finished.status == TaskPhase.ROLLED_BACK
    assert finished.metadata["rollback_claim"]["state"] == "completed"


def test_startup_recovery_marks_interrupted_rollback_for_manual_repair() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation", preview_hmac="preview")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id) is True
    _expire_claim(task.id)

    assert recover_interrupted_task_rollbacks() == [task.id]

    recovered = Task.model_validate(db.fetch_one("tasks", task.id))
    assert recovered.status == TaskPhase.REPAIR_REQUIRED
    assert recovered.phase == TaskPhase.REPAIR_REQUIRED
    assert recovered.metadata["rollback_claim"]["state"] == "interrupted"
    assert recovered.metadata["rollback"]["manual_required"] == 1
    assert "Automatic retry is blocked" in recovered.final_summary
    assert recover_interrupted_task_rollbacks() == []


def test_startup_recovery_leaves_live_foreign_claim_running() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(
        task.id,
        "confirmation",
        preview_hmac="preview",
        owner_id="worker-a",
    )
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True

    assert recover_interrupted_task_rollbacks() == []

    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.status == TaskPhase.COMPLETED
    assert stored.metadata["rollback_claim"]["state"] == "running"
    assert stored.metadata["rollback_claim"]["owner_id"] == "worker-a"


def test_startup_recovery_marks_expired_foreign_claim_for_manual_repair() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(
        task.id,
        "confirmation",
        preview_hmac="preview",
        owner_id="worker-a",
    )
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True
    _expire_claim(task.id)

    assert recover_interrupted_task_rollbacks() == [task.id]
    assert (
        finish_task_rollback(
            task.id,
            claim.claim_id,
            owner_id="worker-a",
            phase=TaskPhase.ROLLED_BACK,
            rollback_metadata={},
            final_summary="must not overwrite recovery",
        )
        is None
    )


def test_new_claim_marks_a_later_expired_active_claim_for_manual_repair() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(
        task.id,
        "confirmation-a",
        preview_hmac="preview-a",
        owner_id="worker-a",
    )
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True
    _expire_claim(task.id)

    replacement, replacement_error = claim_task_rollback(
        task.id,
        "confirmation-b",
        preview_hmac="preview-b",
        owner_id="worker-b",
    )

    assert replacement is None
    assert "previous rollback lease expired" in replacement_error.casefold()
    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.status == TaskPhase.REPAIR_REQUIRED
    assert stored.metadata["rollback_claim"]["claim_id"] == claim.claim_id
    assert stored.metadata["rollback_claim"]["state"] == "interrupted"
    assert stored.metadata["rollback"]["manual_required"] == 1


def test_interrupted_claim_stays_permanently_blocked_if_phase_is_overwritten_to_denied() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation-a", owner_id="worker-a")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True
    _expire_claim(task.id)
    assert recover_interrupted_task_rollbacks() == [task.id]
    interrupted = Task.model_validate(db.fetch_one("tasks", task.id))
    interrupted.status = TaskPhase.DENIED
    interrupted.phase = TaskPhase.DENIED
    db.upsert_model("tasks", interrupted)

    replacement, replacement_error = claim_task_rollback(
        task.id,
        "confirmation-b",
        owner_id="worker-b",
    )

    assert replacement is None
    assert "permanently blocked" in replacement_error
    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.metadata["rollback_claim"]["claim_id"] == claim.claim_id
    assert stored.metadata["rollback_claim"]["state"] == "interrupted"


def test_task_rollback_claim_enforces_owner_and_state_cas() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(
        task.id,
        "confirmation",
        preview_hmac="preview",
        owner_id="worker-a",
    )
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-b") is False
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is False
    assert heartbeat_task_rollback(task.id, claim.claim_id, owner_id="worker-b") is False
    assert heartbeat_task_rollback(task.id, claim.claim_id, owner_id="worker-a") is True

    finished = finish_task_rollback(
        task.id,
        claim.claim_id,
        owner_id="worker-a",
        phase=TaskPhase.ROLLED_BACK,
        rollback_metadata={"state": "succeeded"},
        final_summary="done",
    )

    assert finished is not None
    assert heartbeat_task_rollback(task.id, claim.claim_id, owner_id="worker-a") is False


def test_periodic_lease_heartbeat_protects_a_long_running_step() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation", owner_id="worker-a", lease_seconds=30)
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(
        task.id,
        claim.claim_id,
        owner_id="worker-a",
        lease_seconds=30,
    )

    with maintain_task_rollback_lease(
        task.id,
        claim.claim_id,
        owner_id="worker-a",
        lease_seconds=30,
        interval_seconds=0.01,
    ) as lease_alive:
        short_expiry = (datetime.now(UTC) + timedelta(milliseconds=250)).isoformat()
        _set_claim_expiry(task.id, short_expiry)
        time.sleep(0.35)
        assert lease_alive() is True
        assert recover_interrupted_task_rollbacks() == []

    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.metadata["rollback_claim"]["state"] == "running"
    assert stored.metadata["rollback_claim"]["lease_expires_at"] > "2000-01-01T00:00:00+00:00"


def test_expired_lease_cannot_be_revived_or_finalized() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation", owner_id="worker-a")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True
    _expire_claim(task.id)

    assert heartbeat_task_rollback(task.id, claim.claim_id, owner_id="worker-a") is False
    assert (
        finish_task_rollback(
            task.id,
            claim.claim_id,
            owner_id="worker-a",
            phase=TaskPhase.ROLLED_BACK,
            rollback_metadata={"state": "succeeded"},
            final_summary="must not finalize",
        )
        is None
    )
    assert recover_interrupted_task_rollbacks() == [task.id]
    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.status == TaskPhase.REPAIR_REQUIRED
    assert stored.metadata["rollback_claim"]["state"] == "interrupted"


def test_lease_callback_observes_durable_recovery_before_the_next_step() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation", owner_id="worker-a")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id, owner_id="worker-a") is True

    with maintain_task_rollback_lease(
        task.id,
        claim.claim_id,
        owner_id="worker-a",
        interval_seconds=60,
    ) as lease_alive:
        _expire_claim(task.id)
        assert recover_interrupted_task_rollbacks() == [task.id]
        assert lease_alive() is False
        assert lease_alive.lost_event.is_set() is True

    stored = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored.status == TaskPhase.REPAIR_REQUIRED
    assert stored.metadata["rollback_claim"]["state"] == "interrupted"


def test_finish_does_not_persist_a_malformed_task() -> None:
    task = _completed_task()
    claim, error = claim_task_rollback(task.id, "confirmation")
    assert error == ""
    assert claim is not None
    assert mark_task_rollback_running(task.id, claim.claim_id)
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (task.id,)).fetchone()
        payload = json.loads(row[0])
        payload["id"] = []
        conn.execute(
            "UPDATE tasks SET data = ? WHERE id = ?",
            (json.dumps(payload), task.id),
        )

    finished = finish_task_rollback(
        task.id,
        claim.claim_id,
        phase=TaskPhase.ROLLED_BACK,
        rollback_metadata={"state": "succeeded"},
        final_summary="done",
    )

    assert finished is None
    with db.connect() as conn:
        stored = json.loads(conn.execute("SELECT data FROM tasks WHERE id = ?", (task.id,)).fetchone()[0])
    assert stored["metadata"]["rollback_claim"]["state"] == "running"
