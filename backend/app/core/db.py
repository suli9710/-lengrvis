from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel

from app.config import get_base_settings


_DATA_DIR_OVERRIDE: ContextVar[str | None] = ContextVar("marvis_data_dir_override", default=None)
AUDIT_GENESIS_HASH = "0" * 64
AUDIT_HMAC_SECRET_FILE = "audit_hmac.secret"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    override = _DATA_DIR_OVERRIDE.get()
    path = Path(override or get_base_settings().data_dir) / "marvis.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def using_data_dir(data_dir: str | Path | None) -> Iterator[None]:
    if not data_dir:
        yield
        return
    token = _DATA_DIR_OVERRIDE.set(str(data_dir))
    try:
        yield
    finally:
        _DATA_DIR_OVERRIDE.reset(token)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _model_json(model: BaseModel) -> str:
    return model.model_dump_json()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
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
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(file_id, path, text)"
            )
        except sqlite3.OperationalError:
            # Some Python builds may not ship FTS5. The search service falls back to LIKE.
            pass
        _ensure_columns(
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
            """
        )
        _ensure_columns(
            conn,
            "llm_usage_events",
            {
                "data": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            conn,
            "perception_suggestions",
            {
                "status": "TEXT NOT NULL DEFAULT 'proposed'",
                "linked_run_id": "TEXT",
                "expires_at": "TEXT",
            },
        )


def upsert_model(table: str, model: BaseModel, *, task_id: str | None = None, status: str | None = None) -> None:
    data = json.loads(model.model_dump_json())
    now = data.get("updated_at") or data.get("created_at") or _now_iso()
    with connect() as conn:
        if table == "tasks":
            conn.execute(
                """
                INSERT INTO tasks (id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
                """,
                (data["id"], _json(data), data.get("created_at", now), now),
            )
            return
        if table == "chat_messages":
            conn.execute(
                "INSERT OR REPLACE INTO chat_messages (id, data, created_at) VALUES (?, ?, ?)",
                (data["id"], _json(data), data.get("created_at", now)),
            )
            return
        if table == "plans":
            conn.execute(
                "INSERT OR REPLACE INTO plans (id, task_id, data, created_at) VALUES (?, ?, ?, ?)",
                (data["id"], data["task_id"], _json(data), now),
            )
            return
        if table == "goals":
            conn.execute(
                """
                INSERT INTO goals (id, scope, parent_goal_id, status, depth, task_ids, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope=excluded.scope,
                    parent_goal_id=excluded.parent_goal_id,
                    status=excluded.status,
                    depth=excluded.depth,
                    task_ids=excluded.task_ids,
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    data.get("scope", "default"),
                    data.get("parent_goal_id") or None,
                    data.get("status", "active"),
                    int(data.get("depth") or 0),
                    _json(data.get("related_task_ids") or data.get("task_ids") or []),
                    _json(data),
                    data.get("created_at", now),
                    now,
                ),
            )
            return
        if table == "agent_messages":
            conn.execute(
                "INSERT OR REPLACE INTO agent_messages (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (data["id"], data["task_id"], data.get("step_id"), _json(data), data.get("created_at", now)),
            )
            return
        if table == "runs":
            conn.execute(
                """
                INSERT INTO runs (id, task_id, engine, phase, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    task_id=excluded.task_id,
                    engine=excluded.engine,
                    phase=excluded.phase,
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    data.get("task_id") or None,
                    data.get("engine", "auto"),
                    data.get("phase", "created"),
                    _json(data),
                    data.get("created_at", now),
                    data.get("updated_at", now),
                ),
            )
            return
        if table == "run_events":
            conn.execute("BEGIN IMMEDIATE")
            _insert_run_event_locked(conn, data)
            return
        if table == "safety_reviews":
            conn.execute(
                "INSERT OR REPLACE INTO safety_reviews (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (data["id"], data["task_id"], data.get("step_id"), _json(data), data.get("created_at", now)),
            )
            return
        if table == "tool_calls":
            conn.execute(
                "INSERT OR REPLACE INTO tool_calls (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
                (data["id"], data["task_id"], data["step_id"], _json(data), data.get("created_at", now)),
            )
            return
        if table == "tool_results":
            conn.execute(
                "INSERT OR REPLACE INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
                (data["id"], data["tool_call_id"], _json(data), data.get("created_at", now)),
            )
            return
        if table == "approvals":
            conn.execute(
                "INSERT OR REPLACE INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["id"],
                    data["task_id"],
                    data.get("step_id"),
                    _json(data),
                    status or data.get("status", "pending"),
                    data.get("created_at", now),
                ),
            )
            return
        if table == "audit_events":
            conn.execute("BEGIN IMMEDIATE")
            stored = _prepare_audit_event_locked(conn, data)
            conn.execute(
                """
                INSERT INTO audit_events (id, task_id, event_type, actor, sequence, prev_hash, event_hash, hmac, data, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored["id"],
                    stored.get("task_id"),
                    stored["event_type"],
                    stored["actor"],
                    stored["sequence"],
                    stored["prev_hash"],
                    stored["event_hash"],
                    stored["hmac"],
                    _json(stored),
                    stored.get("created_at", now),
                ),
            )
            return
        if table == "scheduled_tasks":
            conn.execute(
                """
                INSERT INTO scheduled_tasks (id, cron, goal, mode, enabled, next_run_at, last_run_at, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    cron=excluded.cron,
                    goal=excluded.goal,
                    mode=excluded.mode,
                    enabled=excluded.enabled,
                    next_run_at=excluded.next_run_at,
                    last_run_at=excluded.last_run_at,
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    data["cron"],
                    data["goal"],
                    data.get("mode", "efficiency"),
                    1 if data.get("enabled", True) else 0,
                    data.get("next_run_at") or None,
                    data.get("last_run_at") or None,
                    _json(data),
                    data.get("created_at", now),
                    now,
                ),
            )
            return
        if table == "wakeups":
            conn.execute(
                """
                INSERT INTO wakeups (id, source, source_id, status, due_at, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    source_id=excluded.source_id,
                    status=excluded.status,
                    due_at=excluded.due_at,
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    data.get("source", "schedule"),
                    data.get("source_id") or "",
                    data.get("status", "pending"),
                    data.get("due_at") or data.get("created_at", now),
                    _json(data),
                    data.get("created_at", now),
                    now,
                ),
            )
            return
        if table == "memories":
            conn.execute(
                """
                INSERT OR REPLACE INTO memories (id, kind, content, tags, task_id, embedding, data, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    data.get("kind", "fact"),
                    data.get("content", ""),
                    ",".join(data.get("tags") or []),
                    data.get("task_id") or "",
                    data.pop("embedding_blob", None) if isinstance(data.get("embedding_blob", None), (bytes, bytearray)) else None,
                    _json(data),
                    data.get("created_at", now),
                    data.get("last_used_at") or None,
                ),
            )
            return
        if table == "session_contexts":
            conn.execute(
                """
                INSERT INTO session_contexts (id, data, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
                """,
                (data["id"], _json(data), data.get("created_at", now), now),
            )
            return
    raise ValueError(f"Unsupported table: {table}")


def fetch_one(table: str, record_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(f"SELECT data FROM {table} WHERE id = ?", (record_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def fetch_many(table: str, where: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    query = f"SELECT data FROM {table}"
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY created_at DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(query, (*args, limit)).fetchall()
    return [json.loads(row["data"]) for row in rows]


def claim_scheduled_task_run(
    schedule_id: str,
    *,
    expected_next_run_at: str,
    claimed_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Atomically claim one due scheduled task before side effects run."""
    stored = dict(claimed_data)
    now = str(stored.get("updated_at") or _now_iso())
    expected_next = expected_next_run_at or ""
    with connect() as conn:
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
                _json(stored),
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
    timestamp = updated_at or _now_iso()
    with connect() as conn:
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
            (_json(stored), timestamp, schedule_id),
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
    timestamp = updated_at or _now_iso()
    with connect() as conn:
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
                _json(stored),
                timestamp,
                schedule_id,
            ),
        )
    return stored


def insert_perception_observation(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body.setdefault("id", f"pobs_{uuid4().hex}")
    body.setdefault("created_at", _now_iso())
    with connect() as conn:
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
                _json(body),
                body["created_at"],
            ),
        )


