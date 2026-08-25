from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import approval_observability as observed
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.observability import metrics


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    metrics.reset()
    db.init_db(force=True)
    yield
    metrics.reset()


def _store(approval: Approval) -> Approval:
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def _counter_values(name: str) -> dict[tuple[tuple[str, str], ...], float]:
    return {
        tuple(sorted(entry["labels"].items())): entry["value"]
        for entry in metrics.snapshot()["counters"]
        if entry["name"] == name
    }


def test_atomic_decision_records_applied_rejected_and_unavailable_once() -> None:
    approved = _store(
        Approval(
            task_id="task-sensitive-approved",
            message="Approve private.tool for C:\\private\\approved.txt",
        )
    )
    rejected = _store(
        Approval(
            task_id="task-sensitive-rejected",
            message="Reject private.tool for C:\\private\\rejected.txt",
        )
    )

    assert db.decide_approval_atomically(approved.id, "approved", now_iso())["status"] == "approved"
    assert db.decide_approval_atomically(rejected.id, "rejected", now_iso())["status"] == "rejected"
    assert db.decide_approval_atomically(rejected.id, "approved", now_iso()) is None

    assert _counter_values("approval_decision_outcomes_total") == {
        (("decision", "approved"), ("outcome", "applied")): 1.0,
        (("decision", "approved"), ("outcome", "unavailable")): 1.0,
        (("decision", "rejected"), ("outcome", "applied")): 1.0,
    }
    rendered = metrics.render_prometheus()
    assert approved.id not in rendered
    assert rejected.id not in rendered
    assert "task-sensitive" not in rendered
    assert "private.tool" not in rendered
    assert "approved.txt" not in rendered


