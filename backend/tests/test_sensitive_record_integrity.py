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


def test_deleted_permission_policy_row_is_detected_as_orphaned_proof() -> None:
    store = PermissionStore("default")
    store.add_rule(PermissionRule(id="deny_read", effect="deny", tools=["file.read_text"]))
    with db.connect() as conn:
        conn.execute("DELETE FROM permission_policies WHERE id = ?", (store.policy_id,))

    result = db.sensitive_integrity_check()

    assert result["ok"] is False
    assert any(
        failure["table"] == "permission_policies" and "record is missing" in failure["reason"]
        for failure in result["failures"]
    )
    with pytest.raises(db.SensitiveRecordIntegrityError, match="record is missing"):
        store.get_policy()


def test_dropped_permission_policy_table_fails_integrity_and_policy_read() -> None:
    store = PermissionStore("default")
    store.add_rule(PermissionRule(id="deny_read", effect="deny", tools=["file.read_text"]))
    with db.connect() as conn:
        conn.execute("DROP TABLE permission_policies")

    result = db.sensitive_integrity_check()

    assert result["ok"] is False
    assert any(failure["table"] == "permission_policies" and failure["id"] == "*" for failure in result["failures"])
    with pytest.raises(db.SensitiveRecordIntegrityError, match="schema is unavailable"):
        store.get_policy()


def test_dropped_integrity_proof_table_blocks_reinitialization() -> None:
    db.set_setting("allow_cloud_context", False)
    with db.connect() as conn:
        conn.execute("DROP TABLE sensitive_record_integrity")

    db.reset_init_db_cache()
    with pytest.raises(RuntimeError, match="sensitive_record_integrity"):
        db.init_db(force=True)

    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sensitive_record_integrity'"
            ).fetchone()
            is None
        )


def test_deleted_bootstrap_marker_never_reseals_tampered_records() -> None:
    db.set_setting("allow_cloud_context", False)
    with db.connect() as conn:
        conn.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            ("true", "allow_cloud_context"),
        )
        conn.execute(
            "DELETE FROM sensitive_record_integrity WHERE table_name = 'app_settings' AND record_id = ?",
            ("allow_cloud_context",),
        )
        conn.execute("DELETE FROM sensitive_record_integrity WHERE table_name = '__meta__' AND record_id = 'bootstrap'")

    result = db.bootstrap_sensitive_record_integrity()

    assert result["ok"] is False
    assert any("bootstrap marker" in failure["reason"] for failure in result["failures"])
    assert db.sensitive_integrity_check()["ok"] is False


def test_deleted_permission_row_and_proof_is_detected_by_presence_ledger() -> None:
    store = PermissionStore("default")
    store.add_rule(PermissionRule(id="deny_read", effect="deny", tools=["file.read_text"]))
    with db.connect() as conn:
        conn.execute("DELETE FROM permission_policies WHERE id = ?", (store.policy_id,))
        conn.execute(
            "DELETE FROM sensitive_record_integrity WHERE table_name = 'permission_policies' AND record_id = ?",
            (store.policy_id,),
        )

    with pytest.raises(db.SensitiveRecordIntegrityError, match="record is missing"):
        store.get_policy()
    result = db.sensitive_integrity_check()
    assert result["ok"] is False
    assert any(failure["table"] == "permission_policies" for failure in result["failures"])


def test_signed_but_malformed_permission_policy_is_semantic_integrity_failure() -> None:
    store = PermissionStore("default")
    store.add_rule(PermissionRule(id="deny_read", effect="deny", tools=["file.read_text"]))
    malformed = "{not-json"
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE permission_policies SET data = ? WHERE id = ?",
            (malformed, store.policy_id),
        )
        db.store_sensitive_record_integrity(
            "permission_policies",
            store.policy_id,
            malformed,
            conn=conn,
        )

    result = db.sensitive_integrity_check()

    assert result["ok"] is False
    assert any("payload is invalid" in failure["reason"] for failure in result["failures"])
    with pytest.raises(db.SensitiveRecordIntegrityError, match="payload is invalid"):
        store.get_policy()
