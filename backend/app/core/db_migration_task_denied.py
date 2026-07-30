from __future__ import annotations

import json
import sqlite3
from typing import Any

_LEGACY_DENIED_TASK_SUMMARY_PREFIXES = (
    "automated execution was denied:",
    "denied:",
    "deterministic plan integrity verification failed.",
    "forbidden intent detected.",
    "safetyreviewagent stopped the task",
    "task denied by safety review",
    "tool dry-run preview did not satisfy the approval safety contract.",
    "tool execution was denied:",
    "tool requires approval but does not support a safe dry-run preview.",
)


def task_denied_phase_backfill(conn: sqlite3.Connection) -> None:
    """Recover unambiguous denials written while DENIED aliased CANCELLED."""

    if not _table_exists(conn, "tasks"):
        return
    denied_run_task_ids = _denied_run_task_ids(conn)
    migrated_task_ids: set[str] = set()
    rows = conn.execute("SELECT id, data FROM tasks ORDER BY id").fetchall()
    for row in rows:
        task_id = str(row[0])
        payload = _safe_json_payload(row[1])
        if not payload:
            continue
        status = _text(payload.get("status")).casefold()
        phase = _text(payload.get("phase")).casefold()
        summary = _text(payload.get("final_summary")).casefold()
        explicit_denial = status == "denied" or phase == "denied"
        legacy_aliased_denial = status == "cancelled" and (
            task_id in denied_run_task_ids or summary.startswith(_LEGACY_DENIED_TASK_SUMMARY_PREFIXES)
        )
        if not explicit_denial and not legacy_aliased_denial:
            continue
        payload.update(status="denied", phase="denied", execution_stage="idle")
        conn.execute(
            "UPDATE tasks SET data = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), task_id),
        )
        migrated_task_ids.add(task_id)
    _align_latest_cancelled_runs(conn, migrated_task_ids)


def _denied_run_task_ids(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "runs"):
        return set()
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT task_id FROM runs WHERE phase = 'denied' AND task_id IS NOT NULL"
        ).fetchall()
        if str(row[0] or "").strip()
    }


def _align_latest_cancelled_runs(conn: sqlite3.Connection, task_ids: set[str]) -> None:
    if not task_ids or not _table_exists(conn, "runs"):
        return
    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        f"""
        SELECT id, task_id, data
        FROM runs
        WHERE phase = 'cancelled' AND task_id IN ({placeholders})
        ORDER BY task_id, updated_at DESC, id DESC
        """,  # noqa: S608
        tuple(sorted(task_ids)),
    ).fetchall()
    aligned_task_ids: set[str] = set()
    for row in rows:
        task_id = str(row[1] or "")
        if not task_id or task_id in aligned_task_ids:
            continue
        payload = _safe_json_payload(row[2])
        serialized = row[2]
        if payload:
            payload["phase"] = "denied"
            serialized = json.dumps(payload, ensure_ascii=False)
        conn.execute(
            "UPDATE runs SET phase = 'denied', data = ? WHERE id = ?",
            (serialized, str(row[0])),
        )
        aligned_task_ids.add(task_id)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _safe_json_payload(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""
