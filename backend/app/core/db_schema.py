from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

EnsureColumns = Callable[[sqlite3.Connection, str, dict[str, str]], None]

logger = logging.getLogger(__name__)

FTS_TABLE = "document_chunks_fts"
FTS_MODE_TRIGRAM = "trigram"
FTS_MODE_PLAIN = "plain"
FTS_MODE_UNAVAILABLE = "unavailable"
_TRIGRAM_SUPPORT_CACHE: bool | None = None


def initialize_schema(conn: sqlite3.Connection, ensure_columns: EnsureColumns) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_created_at
            ON tasks(created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS goals (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            parent_goal_id TEXT,
            status TEXT NOT NULL,
            depth INTEGER NOT NULL,
            task_ids TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_goals_scope_status_depth
            ON goals(scope, status, depth, created_at);
        CREATE INDEX IF NOT EXISTS idx_goals_parent_goal_id
            ON goals(parent_goal_id);
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_messages_task_created
            ON agent_messages(task_id, created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            engine TEXT NOT NULL,
            phase TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_task_id
            ON runs(task_id);
        CREATE INDEX IF NOT EXISTS idx_runs_phase_updated
            ON runs(phase, updated_at);
        CREATE TABLE IF NOT EXISTS run_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            name TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_run_sequence
            ON run_events(run_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_run_events_run_created
            ON run_events(run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_run_events_created
            ON run_events(created_at);
        CREATE TABLE IF NOT EXISTS task_recordings (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            file_name TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            image BLOB NOT NULL,
            data TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_task_recordings_task_id
            ON task_recordings(task_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_task_recordings_step_id
            ON task_recordings(task_id, step_id, captured_at);
        CREATE TABLE IF NOT EXISTS safety_reviews (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_safety_reviews_task_created
            ON safety_reviews(task_id, created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS tool_calls (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            execution_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'created',
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tool_results (
            id TEXT PRIMARY KEY,
            tool_call_id TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS approvals (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            step_id TEXT,
            data TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mobile_pairings (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mobile_pairings_status_expires
            ON mobile_pairings(status, expires_at);
        CREATE TABLE IF NOT EXISTS mobile_devices (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 0,
            prev_hash TEXT NOT NULL DEFAULT '',
            event_hash TEXT NOT NULL DEFAULT '',
            hmac TEXT NOT NULL DEFAULT '',
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_events_task_created
            ON audit_events(task_id, created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS audit_chain_heads (
            id TEXT PRIMARY KEY,
            sequence INTEGER NOT NULL,
            event_hash TEXT NOT NULL,
            event_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_chain_heads_sequence
            ON audit_chain_heads(sequence DESC, created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS llm_usage_events (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            mode TEXT NOT NULL,
            task TEXT NOT NULL,
            purpose TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            total_cost_usd REAL,
            estimated INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_events_created_at
            ON llm_usage_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_events_provider_model
            ON llm_usage_events(provider, model, created_at);
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS permission_policies (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS indexed_files (
            id TEXT PRIMARY KEY,
            normalized_path TEXT UNIQUE NOT NULL,
            data TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            name TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_chunk_embeddings (
            id TEXT PRIMARY KEY,
            chunk_id TEXT UNIQUE NOT NULL,
            file_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            embedding TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_document_chunk_embeddings_file_id
            ON document_chunk_embeddings(file_id);
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id TEXT PRIMARY KEY,
            cron TEXT NOT NULL,
            goal TEXT NOT NULL,
            mode TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT,
            last_run_at TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wakeups (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT,
            status TEXT NOT NULL,
            due_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wakeups_status_due
            ON wakeups(status, due_at);
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            task_id TEXT,
            embedding BLOB,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS session_contexts (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS perception_observations (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_id TEXT,
            event_type TEXT NOT NULL,
            environment_type TEXT,
            source_agent TEXT,
            summary TEXT NOT NULL,
            suppressed INTEGER NOT NULL DEFAULT 0,
            process_name TEXT,
            window_title TEXT,
            screen_state_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_perception_observations_created
            ON perception_observations(created_at);
        CREATE INDEX IF NOT EXISTS idx_perception_observations_task
            ON perception_observations(task_id, created_at);
        CREATE TABLE IF NOT EXISTS perception_suggestions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            suggestion_id TEXT,
            rule_id TEXT,
            severity TEXT NOT NULL,
            title TEXT,
            summary TEXT NOT NULL,
            suppressed INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'proposed',
            linked_run_id TEXT,
            expires_at TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_perception_suggestions_created
            ON perception_suggestions(created_at);
        CREATE INDEX IF NOT EXISTS idx_perception_suggestions_task
            ON perception_suggestions(task_id, created_at);
        """
    )
    ensure_document_chunks_fts(conn)
    ensure_columns(
        conn,
        "audit_events",
        {
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "prev_hash": "TEXT NOT NULL DEFAULT ''",
            "event_hash": "TEXT NOT NULL DEFAULT ''",
            "hmac": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_events_sequence
            ON audit_events(sequence)
            WHERE sequence > 0
        """
    )
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN
            SELECT RAISE(ABORT, 'audit_events is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_chain_heads_no_update
        BEFORE UPDATE ON audit_chain_heads
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_heads is append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS audit_chain_heads_no_delete
        BEFORE DELETE ON audit_chain_heads
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_heads is append-only');
        END;
        """
    )
    ensure_columns(
        conn,
        "tool_calls",
        {
            "execution_key": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'created'",
        },
    )
    conn.execute(
        """
        UPDATE tool_calls
        SET execution_key = COALESCE(NULLIF(json_extract(data, '$.execution_key'), ''), execution_key),
            status = COALESCE(NULLIF(json_extract(data, '$.status'), ''), status)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_calls_execution_key
            ON tool_calls(execution_key)
            WHERE execution_key <> ''
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tool_calls_status_created
            ON tool_calls(status, created_at, id)
        """
    )
    ensure_columns(
        conn,
        "llm_usage_events",
        {
            "data": "TEXT NOT NULL DEFAULT '{}'",
        },
    )
    ensure_columns(
        conn,
        "perception_suggestions",
        {
            "status": "TEXT NOT NULL DEFAULT 'proposed'",
            "linked_run_id": "TEXT",
            "expires_at": "TEXT",
        },
    )


def ensure_document_chunks_fts(conn: sqlite3.Connection) -> str:
    """Ensure the optional document FTS table exists and mirrors chunks.

    The preferred shape uses SQLite FTS5's trigram tokenizer for substring and
    CJK-friendly matching. Some bundled SQLite builds do not include that
    tokenizer, so schema init must fall back to plain FTS5 instead of failing
    startup. If FTS5 itself is unavailable, search paths fall back to LIKE.
    """

    ddl = _fts_table_sql(conn)
    existing_mode = _fts_mode_from_sql(ddl)
    desired_mode = (
        FTS_MODE_TRIGRAM
        if existing_mode == FTS_MODE_TRIGRAM or _sqlite_supports_trigram(conn)
        else FTS_MODE_PLAIN
    )

    if ddl and existing_mode not in {desired_mode, FTS_MODE_UNAVAILABLE}:
        _drop_fts_table(conn)
        ddl = None
        existing_mode = FTS_MODE_UNAVAILABLE

    if not ddl:
        created_mode = _create_fts_table(conn, desired_mode)
        if created_mode == FTS_MODE_UNAVAILABLE and desired_mode == FTS_MODE_TRIGRAM:
            created_mode = _create_fts_table(conn, FTS_MODE_PLAIN)
        if created_mode == FTS_MODE_UNAVAILABLE:
            logger.info("document chunk FTS5 unavailable; file content search will use LIKE fallback")
            return FTS_MODE_UNAVAILABLE
        existing_mode = created_mode

    _backfill_document_chunks_fts(conn)
    return existing_mode


def document_chunks_fts_mode(conn: sqlite3.Connection) -> str:
    return _fts_mode_from_sql(_fts_table_sql(conn))


def _sqlite_supports_trigram(conn: sqlite3.Connection) -> bool:
    global _TRIGRAM_SUPPORT_CACHE
    if _TRIGRAM_SUPPORT_CACHE is not None:
        return _TRIGRAM_SUPPORT_CACHE
    probe = "document_chunks_fts_trigram_probe"
    try:
        conn.execute(f"CREATE VIRTUAL TABLE {probe} USING fts5(text, tokenize='trigram')")
    except sqlite3.OperationalError:
        _TRIGRAM_SUPPORT_CACHE = False
    else:
        _TRIGRAM_SUPPORT_CACHE = True
    finally:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {probe}")
        except sqlite3.OperationalError:
            pass
    return bool(_TRIGRAM_SUPPORT_CACHE)


def _fts_table_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (FTS_TABLE,),
    ).fetchone()
    return str(row["sql"] if row is not None else "")


def _fts_mode_from_sql(sql: str) -> str:
    lowered = str(sql or "").lower()
    if not lowered:
        return FTS_MODE_UNAVAILABLE
    if "tokenize" in lowered and "trigram" in lowered:
        return FTS_MODE_TRIGRAM
    return FTS_MODE_PLAIN


def _drop_fts_table(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(f"DROP TABLE IF EXISTS {FTS_TABLE}")
    except sqlite3.OperationalError as exc:
        logger.info("could not drop stale document chunk FTS table: %s", exc)


def _create_fts_table(conn: sqlite3.Connection, mode: str) -> str:
    tokenizer = ", tokenize='trigram'" if mode == FTS_MODE_TRIGRAM else ""
    try:
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(file_id, path, text{tokenizer})")
    except sqlite3.OperationalError as exc:
        logger.info("could not create %s document chunk FTS table: %s", mode, exc)
        return FTS_MODE_UNAVAILABLE
    return mode


def _backfill_document_chunks_fts(conn: sqlite3.Connection) -> None:
    fts_count_sql = "SELECT COUNT(*) AS count FROM document_chunks_fts"
    fts_delete_sql = "DELETE FROM document_chunks_fts"
    fts_backfill_sql = """
        INSERT INTO document_chunks_fts (file_id, path, text)
        SELECT dc.file_id, f.normalized_path, dc.text
        FROM document_chunks dc
        JOIN indexed_files f ON f.id = dc.file_id
        ORDER BY dc.file_id, dc.chunk_index
        """
    try:
        chunk_count = int(conn.execute("SELECT COUNT(*) AS count FROM document_chunks").fetchone()["count"] or 0)
        fts_count = int(conn.execute(fts_count_sql).fetchone()["count"] or 0)
    except sqlite3.Error as exc:
        logger.debug("could not inspect document chunk FTS table for backfill: %s", exc, exc_info=True)
        return
    if fts_count == chunk_count:
        return
    try:
        conn.execute(fts_delete_sql)
        conn.execute(fts_backfill_sql)
    except sqlite3.Error as exc:
        logger.debug("could not backfill document chunk FTS table: %s", exc, exc_info=True)
