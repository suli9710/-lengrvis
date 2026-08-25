"""Local retention enforcement for task bodies and high-sensitivity artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_env
from app.core import db
from app.core.audit import record
from app.core.db_migrations import backfill_missing_memory_metadata

DEFAULT_TASK_DETAIL_RETENTION_DAYS = 30
TASK_DETAIL_RETENTION_DAYS_ENV = "LENGRVIS_TASK_DETAIL_RETENTION_DAYS"
DEFAULT_MEMORY_REVIEW_RETENTION_DAYS = 30
MEMORY_REVIEW_RETENTION_DAYS_ENV = "LENGRVIS_MEMORY_REVIEW_RETENTION_DAYS"
EXPIRED_CONTENT_MARKER = "[expired by retention policy]"
_MAX_RETENTION_DAYS = 3650
_BATCH_SIZE = 200
_TERMINAL_TASK_PHASES = frozenset({"completed", "failed", "cancelled", "denied", "rolled_back", "repair_required"})
_TASK_SUMMARY_KEYS = (
    "id",
    "status",
    "phase",
    "execution_stage",
    "mode",
    "created_at",
    "updated_at",
)
_RUN_SUMMARY_KEYS = (
    "id",
    "mode",
    "requested_engine",
    "engine",
    "phase",
    "task_id",
    "created_at",
    "updated_at",
)
_DATABASE_CHANGE_KEYS = (
    "tasks_expired",
    "runs_expired",
    "chat_messages_deleted",
    "plans_deleted",
    "agent_messages_deleted",
    "run_events_deleted",
    "task_recordings_deleted",
    "safety_reviews_deleted",
    "tool_calls_deleted",
    "tool_results_deleted",
    "approvals_deleted",
    "memories_deleted",
)
_DELETE_TARGETS = frozenset(
    {
        ("tool_results", "tool_call_id"),
        ("run_events", "run_id"),
        ("plans", "task_id"),
        ("agent_messages", "task_id"),
        ("task_recordings", "task_id"),
        ("safety_reviews", "task_id"),
        ("tool_calls", "task_id"),
        ("tool_calls", "id"),
        ("approvals", "task_id"),
        ("approvals", "id"),
    }
)


def configured_retention_days() -> int:
    return _configured_retention_days(TASK_DETAIL_RETENTION_DAYS_ENV, DEFAULT_TASK_DETAIL_RETENTION_DAYS)


def configured_memory_review_retention_days() -> int:
    return _configured_retention_days(
        MEMORY_REVIEW_RETENTION_DAYS_ENV,
        DEFAULT_MEMORY_REVIEW_RETENTION_DAYS,
    )


def _configured_retention_days(env_name: str, default_days: int) -> int:
    raw = str(get_env(env_name) or "").strip()
    if not raw:
        return default_days
    try:
        value = int(raw)
    except ValueError:
        return default_days
    return max(1, min(_MAX_RETENTION_DAYS, value))


def cleanup_expired_task_details(
    *,
    now: datetime | None = None,
    retention_days: int | None = None,
    memory_review_retention_days: int | None = None,
    vacuum: bool = True,
) -> dict[str, Any]:
    """Remove task business content older than the configured retention window.

    Task and run rows are reduced to non-business status summaries so history
    views can still explain that work existed. Detailed plans, messages, tool
    inputs/results, approvals, run events, screenshots, and old chat bodies are
    deleted. The append-only audit chain is intentionally untouched.
    """
    effective_now = _normalized_now(now)
    days = (
        configured_retention_days() if retention_days is None else max(1, min(_MAX_RETENTION_DAYS, int(retention_days)))
    )
    cutoff = effective_now - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    memory_days = (
        configured_memory_review_retention_days()
        if memory_review_retention_days is None
        else max(1, min(_MAX_RETENTION_DAYS, int(memory_review_retention_days)))
    )
    memory_cutoff = effective_now - timedelta(days=memory_days)
    expired_at = effective_now.isoformat()
    counts = {key: 0 for key in _DATABASE_CHANGE_KEYS}

    with db.connect() as conn:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN IMMEDIATE")
        candidate_rows = conn.execute(
            "SELECT id, data, created_at FROM tasks WHERE updated_at < ? ORDER BY id ASC",
            (cutoff_iso,),
        ).fetchall()
        task_rows = [row for row in candidate_rows if _terminal_task(_json_object(str(row["data"] or "")))]
        task_ids = [str(row["id"]) for row in task_rows]

        for row in task_rows:
            replacement = _expired_task_summary(
                task_id=str(row["id"]),
                raw=str(row["data"] or ""),
                created_at=str(row["created_at"] or ""),
                expired_at=expired_at,
                retention_days=days,
            )
            if replacement is None:
                continue
            cursor = conn.execute("UPDATE tasks SET data = ? WHERE id = ?", (replacement, str(row["id"])))
            counts["tasks_expired"] += int(cursor.rowcount or 0)

        for task_batch in _batches(task_ids):
            placeholders = _placeholders(task_batch)
            run_rows = conn.execute(
                f"SELECT id, data, created_at FROM runs WHERE task_id IN ({placeholders})",  # noqa: S608
                tuple(task_batch),
            ).fetchall()
            run_ids = [str(row["id"]) for row in run_rows]
            for row in run_rows:
                replacement = _expired_run_summary(
                    run_id=str(row["id"]),
                    raw=str(row["data"] or ""),
                    created_at=str(row["created_at"] or ""),
                    expired_at=expired_at,
                    retention_days=days,
                )
                if replacement is None:
                    continue
                cursor = conn.execute("UPDATE runs SET data = ? WHERE id = ?", (replacement, str(row["id"])))
                counts["runs_expired"] += int(cursor.rowcount or 0)

            tool_call_rows = conn.execute(
                f"SELECT id FROM tool_calls WHERE task_id IN ({placeholders})",  # noqa: S608
                tuple(task_batch),
            ).fetchall()
            tool_call_ids = [str(row["id"]) for row in tool_call_rows]
            counts["tool_results_deleted"] += _delete_ids(conn, "tool_results", "tool_call_id", tool_call_ids)
            counts["run_events_deleted"] += _delete_ids(conn, "run_events", "run_id", run_ids)

            approval_rows = conn.execute(
                f"SELECT id FROM approvals WHERE task_id IN ({placeholders})",  # noqa: S608
                tuple(task_batch),
            ).fetchall()
            approval_ids = [str(row["id"]) for row in approval_rows]
            _delete_sensitive_integrity_rows(conn, "approvals", approval_ids)

            counts["plans_deleted"] += _delete_ids(conn, "plans", "task_id", task_batch)
            counts["agent_messages_deleted"] += _delete_ids(conn, "agent_messages", "task_id", task_batch)
            counts["task_recordings_deleted"] += _delete_ids(conn, "task_recordings", "task_id", task_batch)
            counts["safety_reviews_deleted"] += _delete_ids(conn, "safety_reviews", "task_id", task_batch)
            counts["tool_calls_deleted"] += _delete_ids(conn, "tool_calls", "task_id", task_batch)
            counts["approvals_deleted"] += _delete_ids(conn, "approvals", "task_id", task_batch)

        # Cover orphaned/legacy rows that no longer have a corresponding task.
        cursor = conn.execute("DELETE FROM task_recordings WHERE created_at < ?", (cutoff_iso,))
        counts["task_recordings_deleted"] += int(cursor.rowcount or 0)
        cursor = conn.execute("DELETE FROM chat_messages WHERE created_at < ?", (cutoff_iso,))
        counts["chat_messages_deleted"] += int(cursor.rowcount or 0)

        counts["memories_deleted"] += _delete_expired_memories(
            conn,
            now=effective_now,
            review_cutoff=memory_cutoff,
        )
        _delete_orphaned_sensitive_rows(conn, cutoff_iso=cutoff_iso, counts=counts)

    counts["diagnostic_packages_deleted"] = _delete_expired_diagnostic_packages(cutoff)
    total_rows_changed = sum(counts[key] for key in _DATABASE_CHANGE_KEYS)
    if vacuum and total_rows_changed:
        _reclaim_deleted_pages()

    result: dict[str, Any] = {
        "retention_days": days,
        "memory_review_retention_days": memory_days,
        "cutoff": cutoff_iso,
        "expired_at": expired_at,
        "counts": counts,
        "rows_changed": total_rows_changed,
        "audit_chain_preserved": True,
        "vacuumed": bool(vacuum and total_rows_changed),
    }
    if total_rows_changed or counts["diagnostic_packages_deleted"]:
        record(
            "privacy.retention_cleanup",
            "LocalRetentionService",
            {
                "retention_days": days,
                "memory_review_retention_days": memory_days,
                "cutoff": cutoff_iso,
                "counts": counts,
                "audit_chain_preserved": True,
            },
        )
    return result


def _delete_expired_memories(conn: Any, *, now: datetime, review_cutoff: datetime) -> int:
    backfill_missing_memory_metadata(conn)
    rows = conn.execute(
        """
        SELECT
            memory.id,
            memory.created_at,
            memory.last_used_at,
            quarantine.state AS lifecycle_state,
            quarantine.user_confirmed AS lifecycle_user_confirmed,
            quarantine.expires_at AS lifecycle_expires_at,
            quarantine.reviewed_at AS lifecycle_reviewed_at,
            quarantine.created_at AS lifecycle_created_at,
            quarantine.updated_at AS lifecycle_updated_at,
            scope.conflict_status AS namespace_conflict_status,
            scope.created_at AS namespace_created_at,
            scope.updated_at AS namespace_updated_at
        FROM memories AS memory
        JOIN memory_quarantine AS quarantine ON quarantine.memory_id = memory.id
        JOIN memory_namespace AS scope ON scope.memory_id = memory.id
        ORDER BY memory.id ASC
        """
    ).fetchall()
    expired_ids: list[str] = []
    for row in rows:
        if _memory_should_expire(
            created_at=str(row["created_at"] or ""),
            last_used_at=str(row["last_used_at"] or ""),
            state=str(row["lifecycle_state"] or ""),
            user_confirmed=bool(row["lifecycle_user_confirmed"]),
            expires_at=str(row["lifecycle_expires_at"] or ""),
            reviewed_at=str(row["lifecycle_reviewed_at"] or ""),
            lifecycle_created_at=str(row["lifecycle_created_at"] or ""),
            lifecycle_updated_at=str(row["lifecycle_updated_at"] or ""),
            conflict_status=str(row["namespace_conflict_status"] or ""),
            namespace_created_at=str(row["namespace_created_at"] or ""),
            namespace_updated_at=str(row["namespace_updated_at"] or ""),
            now=now,
            review_cutoff=review_cutoff,
        ):
            expired_ids.append(str(row["id"]))
    if not expired_ids:
        return 0
    deleted = 0
    for batch in _batches(expired_ids):
        placeholders = _placeholders(batch)
        cursor = conn.execute(
            f"DELETE FROM memories WHERE id IN ({placeholders})",  # noqa: S608
            tuple(batch),
        )
        deleted += int(cursor.rowcount or 0)
    return deleted


def _memory_should_expire(
    *,
    created_at: str,
    last_used_at: str,
    state: str,
    user_confirmed: bool,
    expires_at: str,
    reviewed_at: str,
    lifecycle_created_at: str,
    lifecycle_updated_at: str,
    conflict_status: str,
    namespace_created_at: str,
    namespace_updated_at: str,
    now: datetime,
    review_cutoff: datetime,
) -> bool:
    normalized_expiry = _parse_timestamp(expires_at)
    if normalized_expiry is not None and normalized_expiry <= now:
        return True

    normalized_state = state.strip().casefold()
    normalized_conflict = conflict_status.strip().casefold()
    lifecycle_needs_review = normalized_state != "active" or not user_confirmed
    conflict_needs_review = normalized_conflict not in {"none", "resolved"}
    invalid_expiry_needs_review = bool(expires_at.strip()) and normalized_expiry is None
    if not lifecycle_needs_review and not conflict_needs_review and not invalid_expiry_needs_review:
        return False

    references = [created_at, last_used_at]
    if lifecycle_needs_review or invalid_expiry_needs_review:
        references.extend(
            [
                lifecycle_created_at,
                lifecycle_updated_at,
                reviewed_at,
            ]
        )
    if conflict_needs_review:
        references.extend([namespace_created_at, namespace_updated_at])
    reference = _latest_timestamp(*references)
    return reference is None or reference <= review_cutoff


def _delete_orphaned_sensitive_rows(conn: Any, *, cutoff_iso: str, counts: dict[str, int]) -> None:
    orphan_tool_calls = [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT tool_calls.id
            FROM tool_calls
            LEFT JOIN tasks ON tasks.id = tool_calls.task_id
            WHERE tasks.id IS NULL AND tool_calls.created_at < ?
            """,
            (cutoff_iso,),
        ).fetchall()
    ]
    counts["tool_results_deleted"] += _delete_ids(
        conn,
        "tool_results",
        "tool_call_id",
        orphan_tool_calls,
    )
    counts["tool_calls_deleted"] += _delete_ids(conn, "tool_calls", "id", orphan_tool_calls)

    cursor = conn.execute(
        """
        DELETE FROM tool_results
        WHERE created_at < ?
          AND NOT EXISTS (
              SELECT 1 FROM tool_calls WHERE tool_calls.id = tool_results.tool_call_id
          )
        """,
        (cutoff_iso,),
    )
    counts["tool_results_deleted"] += int(cursor.rowcount or 0)

    orphan_approval_ids = [
        str(row["id"])
        for row in conn.execute(
            """
            SELECT approvals.id
            FROM approvals
            LEFT JOIN tasks ON tasks.id = approvals.task_id
            WHERE tasks.id IS NULL AND approvals.created_at < ?
            """,
            (cutoff_iso,),
        ).fetchall()
    ]
    _delete_sensitive_integrity_rows(conn, "approvals", orphan_approval_ids)
    counts["approvals_deleted"] += _delete_ids(conn, "approvals", "id", orphan_approval_ids)


