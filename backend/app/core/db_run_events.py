from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.core import db


def next_run_event_sequence(run_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()
    return int(row["sequence"] or 0) + 1


def insert_run_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(model.model_dump_json()) if isinstance(model, BaseModel) else dict(model)
    return insert_run_event_record(data)


def insert_run_event_record(data: dict[str, Any]) -> dict[str, Any]:
    with db._EVENT_WRITE_LOCK:
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return insert_run_event_locked(conn, data)


def insert_run_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    now = data.get("created_at") or db._now_iso()
    stored = dict(data)
    stored.setdefault("id", f"runevt_{uuid4().hex}")
    stored["created_at"] = now
    sequence = int(stored.get("sequence") or 0)
    if sequence <= 0:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM run_events WHERE run_id = ?",
            (stored["run_id"],),
        ).fetchone()
        sequence = int(row["sequence"] or 0) + 1
        stored["sequence"] = sequence
    conn.execute(
        """
        INSERT INTO run_events (id, run_id, name, sequence, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            stored["id"],
            stored["run_id"],
            stored["name"],
            sequence,
            db._json(stored),
            now,
        ),
    )
    return stored


def fetch_run_events(run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT data FROM run_events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (run_id, after_sequence, limit),
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]


def delete_run_events_before(cutoff_iso: str) -> int:
    with db.connect() as conn:
        cursor = conn.execute("DELETE FROM run_events WHERE created_at < ?", (cutoff_iso,))
    return int(cursor.rowcount or 0)