def insert_perception_suggestion(payload: dict[str, Any]) -> None:
    body = dict(payload)
    body.setdefault("id", f"psug_{uuid4().hex}")
    body.setdefault("created_at", _now_iso())
    with connect() as conn:
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
                _json(body),
                body["created_at"],
            ),
        )


def next_run_event_sequence(run_id: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM run_events WHERE run_id = ?", (run_id,)).fetchone()
    return int(row["sequence"] or 0) + 1


def insert_run_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(model.model_dump_json()) if isinstance(model, BaseModel) else dict(model)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _insert_run_event_locked(conn, data)


def _insert_run_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    now = data.get("created_at") or _now_iso()
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
            _json(stored),
            now,
        ),
    )
    return stored


def insert_audit_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(model.model_dump_json()) if isinstance(model, BaseModel) else dict(model)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        stored = _prepare_audit_event_locked(conn, data)
        conn.execute(
            """
            INSERT INTO audit_events (id, task_id, event_type, actor, sequence, prev_hash, event_hash, hmac, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stored["id"],
                stored.get("task_id"),
                stored["event_type"],
                stored["actor"],
                stored["sequence"],
                stored["prev_hash"],
                stored["event_hash"],
                stored["hmac"],
                _json(stored),
                stored["created_at"],
            ),
        )
    return stored


def verify_audit_log(*, limit: int | None = None) -> dict[str, Any]:
    query = """
        SELECT id, sequence, prev_hash, event_hash, hmac, data
        FROM audit_events
        WHERE sequence > 0
        ORDER BY sequence ASC, created_at ASC, id ASC
    """
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (max(1, int(limit)),)
    else:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total, COALESCE(MAX(sequence), 0) AS max_sequence FROM audit_events WHERE sequence > 0").fetchone()
        expected_total = int(row["total"] or 0)
        max_sequence = int(row["max_sequence"] or 0)
        if expected_total != max_sequence:
            return {
                "ok": False,
                "checked": 0,
                "last_hash": AUDIT_GENESIS_HASH,
                "failures": [{"reason": "sequence_gap", "expected_events": expected_total, "max_sequence": max_sequence}],
            }

    expected_prev = AUDIT_GENESIS_HASH
    checked = 0
    failures: list[dict[str, Any]] = []
    last_hash = expected_prev
    with connect() as conn:
        rows = conn.execute(query, args).fetchall()

    for index, row in enumerate(rows, start=1):
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            failures.append({"id": row["id"], "sequence": row["sequence"], "reason": "invalid_json"})
            break

        sequence = int(data.get("sequence") or row["sequence"] or 0)
        prev_hash = str(data.get("prev_hash") or row["prev_hash"] or "")
        event_hash = str(data.get("event_hash") or row["event_hash"] or "")
        event_hmac = str(data.get("hmac") or row["hmac"] or "")

        if sequence != index:
            failures.append({"id": row["id"], "sequence": sequence, "reason": "sequence_gap", "expected": index})
            break
        if prev_hash != expected_prev:
            failures.append({"id": row["id"], "sequence": sequence, "reason": "prev_hash_mismatch"})
            break

        unsigned = dict(data)
        unsigned["sequence"] = sequence
        unsigned["prev_hash"] = prev_hash
        unsigned["event_hash"] = ""
        unsigned["hmac"] = ""
        computed_hash = _audit_event_hash(unsigned)
        computed_hmac = _audit_event_hmac(computed_hash)
        if not hmac.compare_digest(event_hash, computed_hash):
            failures.append({"id": row["id"], "sequence": sequence, "reason": "event_hash_mismatch"})
            break
        if not hmac.compare_digest(event_hmac, computed_hmac):
            failures.append({"id": row["id"], "sequence": sequence, "reason": "hmac_mismatch"})
            break

        checked += 1
        last_hash = event_hash
        expected_prev = event_hash

    return {
        "ok": not failures,
        "checked": checked,
        "last_hash": last_hash,
        "failures": failures,
    }


def _prepare_audit_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    stored = dict(data)
    stored.setdefault("id", f"audit_{uuid4().hex}")
    stored["created_at"] = stored.get("created_at") or _now_iso()

    row = conn.execute(
        "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC, created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    sequence = int(row["sequence"] or 0) + 1 if row else 1
    prev_hash = str(row["event_hash"] or "") if row else AUDIT_GENESIS_HASH

    stored["sequence"] = sequence
    stored["prev_hash"] = prev_hash
    stored["event_hash"] = ""
    stored["hmac"] = ""
    event_hash = _audit_event_hash(stored)
    stored["event_hash"] = event_hash
    stored["hmac"] = _audit_event_hmac(event_hash)
    return stored


def _audit_event_hash(event: dict[str, Any]) -> str:
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _audit_event_hmac(event_hash: str) -> str:
    return hmac.new(_audit_hmac_secret().encode("utf-8"), event_hash.encode("utf-8"), sha256).hexdigest()


def _audit_hmac_secret() -> str:
    configured = (os.environ.get("MAVRIS_AUDIT_HMAC_SECRET") or os.environ.get("MARVIS_AUDIT_HMAC_SECRET") or "").strip()
    if configured:
        return configured

    secret_path = db_path().parent / AUDIT_HMAC_SECRET_FILE
    try:
        if secret_path.exists():
            value = secret_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_hex(32)
        secret_path.write_text(value, encoding="utf-8")
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
        return value
    except OSError:
        return ""


def fetch_run_events(run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    with connect() as conn:
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
    with connect() as conn:
        cursor = conn.execute("DELETE FROM run_events WHERE created_at < ?", (cutoff_iso,))
    return int(cursor.rowcount or 0)


def claim_approval_for_execution(approval_id: str, consumed_at: str) -> dict[str, Any] | None:
    """Atomically mark an approved approval as consumed before side effects run."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ? AND status = ?",
            (approval_id, "approved"),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["data"])
        if data.get("consumed_at"):
            return None
        data["consumed_at"] = consumed_at
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (_json(data), approval_id, "approved"),
        )
        if cursor.rowcount != 1:
            return None
    return data


