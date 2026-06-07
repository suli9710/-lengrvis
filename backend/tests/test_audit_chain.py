from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.main import create_app


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
