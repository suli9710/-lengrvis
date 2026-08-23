from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core import db

TOOL_CALL_DATA_CORRUPT_FIELD = "_tool_call_data_corrupt"


def _authoritative_data(row: sqlite3.Row) -> dict[str, Any]:
    try:
        parsed = json.loads(row["data"])
        if not isinstance(parsed, dict):
            raise TypeError("Tool call data must be an object.")
        data = parsed
    except (TypeError, ValueError):
        data = {TOOL_CALL_DATA_CORRUPT_FIELD: True}
    for field in ("id", "task_id", "step_id", "execution_key", "status", "created_at"):
        data[field] = row[field]
    return data


def reserve_tool_call(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    execution_key = str(data.get("execution_key") or "")
    execution_intent_key = str(data.get("execution_intent_key") or "")
    if not execution_key:
        raise ValueError("Tool execution_key is required for reservation.")
    if not execution_intent_key:
        raise ValueError("Tool execution_intent_key is required for reservation.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        intent_rows = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE json_valid(data)
              AND json_extract(data, '$.execution_intent_key') = ?
            ORDER BY created_at, id
            LIMIT 2
            """,
            (execution_intent_key,),
        ).fetchall()
        if len(intent_rows) > 1:
            raise ValueError("Multiple tool execution rows share one stable intent binding.")
        if intent_rows:
            return _authoritative_data(intent_rows[0]), False
        corrupt_rows = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE task_id = ?
              AND step_id = ?
              AND CASE
                    WHEN json_valid(data) THEN json_type(data) <> 'object'
                    ELSE 1
                  END = 1
            ORDER BY created_at, id
            LIMIT 2
            """,
            (data["task_id"], data["step_id"]),
        ).fetchall()
        if len(corrupt_rows) > 1:
            raise ValueError("Multiple corrupt tool execution rows conflict with the current execution intent.")
        if corrupt_rows:
            return _authoritative_data(corrupt_rows[0]), False
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
        legacy_rows = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE task_id = ?
              AND step_id = ?
              AND json_valid(data)
              AND COALESCE(json_extract(data, '$.tool_name'), '') = ?
              AND COALESCE(json_extract(data, '$.plan_revision'), 0) = ?
              AND COALESCE(json_extract(data, '$.execution_intent_key'), '') = ''
            ORDER BY created_at, id
            LIMIT 2
            """,
            (
                data["task_id"],
                data["step_id"],
                str(data.get("tool_name") or ""),
                int(data.get("plan_revision") or 0),
            ),
        ).fetchall()
        if len(legacy_rows) > 1:
            raise ValueError("Multiple legacy tool execution rows conflict with the current execution intent.")
        if legacy_rows:
            return _authoritative_data(legacy_rows[0]), False
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


def list_tool_calls_for_task(task_id: str, *, limit: int | None = 5000) -> list[dict[str, Any]]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return []
    query_limit = int(limit) if limit is not None else None
    if query_limit is not None and query_limit < 1:
        raise ValueError("Limit must be positive")
    query = """
        SELECT id, task_id, step_id, execution_key, status, data, created_at
        FROM tool_calls
        WHERE task_id = ?
        ORDER BY created_at DESC, id DESC
    """
    args: tuple[Any, ...] = (task_id,)
    if query_limit is not None:
        query += " LIMIT ?"
        args = (*args, query_limit)
    with db.connect() as conn:
        rows = conn.execute(query, args).fetchall()
    return [_authoritative_data(row) for row in rows]


def list_corrupt_tool_call_bindings() -> list[dict[str, Any]]:
    """Return physical bindings for corrupt rows without trusting their JSON payload."""

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            ORDER BY created_at, id
            """
        ).fetchall()
    bindings: list[dict[str, Any]] = []
    for row in rows:
        data = _authoritative_data(row)
        if data.get(TOOL_CALL_DATA_CORRUPT_FIELD) is True:
            bindings.append(data)
    return bindings