def _expired_task_summary(
    *,
    task_id: str,
    raw: str,
    created_at: str,
    expired_at: str,
    retention_days: int,
) -> str | None:
    data = _json_object(raw)
    if _already_expired(data):
        return None
    summary = {key: data[key] for key in _TASK_SUMMARY_KEYS if key in data}
    summary.setdefault("id", task_id)
    summary.setdefault("created_at", created_at)
    summary.setdefault("updated_at", created_at)
    summary["user_goal"] = EXPIRED_CONTENT_MARKER
    summary["final_summary"] = EXPIRED_CONTENT_MARKER
    summary["metadata"] = _retention_metadata(expired_at, retention_days)
    return json.dumps(summary, ensure_ascii=False)


def _expired_run_summary(
    *,
    run_id: str,
    raw: str,
    created_at: str,
    expired_at: str,
    retention_days: int,
) -> str | None:
    data = _json_object(raw)
    state = data.get("state")
    if isinstance(state, dict) and _already_expired(state):
        return None
    summary = {key: data[key] for key in _RUN_SUMMARY_KEYS if key in data}
    summary.setdefault("id", run_id)
    summary.setdefault("created_at", created_at)
    summary.setdefault("updated_at", created_at)
    summary["message"] = EXPIRED_CONTENT_MARKER
    summary["state"] = _retention_metadata(expired_at, retention_days)
    summary["error"] = ""
    return json.dumps(summary, ensure_ascii=False)


