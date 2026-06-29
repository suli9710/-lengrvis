from __future__ import annotations

import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.main import create_app
from app.security import local_secret


def test_audit_events_are_append_only_hash_chained(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()

    first = record("security.first", "pytest", {"ok": True})
    second = record("security.second", "pytest", {"ok": True})

    assert first.sequence == 1
    assert second.sequence == 2
    assert first.prev_hash == db.AUDIT_GENESIS_HASH
    assert second.prev_hash == first.event_hash
    assert first.event_hash
    assert first.hmac
    verification = db.verify_audit_log()
    assert verification["ok"] is True
    assert verification["checked"] == 2
    assert verification["last_event_id"] == second.id
    assert verification["last_sequence"] == 2
    assert verification["failure_index"] is None
    assert verification["failure_reason"] == ""


def test_audit_verification_summarizes_empty_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()

    result = db.verify_audit_log()

    assert result["ok"] is True
    assert result["checked"] == 0
    assert result["last_event_id"] is None
    assert result["last_sequence"] == 0
    assert result["last_hash"] == db.AUDIT_GENESIS_HASH
    assert result["failure_index"] is None
    assert result["failure_reason"] == ""
    assert result["failures"] == []


def test_audit_verification_detects_tampering(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    event = record("security.tamper", "pytest", {"ok": True})

    conn = sqlite3.connect(db.db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("DROP TRIGGER audit_events_no_update")
        row = conn.execute("SELECT data FROM audit_events WHERE id = ?", (event.id,)).fetchone()
        payload = json.loads(row["data"])
        payload["payload"]["ok"] = False
        conn.execute("UPDATE audit_events SET data = ? WHERE id = ?", (json.dumps(payload), event.id))
        conn.commit()
    finally:
        conn.close()

    db.init_db()
    result = db.verify_audit_log()

    assert result["ok"] is False
    assert result["checked"] == 0
    assert result["failure_index"] == 1
    assert result["failure_event_id"] == event.id
    assert result["failure_sequence"] == 1
    assert result["failure_reason"] == "event_hash_mismatch"
    assert result["failures"][0]["reason"] == "event_hash_mismatch"


def test_audit_verification_detects_bad_hash_column(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    event = record("security.bad_hash", "pytest", {"ok": True})

    conn = sqlite3.connect(db.db_path())
    try:
        conn.execute("DROP TRIGGER audit_events_no_update")
        conn.execute("UPDATE audit_events SET event_hash = ? WHERE id = ?", ("f" * 64, event.id))
        conn.commit()
    finally:
        conn.close()

    db.init_db()
    result = db.verify_audit_log()

    assert result["ok"] is False
    assert result["failure_index"] == 1
    assert result["failure_event_id"] == event.id
    assert result["failure_reason"] == "stored_column_mismatch"


def test_audit_events_cannot_be_updated_or_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    event = record("security.append_only", "pytest", {"ok": True})

    with db.connect() as conn:
        try:
            conn.execute("UPDATE audit_events SET actor = ? WHERE id = ?", ("attacker", event.id))
            updated = True
        except Exception as exc:  # noqa: BLE001 - sqlite raises OperationalError/IntegrityError depending on build.
            updated = False
            assert "append-only" in str(exc)

    with db.connect() as conn:
        try:
            conn.execute("DELETE FROM audit_events WHERE id = ?", (event.id,))
            deleted = True
        except Exception as exc:  # noqa: BLE001
            deleted = False
            assert "append-only" in str(exc)

    assert updated is False
    assert deleted is False


def test_audit_verification_detects_missing_append_only_trigger(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    event = record("security.trigger_deleted", "pytest", {"ok": True})

    with sqlite3.connect(db.db_path()) as conn:
        conn.execute("DROP TRIGGER audit_events_no_delete")

    result = db.verify_audit_log()

    assert result["ok"] is False
    assert result["checked"] == 1
    assert result["failure_event_id"] == event.id
    assert result["failure_reason"] == "append_only_trigger_missing"
    assert result["failures"][0]["missing_triggers"] == ["audit_events_no_delete"]


def test_audit_verify_route(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    record("security.route", "pytest", {"ok": True})

    response = TestClient(create_app()).get("/api/audit/verify")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["checked"] == 1
    assert payload["last_event_id"]
    assert payload["failure_reason"] == ""


def test_audit_hmac_secret_is_generated_and_reused(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET_FILE", raising=False)
    db.init_db()

    first = record("security.local_secret_first", "pytest", {"ok": True})
    secret_path = db.audit_hmac_secret_path()
    stored_secret = secret_path.read_text(encoding="utf-8").strip()
    second = record("security.local_secret_second", "pytest", {"ok": True})

    assert secret_path.exists()
    decrypted_secret = local_secret.read_local_secret(secret_path)
    assert len(decrypted_secret) == 64
    if local_secret.dpapi_available():
        assert stored_secret.startswith(local_secret.LOCAL_SECRET_DPAPI_PREFIX)
    elif local_secret.keyring_available():
        assert stored_secret.startswith(local_secret.LOCAL_SECRET_KEYRING_PREFIX)
        assert decrypted_secret not in stored_secret
    else:
        assert stored_secret == decrypted_secret
    assert first.hmac
    assert second.hmac
    assert secret_path.read_text(encoding="utf-8").strip() == stored_secret
    assert db.verify_audit_log()["ok"] is True


def test_audit_hmac_secret_marker_is_not_next_to_database_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET_FILE", raising=False)
    db.init_db()

    record("security.local_secret_path", "pytest", {"ok": True})

    secret_path = db.audit_hmac_secret_path()
    assert secret_path.exists()
    assert secret_path.parent != db.db_path().parent
    assert secret_path.parent == tmp_path / db.AUDIT_HMAC_SECRET_DIR
    assert not (tmp_path / db.AUDIT_HMAC_SECRET_FILE).exists()


def test_audit_chain_head_cache_keeps_chain_consistent(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()

    events = [record(f"security.cache_{index}", "pytest", {"index": index}) for index in range(5)]

    # Invalidate the in-memory head mid-chain: the next event must fall back
    # to a database query and still extend the chain correctly.
    db.reset_audit_caches()
    recovered = record("security.cache_recovered", "pytest", {"ok": True})

    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert recovered.sequence == 6
    assert recovered.prev_hash == events[-1].event_hash
    verification = db.verify_audit_log()
    assert verification["ok"] is True
    assert verification["checked"] == 6


def test_audit_verification_detects_tail_truncation_after_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    first = record("security.tail_first", "pytest", {"ok": True})
    second = record("security.tail_second", "pytest", {"ok": True})
    third = record("security.tail_third", "pytest", {"ok": True})

    conn = sqlite3.connect(db.db_path())
    try:
        conn.execute("DROP TRIGGER audit_events_no_delete")
        conn.execute("DELETE FROM audit_events WHERE id = ?", (third.id,))
        conn.commit()
    finally:
        conn.close()
    db.reset_audit_caches()

    result = db.verify_audit_log()

    assert result["ok"] is False
    assert result["checked"] == 2
    assert result["last_event_id"] == second.id
    assert result["last_sequence"] == 2
    assert result["failure_reason"] == "tail_truncated"
    assert result["failure_sequence"] == 3
    assert result["failure_event_id"] == third.id
    assert first.sequence == 1


def test_audit_verification_detects_external_anchor_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    event = record("security.anchor_first", "pytest", {"ok": True})

    anchor_path = db.audit_anchor_path()
    payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    payload["event_hash"] = "f" * 64
    anchor_path.write_text(json.dumps(payload), encoding="utf-8")

    result = db.verify_audit_log()

    assert result["ok"] is False
    assert result["checked"] == 1
    assert result["last_event_id"] == event.id
    assert result["failure_reason"] == "external_anchor_mismatch"


def test_audit_fail_closed_blocks_after_anchor_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_AUDIT_FAIL_CLOSED", "true")
    db.init_db()
    record("security.anchor_gate", "pytest", {"ok": True})

    anchor_path = db.audit_anchor_path()
    payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    payload["event_hash"] = "0" * 64
    anchor_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(db.SensitiveRecordIntegrityError, match="Audit fail-closed gate blocked"):
        db.require_audit_fail_closed_ok()


def test_audit_hmac_secret_read_once_per_process(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET_FILE", raising=False)
    db.init_db()

    calls = {"count": 0}
    original = local_secret.load_or_create_local_secret
    audit_secret_path = db.audit_hmac_secret_path()

    def counting_loader(path, **kwargs):  # noqa: ANN001, ANN202
        if str(path) == str(audit_secret_path):
            calls["count"] += 1
        return original(path, **kwargs)

    monkeypatch.setattr(local_secret, "load_or_create_local_secret", counting_loader)

    record("security.secret_cache_first", "pytest", {"ok": True})
    record("security.secret_cache_second", "pytest", {"ok": True})
    record("security.secret_cache_third", "pytest", {"ok": True})

    assert calls["count"] == 1
    assert db.verify_audit_log()["ok"] is True


def test_audit_write_after_cache_reset_with_local_secret_does_not_deadlock(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET", raising=False)
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET_FILE", raising=False)
    db.init_db()

    first = record("security.local_secret_head_first", "pytest", {"ok": True})
    db.reset_audit_caches()
    result: dict[str, object] = {}

    def write_after_reset() -> None:
        try:
            result["event"] = record("security.local_secret_head_second", "pytest", {"ok": True})
        except BaseException as exc:  # noqa: BLE001 - propagate thread failures into assertion output.
            result["error"] = exc

    thread = threading.Thread(target=write_after_reset, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "audit write deadlocked after cache reset with persisted chain head"
    assert "error" not in result
    second = result["event"]
    assert second.sequence == first.sequence + 1
    assert second.prev_hash == first.event_hash
    assert db.verify_audit_log()["ok"] is True


def test_audit_hmac_secret_unavailable_does_not_fall_back_to_empty_key(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_AUDIT_HMAC_SECRET", raising=False)
    db.init_db()

    def fail_audit_secret_write(path, value):  # noqa: ANN001, ANN202
        raise OSError("blocked audit secret write")

    monkeypatch.setattr(local_secret, "_write_secret_file", fail_audit_secret_write)

    with pytest.raises(RuntimeError, match="Audit HMAC secret is unavailable"):
        record("security.no_empty_secret", "pytest", {"ok": True})