def list_agent_message_rows_for_task(task_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM agent_messages WHERE task_id = ? ORDER BY created_at DESC, id DESC",
            (task_id,),
        ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["data"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            messages.append(payload)
    return messages


def list_tool_result_rows_for_call(call_id: str) -> list[dict[str, Any]]:
    """Return every result row for fail-closed duplicate-result handling."""

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT data
            FROM tool_results
            WHERE tool_call_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (call_id,),
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["data"])
        except (TypeError, ValueError):
            payload = {}
        results.append(payload if isinstance(payload, dict) else {})
    return results


def list_tool_result_rows_for_calls(call_ids: list[str]) -> list[dict[str, Any]]:
    """Return every result for a bounded call-id chunk without a row cap."""

    normalized_ids = list(dict.fromkeys(str(call_id or "").strip() for call_id in call_ids))
    normalized_ids = [call_id for call_id in normalized_ids if call_id]
    if not normalized_ids:
        return []
    placeholders = ", ".join("?" for _ in normalized_ids)
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT data
            FROM tool_results
            WHERE tool_call_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,  # noqa: S608 - placeholders, not identifiers, are generated here.
            tuple(normalized_ids),
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["data"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            results.append(payload)
    return results


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


def list_tool_call_ids_with_durable_denial() -> list[str]:
    """Return calls that have a persisted post-tool DENY result."""

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tool_calls.id, tool_calls.created_at
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.status IN ('executing', 'committed', 'outcome_unknown')
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN json_extract(tool_results.data, '$.output.withheld')
                    ELSE 0
                  END = 1
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN lower(COALESCE(json_extract(
                        tool_results.data,
                        '$.output.post_tool_review_verdict'
                    ), ''))
                    ELSE ''
                  END = 'deny'
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN json_extract(tool_results.data, '$.runtime_review_completed')
                    ELSE 0
                  END = 1
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN lower(COALESCE(json_extract(tool_results.data, '$.runtime_review_verdict'), ''))
                    ELSE ''
                  END = 'deny'
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN length(COALESCE(json_extract(tool_results.data, '$.runtime_review_id'), ''))
                    ELSE 0
                  END > 0
            ORDER BY tool_calls.created_at, tool_calls.id
            """
        ).fetchall()
    return [str(row["id"]) for row in rows]


def list_tool_call_ids_with_durable_denial_for_task(task_id: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tool_calls.id, tool_calls.created_at
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.task_id = ?
              AND tool_calls.status = 'committed'
              AND json_valid(tool_results.data)
              AND json_extract(tool_results.data, '$.output.withheld') = 1
              AND lower(COALESCE(json_extract(
                    tool_results.data,
                    '$.output.post_tool_review_verdict'
                  ), '')) = 'deny'
              AND json_extract(tool_results.data, '$.runtime_review_completed') = 1
              AND lower(COALESCE(json_extract(
                    tool_results.data,
                    '$.runtime_review_verdict'
                  ), '')) = 'deny'
              AND length(COALESCE(json_extract(tool_results.data, '$.runtime_review_id'), '')) > 0
            ORDER BY tool_calls.created_at, tool_calls.id
            """,
            (task_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def list_tool_call_ids_with_cleanup_state_for_task(task_id: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tool_calls.id, tool_calls.created_at
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.task_id = ?
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN COALESCE(json_extract(
                             tool_results.data,
                             '$.output.artifact_cleanup_pending'
                         ), 0)
                         OR COALESCE(json_extract(
                             tool_results.data,
                             '$.output.artifact_cleanup_required'
                         ), 0)
                    ELSE 0
                  END = 1
            ORDER BY tool_calls.created_at, tool_calls.id
            """,
            (task_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def list_tool_result_rows_requiring_artifact_cleanup() -> list[tuple[str, str]]:
    """Return result rows in the sole automatically claimable cleanup state."""

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT tool_calls.id AS tool_call_id, tool_results.id AS result_id
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.status IN ('executing', 'committed', 'outcome_unknown')
              AND json_valid(tool_results.data)
              AND COALESCE(json_extract(
                    tool_results.data,
                    '$.output.artifact_cleanup_pending'
                  ), 0) = 1
              AND COALESCE(json_extract(
                    tool_results.data,
                    '$.output.artifact_cleanup_required'
                  ), 0) = 0
            ORDER BY tool_calls.created_at, tool_calls.id, tool_results.created_at, tool_results.id
            """
        ).fetchall()
    return [(str(row["tool_call_id"]), str(row["result_id"])) for row in rows]


def fetch_tool_result_cleanup_snapshot(result_id: str) -> tuple[dict[str, Any], str] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM tool_results WHERE id = ?", (result_id,)).fetchone()
    if not row:
        return None
    raw_data = str(row["data"])
    try:
        payload = json.loads(raw_data)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload, raw_data


def claim_pending_tool_result_cleanup(
    result_id: str,
    *,
    expected_data: str,
    required_data: dict[str, Any],
) -> str | None:
    """Atomically make one pending cleanup permanently manual before deletion."""

    if str(required_data.get("id") or "") != result_id:
        raise ValueError("Cleanup result replacement identity does not match.")
    replacement = db._json(required_data)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM tool_results WHERE id = ?", (result_id,)).fetchone()
        if not row or str(row["data"]) != expected_data:
            return None
        try:
            current = json.loads(row["data"])
        except (TypeError, ValueError):
            return None
        output = current.get("output") if isinstance(current, dict) else None
        if not isinstance(output, dict):
            return None
        if output.get("artifact_cleanup_pending") is not True or output.get("artifact_cleanup_required") is True:
            return None
        cursor = conn.execute(
            "UPDATE tool_results SET data = ? WHERE id = ? AND data = ?",
            (replacement, result_id, expected_data),
        )
        if cursor.rowcount != 1:
            return None
    return replacement


def complete_claimed_tool_result_cleanup(
    result_id: str,
    *,
    expected_data: str,
    completed_data: dict[str, Any],
) -> bool:
    if str(completed_data.get("id") or "") != result_id:
        raise ValueError("Cleanup result completion identity does not match.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE tool_results SET data = ? WHERE id = ? AND data = ?",
            (db._json(completed_data), result_id, expected_data),
        )
    return cursor.rowcount == 1


def fetch_task_execution_recovery_snapshot(task_id: str) -> tuple[dict[str, Any], str, str] | None:
    """Return task payload plus physical updated_at and raw-data CAS tokens."""

    with db.connect() as conn:
        row = conn.execute(
            "SELECT data, updated_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    raw_data = str(row["data"])
    try:
        payload = json.loads(raw_data)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    physical_updated_at = str(row["updated_at"] or "")
    payload["updated_at"] = physical_updated_at
    return payload, physical_updated_at, raw_data


def compare_and_swap_task_execution_recovery(
    data: dict[str, Any],
    *,
    expected_updated_at: str,
    expected_data: str,
) -> bool:
    """Persist one recovery computation only if its task snapshot is current."""

    task_id = str(data.get("id") or "").strip()
    updated_at = str(data.get("updated_at") or "").strip()
    if not task_id or not updated_at:
        raise ValueError("Task recovery CAS requires id and updated_at.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE tasks
            SET data = ?, updated_at = ?
            WHERE id = ? AND updated_at = ? AND data = ?
            """,
            (db._json(data), updated_at, task_id, expected_updated_at, expected_data),
        )
    return cursor.rowcount == 1


def list_committed_tool_call_ids_with_blocked_result() -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tool_calls.id, tool_calls.created_at
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.status = 'committed'
              AND CASE
                    WHEN json_valid(tool_results.data)
                    THEN COALESCE(json_extract(tool_results.data, '$.output.review_pending'), 0)
                         OR COALESCE(json_extract(tool_results.data, '$.output.outcome_unknown'), 0)
                         OR COALESCE(json_extract(
                             tool_results.data,
                             '$.output.artifact_cleanup_pending'
                         ), 0)
                         OR COALESCE(json_extract(
                             tool_results.data,
                             '$.output.artifact_cleanup_required'
                         ), 0)
                    ELSE 1
                  END = 1
            ORDER BY tool_calls.created_at, tool_calls.id
            """
        ).fetchall()
    return [str(row["id"]) for row in rows]


def list_committed_tool_call_ids_with_result() -> list[str]:
    """Return every committed call that has durable result material to validate."""

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT tool_calls.id, tool_calls.created_at
            FROM tool_calls
            JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
            WHERE tool_calls.status = 'committed'
            ORDER BY tool_calls.created_at, tool_calls.id
            """
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


def resolve_denied_tool_call_cleanup(call_id: str, committed_at: str) -> dict[str, Any] | None:
    """CAS a cleanup-only unknown call back to committed after journal validation."""

    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, task_id, step_id, execution_key, status, data, created_at
            FROM tool_calls
            WHERE id = ? AND status = ?
            """,
            (call_id, "outcome_unknown"),
        ).fetchone()
        if not row:
            return None
        data = _authoritative_data(row)
        data["status"] = "committed"
        data["committed_at"] = committed_at
        data["outcome_unknown_at"] = ""
        cursor = conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, data = ?
            WHERE id = ? AND status = ?
            """,
            ("committed", db._json(data), call_id, "outcome_unknown"),
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
        if data.get(TOOL_CALL_DATA_CORRUPT_FIELD) is True:
            return data
        result_rows = conn.execute(
            """
            SELECT data
            FROM tool_results
            WHERE tool_call_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (call_id,),
        ).fetchall()
        result_requires_manual_review = False
        for result_row in result_rows:
            try:
                result_payload = json.loads(result_row[0])
                if not isinstance(result_payload, dict):
                    raise TypeError("Tool result payload must be an object.")
                output = result_payload.get("output", {})
                if not isinstance(output, dict):
                    raise TypeError("Tool result output must be an object.")
                result_requires_manual_review = bool(
                    output.get("review_pending")
                    or output.get("outcome_unknown")
                    or output.get("artifact_cleanup_pending")
                    or output.get("artifact_cleanup_required")
                )
            except (TypeError, ValueError):
                result_requires_manual_review = True
            if result_requires_manual_review:
                break
        if result_rows and not result_requires_manual_review:
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
    if expected_status not in {"created", "executing", "committed"}:
        raise ValueError("Tool outcome_unknown transition requires created, executing, or committed source status.")
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