def test_atomic_decision_records_expiry_without_claiming_application() -> None:
    approval = _store(
        Approval(
            task_id="task-expired-decision",
            message="Expired decision",
            created_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
    )

    result = db.decide_approval_atomically(approval.id, "approved", now_iso())

    assert result is not None
    assert result["status"] == ApprovalStatus.EXPIRED.value
    assert _counter_values("approval_decision_outcomes_total") == {
        (("decision", "approved"), ("outcome", "expired")): 1.0,
    }


def test_atomic_claim_records_claimed_already_consumed_and_unavailable() -> None:
    approved = _store(
        Approval(
            task_id="task-claim-success",
            message="Claim once",
            status=ApprovalStatus.APPROVED,
        )
    )
    pending = _store(Approval(task_id="task-claim-pending", message="Not approved"))

    assert db.claim_approval_for_execution(approved.id, now_iso()) is not None
    assert db.claim_approval_for_execution(approved.id, now_iso()) is None
    assert db.claim_approval_for_execution(pending.id, now_iso()) is None
    assert db.claim_approval_for_execution("missing-private-approval-id", now_iso()) is None

    assert _counter_values("approval_claim_outcomes_total") == {
        (("outcome", "already_consumed"),): 1.0,
        (("outcome", "claimed"),): 1.0,
        (("outcome", "unavailable"),): 2.0,
    }
    assert approved.id not in metrics.render_prometheus()
    assert pending.id not in metrics.render_prometheus()
    assert "missing-private-approval-id" not in metrics.render_prometheus()


def test_atomic_claim_distinguishes_ttl_and_authentication_invalidation() -> None:
    stale = _store(
        Approval(
            task_id="task-claim-stale",
            message="Expired claim",
            status=ApprovalStatus.APPROVED,
            created_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
    )
    invalid_auth = _store(
        Approval(
            task_id="task-claim-invalid-auth",
            message="Invalid auth claim",
            status=ApprovalStatus.APPROVED,
            authorized_at=now_iso(),
            auth_context={
                "channel": "private-channel-name",
                "confirmation_id": "private-confirmation-id",
            },
        )
    )

    assert db.claim_approval_for_execution(stale.id, now_iso()) is None
    assert db.claim_approval_for_execution(invalid_auth.id, now_iso()) is None

    assert _counter_values("approval_claim_outcomes_total") == {
        (("outcome", "authorization_invalidated"),): 1.0,
        (("outcome", "expired"),): 1.0,
    }
    rendered = metrics.render_prometheus()
    assert "private-channel-name" not in rendered
    assert "private-confirmation-id" not in rendered


def test_concurrent_decisions_emit_one_applied_and_one_unavailable() -> None:
    approval = _store(Approval(task_id="task-concurrent-decision", message="Decide atomically"))
    barrier = threading.Barrier(2)
    results: list[dict[str, object] | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def decide(status: str) -> None:
        try:
            barrier.wait(timeout=5)
            result = db.decide_approval_atomically(approval.id, status, now_iso())
            with lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=decide, args=("approved",)),
        threading.Thread(target=decide, args=("rejected",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sum(result is not None for result in results) == 1
    values = _counter_values("approval_decision_outcomes_total")
    assert sum(value for labels, value in values.items() if ("outcome", "applied") in labels) == 1
    assert sum(value for labels, value in values.items() if ("outcome", "unavailable") in labels) == 1


def test_concurrent_claims_emit_one_claimed_and_one_already_consumed() -> None:
    approval = _store(
        Approval(
            task_id="task-concurrent-claim",
            message="Claim atomically",
            status=ApprovalStatus.APPROVED,
        )
    )
    barrier = threading.Barrier(2)
    results: list[dict[str, object] | None] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def claim() -> None:
        try:
            barrier.wait(timeout=5)
            result = db.claim_approval_for_execution(approval.id, now_iso())
            with lock:
                results.append(result)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=claim), threading.Thread(target=claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sum(result is not None for result in results) == 1
    assert _counter_values("approval_claim_outcomes_total") == {
        (("outcome", "already_consumed"),): 1.0,
        (("outcome", "claimed"),): 1.0,
    }


def test_claim_storage_error_and_observability_double_failure_preserve_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _store(
        Approval(
            task_id="task-claim-error",
            message="Fail before claim",
            status=ApprovalStatus.APPROVED,
        )
    )
    original = RuntimeError("original integrity failure")

    def fail_integrity(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise original

    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    def fail_recovery_log(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(db, "_require_sensitive_record_integrity", fail_integrity)
    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)
    monkeypatch.setattr(observed, "log_best_effort_failure", fail_recovery_log)

    with pytest.raises(RuntimeError) as exc_info:
        db.claim_approval_for_execution(approval.id, now_iso())

    assert exc_info.value is original


@pytest.mark.parametrize(
    ("operation", "metric_name", "expected_labels"),
    [
        (
            "decision",
            "approval_decision_outcomes_total",
            {"decision": "approved", "outcome": "error"},
        ),
        (
            "claim",
            "approval_claim_outcomes_total",
            {"outcome": "error"},
        ),
    ],
)
def test_atomic_storage_errors_emit_one_error_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    metric_name: str,
    expected_labels: dict[str, str],
) -> None:
    approval = _store(
        Approval(
            task_id="task-storage-error",
            message="Fail inside storage",
            status=ApprovalStatus.APPROVED if operation == "claim" else ApprovalStatus.PENDING,
        )
    )
    original = RuntimeError(r"C:\private\approval-storage-secret.txt")

    def fail_integrity(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise original

    monkeypatch.setattr(db, "_require_sensitive_record_integrity", fail_integrity)

    with pytest.raises(RuntimeError) as exc_info:
        if operation == "decision":
            db.decide_approval_atomically(approval.id, "approved", now_iso())
        else:
            db.claim_approval_for_execution(approval.id, now_iso())

    assert exc_info.value is original
    entries = [entry for entry in metrics.snapshot()["counters"] if entry["name"] == metric_name]
    assert entries == [
        {
            "name": metric_name,
            "labels": expected_labels,
            "value": 1.0,
        }
    ]
    assert "approval-storage-secret" not in metrics.render_prometheus()


def test_successful_claim_survives_observability_double_failure_and_consumes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval = _store(
        Approval(
            task_id="task-successful-claim-double-failure",
            message="Claim despite metrics outage",
            status=ApprovalStatus.APPROVED,
        )
    )

    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    def fail_recovery_log(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("logger unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(observed.metrics, "increment_counter", fail_counter)
        scoped.setattr(observed, "log_best_effort_failure", fail_recovery_log)
        claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert claimed is not None
    assert claimed["consumed_at"]
    assert db.fetch_one("approvals", approval.id)["consumed_at"] == claimed["consumed_at"]
    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
