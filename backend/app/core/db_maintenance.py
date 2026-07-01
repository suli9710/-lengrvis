from __future__ import annotations

from typing import Any

from app.core import db
from app.core.db_diagnostics import build_local_product_diagnostics

PERSONAL_DATA_TABLES: tuple[str, ...] = (
    "tasks",
    "chat_messages",
    "plans",
    "goals",
    "agent_messages",
    "runs",
    "run_events",
    "task_recordings",
    "safety_reviews",
    "tool_calls",
    "tool_results",
    "approvals",
    "mobile_pairings",
    "mobile_devices",
    "llm_usage_events",
    "indexed_files",
    "document_chunks",
    "document_chunk_embeddings",
    "scheduled_tasks",
    "wakeups",
    "memories",
    "session_contexts",
    "perception_observations",
    "perception_suggestions",
)
SETTINGS_TABLES: tuple[str, ...] = ("app_settings", "permission_policies")


def erase_local_user_data(*, include_settings: bool = False) -> dict[str, int]:
    """Delete locally stored user content (PIPL/GDPR local deletion entry).

    Audit events are preserved by default so the tamper-evident chain can show
    that the erase happened; callers should append an erase audit event after
    this returns. The database file is VACUUMed so deleted rows do not survive
    in free pages.
    """
    tables = PERSONAL_DATA_TABLES + (SETTINGS_TABLES if include_settings else ())
    counts: dict[str, int] = {}
    with db.connect() as conn:
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
            counts[table] = int(row[0] or 0)
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
    with db.connect() as conn:
        conn.execute("VACUUM")
    if include_settings:
        db._notify_settings_invalidated()
    return counts


def local_product_diagnostics(*, recent_limit: int = 200) -> dict[str, Any]:
    limit = db._query_limit(recent_limit)
    return build_local_product_diagnostics(
        sample_size=limit,
        database_present=db.db_path().exists(),
        tasks=db.fetch_many("tasks", limit=limit),
        runs=db.fetch_many("runs", limit=limit),
        approvals=db.fetch_many("approvals", limit=limit),
        mobile_devices=db.fetch_many("mobile_devices", limit=limit),
        mobile_pairings=db.fetch_many("mobile_pairings", limit=limit),
        tool_results=db.fetch_many("tool_results", limit=limit),
        audits=db.fetch_many("audit_events", limit=limit),
    )
