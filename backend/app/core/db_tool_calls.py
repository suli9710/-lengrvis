from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core import db


def _authoritative_data(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["data"])
    for field in ("id", "task_id", "step_id", "execution_key", "status", "created_at"):
        data[field] = row[field]
    return data


def reserve_tool_call(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    execution_key = str(data.get("execution_key") or "")
    if not execution_key:
        raise ValueError("Tool execution_key is required for reservation.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE execution_key = ?
            """,
            (execution_key,),
        ).fetchone()
        if row:
            return _authoritative_data(row), False
        stored = db._json(data)
        try:
            conn.execute(
                """
                INSERT INTO tool_calls (id, task_id, step_id, execution_key, status, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data["task_id"],
                    data["step_id"],
                    execution_key,
                    data.get("status", "prepared"),
                    stored,
                    data["created_at"],
                ),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT id, task_id, step_id, execution_key, status, data, created_at
                FROM tool_calls
                WHERE execution_key = ?
                """,
                (execution_key,),
            ).fetchone()
            if row:
                return _authoritative_data(row), False
            raise
    return data, True


def fetch_tool_call(call_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ?
            """,
            (call_id,),
        ).fetchone()
    return _authoritative_data(row) if row else None


def list_tool_calls_for_task(task_id: str, *, limit: int = 5000) -> list[dict[str, Any]]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return []
    query_limit = int(limit)
    if query_limit < 1:
        raise ValueError("Limit must be positive")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE task_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (task_id, query_limit),
        ).fetchall()
    return [_authoritative_data(row) for row in rows]


def list_tool_call_ids_by_status(status: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM tool_calls
            WHERE status = ?
            ORDER BY created_at, id
            """,
            (status,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def claim_tool_call_execution(call_id: str, started_at: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ? AND status = ?
            """,
            (call_id, "prepared"),
        ).fetchone()
        if not row:
            return None
        data = _authoritative_data(row)
        data["status"] = "executing"
        data["started_at"] = started_at
        cursor = conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, data = ?
            WHERE id = ? AND status = ?
            """,
            ("executing", db._json(data), call_id, "prepared"),
        )
        if cursor.rowcount != 1:
            return None
    return data


def commit_tool_call_execution(call_id: str, committed_at: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ? AND status = ?
            """,
            (call_id, "executing"),
        ).fetchone()
        if not row:
            return None
        data = _authoritative_data(row)
        data["status"] = "committed"
        data["committed_at"] = committed_at
        cursor = conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, data = ?
            WHERE id = ? AND status = ?
            """,
            ("committed", db._json(data), call_id, "executing"),
        )
        if cursor.rowcount != 1:
            return None
    return data


def recover_tool_call_execution(call_id: str, recovered_at: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ? AND status = ?
            """,
            (call_id, "executing"),
        ).fetchone()
        if not row:
            return None
        data = _authoritative_data(row)
        has_result = conn.execute(
            "SELECT 1 FROM tool_results WHERE tool_call_id = ? LIMIT 1",
            (call_id,),
        ).fetchone()
        if has_result:
            data["status"] = "committed"
            data["committed_at"] = recovered_at
        else:
            data["status"] = "outcome_unknown"
            data["outcome_unknown_at"] = recovered_at
        cursor = conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, data = ?
            WHERE id = ? AND status = ?
            """,
            (data["status"], db._json(data), call_id, "executing"),
        )
        if cursor.rowcount != 1:
            return None
    return data


def mark_tool_call_outcome_unknown(
    call_id: str,
    outcome_unknown_at: str,
    *,
    expected_status: str,
) -> dict[str, Any] | None:
    if expected_status not in {"executing", "committed"}:
        raise ValueError("Tool outcome_unknown transition requires executing or committed source status.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ? AND status = ?
            """,
            (call_id, expected_status),
        ).fetchone()
        if not row:
            return None
        data = _authoritative_data(row)
        data["status"] = "outcome_unknown"
        data["outcome_unknown_at"] = outcome_unknown_at
        cursor = conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, data = ?
            WHERE id = ? AND status = ?
            """,
            ("outcome_unknown", db._json(data), call_id, expected_status),
        )
        if cursor.rowcount != 1:
            return None
    return data
