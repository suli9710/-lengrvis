from __future__ import annotations

from typing import Any

from app.core import db
from app.core.db_diagnostics import build_local_product_diagnostics

PERSONAL_DATA_TABLES: tuple[str, ...] = (
    # Child/auth tables come first so row counts remain accurate even when
    # SQLite foreign-key cascades are enabled.
    "mobile_refresh_tokens",
    "token_families",
    "device_credentials",
    "execution_exceptions",
    "automation_run_items",
    "automation_runs",
    "automation_triggers",
    "automation_template_versions",
    "application_grants",
    "automation_templates",
    "intent_capsules",
    "run_budget_ledgers",
    "tool_results",
    "tool_calls",
    "approvals",
    "task_recordings",
    "safety_reviews",
    "run_events",
    "runs",
    "agent_messages",
    "plans",
    "goals",
    "chat_messages",
    "tasks",
    "mobile_pairings",
    "mobile_devices",
    "llm_usage_events",
    "document_chunk_embeddings",
    "document_chunks",
    "indexed_files",
    "scheduled_tasks",
    "wakeups",
    "memory_active_successors",
    "memory_quarantine",
    "memory_namespace",
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
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN IMMEDIATE")
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
            counts[table] = int(row[0] or 0)
            conn.execute(f"DELETE FROM {table}")  # noqa: S608

        integrity_kinds = ["approvals"]
        if include_settings:
            integrity_kinds.extend(SETTINGS_TABLES)
        placeholders = ",".join("?" for _ in integrity_kinds)
        row = conn.execute(
            f"""SELECT COUNT(*) FROM {db.SENSITIVE_RECORD_INTEGRITY_TABLE}
            WHERE table_name IN ({placeholders})""",  # noqa: S608
            tuple(integrity_kinds),
        ).fetchone()
        counts[db.SENSITIVE_RECORD_INTEGRITY_TABLE] = int(row[0] or 0)
        conn.execute(
            f"""DELETE FROM {db.SENSITIVE_RECORD_INTEGRITY_TABLE}
            WHERE table_name IN ({placeholders})""",  # noqa: S608
            tuple(integrity_kinds),
        )
        # Also clear the presence ledger for the erased tables. Leaving these
        # rows behind (a) leaks that records with those ids/created_at once
        # existed (a PIPL/GDPR residual) and (b) makes sensitive_integrity_check
        # report the records as missing, flipping ok=False and locking out all
        # local writes in fail-closed/commercial mode after a compliant erase.
        conn.execute(
            f"""DELETE FROM sensitive_record_presence
            WHERE table_name IN ({placeholders})""",  # noqa: S608
            tuple(integrity_kinds),
        )

        remaining = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)  # noqa: S608
            for table in tables
        }
        residual = {table: count for table, count in remaining.items() if count}
        if residual:
            raise RuntimeError(f"Local data erase verification failed: {sorted(residual)}")
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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
