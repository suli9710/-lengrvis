from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core import db
from app.core.schemas import (
    APPROVAL_TTL_SECONDS_BY_RISK,
    DEFAULT_APPROVAL_TTL_SECONDS,
    Approval,
    ApprovalStatus,
    approval_expiry_iso,
    approval_is_expired,
)
from app.policy.risk import RiskLevel
from app.services.mobile_pairing_service import approve_approval, list_pending_approvals


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


def _timestamp(offset_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _store(approval: Approval) -> None:
    db.upsert_model("approvals", approval, status=approval.status)


def test_approval_expiry_is_derived_from_created_at_and_stable_for_legacy_payload():
    created_at = "2026-07-11T00:00:00+00:00"
    legacy = {
        "id": "approval-legacy-model",
        "task_id": "task-1",
        "message": "approve",
        "status": "pending",
        "created_at": created_at,
    }

    first = Approval.model_validate(legacy)
    second = Approval.model_validate(legacy)

    assert first.expires_at == second.expires_at
    assert datetime.fromisoformat(first.expires_at) - datetime.fromisoformat(created_at) == timedelta(
        seconds=DEFAULT_APPROVAL_TTL_SECONDS
    )


@pytest.mark.parametrize(
    "risk_level",
    [
        RiskLevel.R0_READ_ONLY,
        RiskLevel.R1_OPEN_ONLY,
        RiskLevel.R2_REVERSIBLE_MODIFY,
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
    ],
)
def test_approval_expiry_uses_risk_tier_ttl(risk_level: RiskLevel):
    created_at = "2026-07-11T00:00:00+00:00"
    approval = Approval(
        task_id=f"task-{risk_level.value}",
        message="approve",
        risk_level=risk_level.value,
        created_at=created_at,
    )

    assert datetime.fromisoformat(approval.expires_at) - datetime.fromisoformat(created_at) == timedelta(
        seconds=APPROVAL_TTL_SECONDS_BY_RISK[risk_level.value]
    )


def test_explicit_approval_ttl_override_remains_supported_for_internal_callers():
    created_at = "2026-07-11T00:00:00+00:00"

    expires_at = approval_expiry_iso(
        created_at,
        30,
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert datetime.fromisoformat(expires_at) - datetime.fromisoformat(created_at) == timedelta(seconds=30)


def test_legacy_risk_bearing_payload_uses_original_created_at_and_shorter_ttl():
    created_at = "2026-07-11T00:00:00+00:00"
    legacy = Approval.model_validate(
        {
            "id": "approval-legacy-r3",
            "task_id": "task-legacy-r3",
            "message": "approve destructive action",
            "risk_level": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
            "status": "pending",
            "created_at": created_at,
        }
    )

    assert datetime.fromisoformat(legacy.expires_at) - datetime.fromisoformat(created_at) == timedelta(minutes=5)


def test_pending_list_expires_legacy_record_without_granting_fresh_ttl():
    created_at = _timestamp(-(DEFAULT_APPROVAL_TTL_SECONDS + 60))
    legacy = {
        "id": "approval-legacy-row",
        "task_id": "task-legacy",
        "step_id": "step-legacy",
        "approval_type": "tool_call",
        "message": "legacy approval",
        "status": "pending",
        "created_at": created_at,
    }
    stored = db._json(legacy)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (legacy["id"], legacy["task_id"], legacy["step_id"], stored, "pending", created_at),
        )
        db._store_sensitive_record_integrity(conn, "approvals", legacy["id"], stored)

    assert list_pending_approvals() == []
    refreshed = Approval.model_validate(db.fetch_one("approvals", legacy["id"]))
    assert refreshed.status == ApprovalStatus.EXPIRED
    assert refreshed.expires_at < _timestamp(0)


def test_mobile_approval_rejects_expired_authorization_and_persists_expired_status():
    approval = Approval(
        task_id="task-expired-mobile",
        message="approve",
        created_at=_timestamp(-(DEFAULT_APPROVAL_TTL_SECONDS + 1)),
    )
    _store(approval)

    with pytest.raises(HTTPException, match="Approval authorization expired") as exc_info:
        approve_approval(approval.id)

    assert exc_info.value.status_code == 409
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.status == ApprovalStatus.EXPIRED


def test_atomic_decision_expires_approval_when_clock_crosses_deadline():
    approval = Approval(task_id="task-decision-race", message="approve")
    _store(approval)
    after_expiry = (datetime.fromisoformat(approval.expires_at) + timedelta(microseconds=1)).isoformat()

    decided = db.decide_approval_atomically(approval.id, ApprovalStatus.APPROVED.value, after_expiry)

    assert decided is not None
    assert decided["status"] == ApprovalStatus.EXPIRED.value
    assert decided["expired_reason"] == "Approval authorization expired."


def test_atomic_execution_claim_expires_stale_approved_record():
    approval = Approval(
        task_id="task-claim-expired",
        message="approve",
        status=ApprovalStatus.APPROVED,
        created_at=_timestamp(-(DEFAULT_APPROVAL_TTL_SECONDS + 1)),
    )
    _store(approval)

    claimed = db.claim_approval_for_execution(approval.id, _timestamp(0))

    assert claimed is None
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.status == ApprovalStatus.EXPIRED
    assert refreshed.consumed_at is None


def test_fresh_approval_remains_usable_before_deadline():
    approval = Approval(task_id="task-fresh", message="approve")

    assert approval_is_expired(approval, at=approval.created_at) is False
    assert approval_is_expired(approval, at=approval.expires_at) is True
