from __future__ import annotations

import json
import sqlite3

from app.core import db
from app.core.audit import record, verify_chain


def test_audit_events_are_append_only_and_verifiable(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    first = record("test.first", "pytest", {"secret": "token"})
    second = record("test.second", "pytest", {"ok": True})

    assert first.sequence == 1
    assert second.sequence == 2
    assert second.prev_hash == first.event_hash
    assert verify_chain()["ok"] is True


def test_audit_verify_detects_tampering(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    event = record("test.tamper", "pytest", {"ok": True})

    with db.connect() as conn:
        try:
            conn.execute("UPDATE audit_events SET actor = ? WHERE id = ?", ("attacker", event.id))
            updated = True
        except Exception as exc:  # noqa: BLE001 - sqlite raises different concrete errors by build.
            updated = False
            assert "append-only" in str(exc)

    assert updated is False

    conn = sqlite3.connect(db.db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("DROP TRIGGER audit_events_no_update")
        row = conn.execute("SELECT data FROM audit_events WHERE id = ?", (event.id,)).fetchone()
        data = json.loads(row["data"])
        data["payload"]["ok"] = False
        conn.execute("UPDATE audit_events SET data = ? WHERE id = ?", (json.dumps(data), event.id))
        conn.commit()
    finally:
        conn.close()

    result = verify_chain()
    assert result["ok"] is False
    assert result["failures"][0]["reason"] == "event_hash_mismatch"
