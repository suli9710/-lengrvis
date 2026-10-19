"""Lightweight guardian check; this module never loads agent/model runtimes."""

from __future__ import annotations

import json
import sqlite3

from app.core import db


def residency_required() -> bool:
    try:
        db.init_db()
        settings = db.get_settings_overrides()
        if settings.get("butler_paused", False):
            return False
        with db.connect() as conn:
            if settings.get("butler_enabled", False):
                for row in conn.execute("SELECT id,data FROM autonomy_scopes"):
                    db._require_sensitive_record_integrity(conn, "autonomy_scopes", row["id"], row["data"])
                    scope = json.loads(row["data"])
                    if scope.get("enabled") and "observe" in scope.get("allowed_actions", []):
                        return True
            for row in conn.execute("SELECT id,data FROM butler_matters ORDER BY updated_at DESC"):
                db._require_sensitive_record_integrity(conn, "butler_matters", row["id"], row["data"])
                matter = json.loads(row["data"])
                if matter.get("status") in {"queued", "planning", "running", "waiting_event", "waiting_approval"}:
                    return True
    except (sqlite3.Error, ValueError, db.SensitiveRecordIntegrityError):
        return False
    return False