def expire_approval_if_pending(approval_id: str, expired_at: str, reason: str = "") -> dict[str, Any] | None:
    """Atomically expire one pending, unconsumed approval."""
    return expire_approval_if_unconsumed(approval_id, expired_at, reason, statuses={"pending"})


def expire_approval_if_unconsumed(
    approval_id: str,
    expired_at: str,
    reason: str = "",
    *,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically expire one unconsumed approval in an allowed status."""
    allowed_statuses = statuses or {"pending", "approved"}
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["data"])
        current_status = str(data.get("status") or "")
        if current_status not in allowed_statuses or data.get("consumed_at"):
            return None
        data["status"] = "expired"
        data["decided_at"] = expired_at
        if reason:
            data["expired_reason"] = reason
        placeholders = ",".join("?" for _ in allowed_statuses)
        cursor = conn.execute(
            f"""
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status IN ({placeholders})
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (_json(data), "expired", approval_id, *sorted(allowed_statuses)),
        )
        if cursor.rowcount != 1:
            return None
    return data


def expire_pending_approvals_for_task(task_id: str, expired_at: str, reason: str = "") -> list[dict[str, Any]]:
    """Atomically expire all pending, unconsumed approvals for a task."""
    expired: list[dict[str, Any]] = []
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, data FROM approvals WHERE task_id = ? AND status = ?",
            (task_id, "pending"),
        ).fetchall()
        for row in rows:
            data = json.loads(row["data"])
            if data.get("status") != "pending" or data.get("consumed_at"):
                continue
            data["status"] = "expired"
            data["decided_at"] = expired_at
            if reason:
                data["expired_reason"] = reason
            cursor = conn.execute(
                """
                UPDATE approvals
                SET data = ?,
                    status = ?
                WHERE id = ?
                  AND status = ?
                  AND json_extract(data, '$.status') = ?
                  AND json_extract(data, '$.consumed_at') IS NULL
                """,
                (_json(data), "expired", row["id"], "pending", "pending"),
            )
            if cursor.rowcount == 1:
                expired.append(data)
    return expired


