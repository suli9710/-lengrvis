from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.core import db


def claim_scheduled_task_run(
    schedule_id: str,
    *,
    expected_next_run_at: str,
    claimed_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Atomically claim one due scheduled task before side effects run."""
    stored = dict(claimed_data)
    now = str(stored.get("updated_at") or db._now_iso())
    expected_next = expected_next_run_at or ""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT enabled, next_run_at, data FROM scheduled_tasks WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        if not row or not bool(row["enabled"]):
            return None

        current = json.loads(row["data"])
        if current.get("enabled") is False:
            return None
        current_next = str(row["next_run_at"] or current.get("next_run_at") or "")
        if current_next != expected_next:
            return None

        cursor = conn.execute(
            """
            UPDATE scheduled_tasks
            SET next_run_at = ?,
                last_run_at = ?,
                data = ?,
                updated_at = ?
            WHERE id = ?
              AND enabled = 1
              AND COALESCE(next_run_at, '') = ?
            """,
            (
                stored.get("next_run_at") or None,
                stored.get("last_run_at") or None,
                db._json(stored),
                now,
                schedule_id,
                expected_next,
            ),
        )
        if cursor.rowcount != 1:
            return None
    return stored


def complete_scheduled_task_run(
    schedule_id: str,
    *,
    expected_last_run_at: str,
    expected_next_run_at: str,
    last_status: str,
    last_task_id: str = "",
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    """Persist a schedule execution result without overwriting newer schedule state."""
    timestamp = updated_at or db._now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM scheduled_tasks WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return None

        stored = json.loads(row["data"])
        if str(stored.get("last_run_at") or "") != expected_last_run_at:
            return None
        if str(stored.get("next_run_at") or "") != expected_next_run_at:
            return None
        stored["last_status"] = last_status
        stored["last_task_id"] = last_task_id
        stored["updated_at"] = timestamp
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET data = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (db._json(stored), timestamp, schedule_id),
        )
    return stored


def set_scheduled_task_enabled(
    schedule_id: str,
    enabled: bool,
    *,
    next_run_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    """Atomically toggle a scheduled task without overwriting run metadata."""
    timestamp = updated_at or db._now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM scheduled_tasks WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        if not row:
            return None

        stored = json.loads(row["data"])
        stored["enabled"] = bool(enabled)
        if next_run_at is not None:
            stored["next_run_at"] = next_run_at
        stored["updated_at"] = timestamp
        conn.execute(
            """
            UPDATE scheduled_tasks
            SET enabled = ?,
                next_run_at = ?,
                last_run_at = ?,
                data = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                1 if enabled else 0,
                stored.get("next_run_at") or None,
                stored.get("last_run_at") or None,
                db._json(stored),
                timestamp,
                schedule_id,
            ),
        )
    return stored


def insert_perception_observation(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body.setdefault("id", f"pobs_{uuid4().hex}")
    body.setdefault("created_at", db._now_iso())
    with db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO perception_observations (
                id, task_id, event_id, event_type, environment_type, source_agent, summary,
                suppressed, process_name, window_title, screen_state_id, data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["id"],
                body.get("task_id") or None,
                body.get("event_id") or None,
                body.get("event_type") or "",
                body.get("environment_type") or None,
                body.get("source_agent") or None,
                body.get("summary") or "",
                1 if body.get("suppressed") else 0,
                body.get("process_name") or None,
                body.get("window_title") or None,
                body.get("screen_state_id") or None,
                db._json(body),
                body["created_at"],
            ),
        )


def insert_perception_suggestion(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body.setdefault("id", f"psug_{uuid4().hex}")
    body.setdefault("created_at", db._now_iso())
    with db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO perception_suggestions (
                id, task_id, suggestion_id, rule_id, severity, title, summary, suppressed,
                status, linked_run_id, expires_at, data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["id"],
                body.get("task_id") or None,
                body.get("suggestion_id") or None,
                body.get("rule_id") or None,
                body.get("severity") or "info",
                body.get("title") or None,
                body.get("summary") or "",
                1 if body.get("suppressed") else 0,
                body.get("status") or "proposed",
                body.get("linked_run_id") or None,
                body.get("expires_at") or None,
                db._json(body),
                body["created_at"],
            ),
        )
