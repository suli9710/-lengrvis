from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus
from app.policy.permissions import PermissionRule, PermissionStore


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "test-sensitive-record-hmac-secret")
    db.reset_audit_caches()
    db.init_db()
    db.bootstrap_sensitive_record_integrity()


def test_tampered_setting_fails_integrity_check() -> None:
    db.set_setting("allow_cloud_context", False)

    with db.connect() as conn:
        conn.execute("UPDATE app_settings SET value = ? WHERE key = ?", ("true", "allow_cloud_context"))

    with pytest.raises(db.SensitiveRecordIntegrityError):
        db.get_settings_overrides()
    result = db.sensitive_integrity_check()
    assert result["ok"] is False
    assert result["failures"][0]["table"] == "app_settings"


def test_tampered_approval_fails_integrity_check() -> None:
    approval = Approval(task_id="task_integrity", message="approve", status=ApprovalStatus.PENDING)
    db.upsert_model("approvals", approval, status=approval.status)
    payload = db.fetch_one("approvals", approval.id)
    assert payload is not None
    payload["status"] = ApprovalStatus.APPROVED.value

    with db.connect() as conn:
        conn.execute(
            "UPDATE approvals SET data = ?, status = ? WHERE id = ?", (json.dumps(payload), "approved", approval.id)
        )

    with pytest.raises(db.SensitiveRecordIntegrityError):
        db.fetch_one("approvals", approval.id)
    result = db.sensitive_integrity_check()
    assert result["ok"] is False
    assert result["failures"][0]["table"] == "approvals"


def test_tampered_permission_policy_fails_integrity_check() -> None:
    store = PermissionStore("default")
    policy = store.add_rule(
        PermissionRule(id="allow_read", name="Allow read", effect="allow", tools=["file.read_text"])
    )
    payload = policy.model_dump(mode="json")
    payload["rules"][0]["effect"] = "deny"

    with db.connect() as conn:
        conn.execute("UPDATE permission_policies SET data = ? WHERE id = ?", (json.dumps(payload), "default"))

    with pytest.raises(db.SensitiveRecordIntegrityError):
        store.get_policy()
    result = db.sensitive_integrity_check()
    assert result["ok"] is False
    assert result["failures"][0]["table"] == "permission_policies"


def test_tampered_audit_chain_head_fails_integrity_check() -> None:
    event = record("integrity.test", "pytest", {"ok": True})
    check = db.sensitive_integrity_check()
    assert check["ok"] is True

    with db.connect() as conn:
        row = conn.execute("SELECT id FROM audit_chain_heads WHERE event_id = ?", (event.id,)).fetchone()
        assert row is not None
        conn.execute(
            """
            UPDATE sensitive_record_integrity
            SET digest = ?
            WHERE table_name = ? AND record_id = ?
            """,
            ("f" * 64, "audit_chain_heads", row["id"]),
        )

    result = db.sensitive_integrity_check()
    assert result["ok"] is False
    assert result["failures"][0]["table"] == "audit_chain_heads"
    with pytest.raises(db.SensitiveRecordIntegrityError):
        db.verify_audit_log()


def test_set_setting_rolls_back_when_integrity_store_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    db.set_setting("integrity_atomic_key", {"version": 1})

    real_store = db._store_sensitive_record_integrity

    def failing_store(conn, table, record_id, data):  # type: ignore[no-untyped-def]
        if table == "app_settings" and record_id == "integrity_atomic_key":
            raise RuntimeError("integrity store failed")
        real_store(conn, table, record_id, data)

    monkeypatch.setattr(db, "_store_sensitive_record_integrity", failing_store)

    with pytest.raises(RuntimeError, match="integrity store failed"):
        db.set_setting("integrity_atomic_key", {"version": 2})

    assert db.get_settings_overrides()["integrity_atomic_key"] == {"version": 1}


def test_bootstrap_fails_when_integrity_proof_deleted_after_initial_bootstrap() -> None:
    db.set_setting("allow_cloud_context", False)

    with db.connect() as conn:
        conn.execute(
            """
            DELETE FROM sensitive_record_integrity
            WHERE table_name = 'app_settings' AND record_id = ?
            """,
            ("allow_cloud_context",),
        )

    result = db.bootstrap_sensitive_record_integrity()
    assert result["ok"] is False
    assert any(item["table"] == "app_settings" for item in result["failures"])