def _retention_metadata(expired_at: str, retention_days: int) -> dict[str, Any]:
    return {
        "retention": {
            "details_expired": True,
            "expired_at": expired_at,
            "retention_days": retention_days,
        }
    }


def _already_expired(value: dict[str, Any]) -> bool:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else value
    retention = metadata.get("retention") if isinstance(metadata, dict) else None
    return isinstance(retention, dict) and retention.get("details_expired") is True


def _terminal_task(value: dict[str, Any]) -> bool:
    phase = str(value.get("phase") or value.get("status") or "").strip().lower()
    return phase in _TERMINAL_TASK_PHASES


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _delete_ids(conn: Any, table: str, column: str, values: list[str]) -> int:
    if (table, column) not in _DELETE_TARGETS:
        raise ValueError("Unsupported retention delete target.")
    deleted = 0
    for batch in _batches(values):
        placeholders = _placeholders(batch)
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})",  # noqa: S608
            tuple(batch),
        )
        deleted += int(cursor.rowcount or 0)
    return deleted


def _delete_sensitive_integrity_rows(conn: Any, table: str, record_ids: list[str]) -> None:
    for batch in _batches(record_ids):
        placeholders = _placeholders(batch)
        conn.execute(
            f"""DELETE FROM {db.SENSITIVE_RECORD_INTEGRITY_TABLE}
            WHERE table_name = ? AND record_id IN ({placeholders})""",  # noqa: S608
            (table, *batch),
        )


def _delete_expired_diagnostic_packages(cutoff: datetime) -> int:
    export_dir = db.db_path().parent / "diagnostic-packages"
    if not export_dir.is_dir():
        return 0
    deleted = 0
    cutoff_timestamp = cutoff.timestamp()
    for package in export_dir.glob("lengrvis-diagnostics-*"):
        try:
            if package.is_file() and package.stat().st_mtime < cutoff_timestamp:
                package.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def _reclaim_deleted_pages() -> None:
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")


def _normalized_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_timestamp(*values: str) -> datetime | None:
    parsed = [timestamp for value in values if (timestamp := _parse_timestamp(value)) is not None]
    return max(parsed) if parsed else None


def _batches(values: list[str]) -> list[list[str]]:
    return [values[index : index + _BATCH_SIZE] for index in range(0, len(values), _BATCH_SIZE)]


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)