def decide_approval_atomically(approval_id: str, status: str, decided_at: str) -> dict[str, Any] | None:
    """Atomically move a pending, unconsumed approval to a terminal decision."""
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported approval decision status: {status}")
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["data"])
        if data.get("status") != "pending" or data.get("consumed_at"):
            return None
        data["status"] = status
        data["decided_at"] = decided_at
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.status') = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (_json(data), status, approval_id, "pending", "pending"),
        )
        if cursor.rowcount != 1:
            return None
    return data


def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, _json(value), _now_iso()),
        )


def get_settings_overrides() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        result[row["key"]] = json.loads(row["value"])
    return result


def upsert_memory(payload: dict[str, Any]) -> None:
    """Custom helper for memories: persists embedding as JSON in data column."""
    record_id = str(payload.get("id") or "")
    content = str(payload.get("content", ""))
    kind = str(payload.get("kind", "fact"))
    tags = payload.get("tags") or []
    embedding = payload.get("embedding") or []
    body = {
        "id": record_id,
        "kind": kind,
        "content": content,
        "tags": list(tags),
        "task_id": payload.get("task_id", ""),
        "source": payload.get("source", "user"),
        "use_count": int(payload.get("use_count") or 0),
        "last_used_at": payload.get("last_used_at") or "",
        "embedding_dim": int(payload.get("embedding_dim") or len(embedding)),
        "created_at": payload.get("created_at") or _now_iso(),
        "embedding": list(embedding),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, kind, content, tags, task_id, embedding, data, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["id"],
                kind,
                content,
                ",".join(tags) if tags else "",
                body["task_id"],
                None,  # embedding column kept null; we store JSON list inside data instead.
                _json(body),
                body["created_at"],
                body["last_used_at"] or None,
            ),
        )


def list_memories(*, tags: list[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT data, tags FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        body = json.loads(row["data"])
        if tags:
            row_tags = set(str(row["tags"] or "").split(",")) - {""}
            wanted = set(tags)
            if not wanted.issubset(row_tags):
                continue
        results.append(body)
    return results


def delete_memory(memory_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cursor.rowcount > 0
