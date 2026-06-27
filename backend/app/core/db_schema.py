from __future__ import annotations

import sqlite3
from collections.abc import Callable

EnsureColumns = Callable[[sqlite3.Connection, str, dict[str, str]], None]


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
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(file_id, path, text)")
    except sqlite3.OperationalError:
        # Some Python builds may not ship FTS5. The search service falls back to LIKE.
        pass
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
