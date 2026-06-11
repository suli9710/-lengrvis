from __future__ import annotations

import hmac
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel

from app.config import get_base_settings, get_env


_DATA_DIR_OVERRIDE: ContextVar[str | None] = ContextVar("lengrvis_data_dir_override", default=None)
AUDIT_GENESIS_HASH = "0" * 64
AUDIT_HMAC_SECRET_FILE = "audit_hmac.secret"

# Audit hot-path caches (2-H2): avoid re-reading the HMAC secret file and
# re-querying the chain tail on every event. Single-writer assumption: failed
# inserts invalidate the chain head and fall back to a fresh DB query.
_AUDIT_CACHE_LOCK = threading.Lock()
_AUDIT_SECRET_CACHE: dict[str, str] = {}
_AUDIT_CHAIN_HEADS: dict[str, tuple[int, str]] = {}


def reset_audit_caches() -> None:
    with _AUDIT_CACHE_LOCK:
        _AUDIT_SECRET_CACHE.clear()
        _AUDIT_CHAIN_HEADS.clear()


DATA_TABLES = frozenset(
    {
        "approvals",
        "agent_messages",
        "audit_events",
        "chat_messages",
        "document_chunks",
        "goals",
        "indexed_files",
        "llm_usage_events",
        "memories",
        "mobile_devices",
        "mobile_pairings",
        "perception_observations",
        "perception_suggestions",
        "permission_policies",
        "plans",
        "run_events",
        "runs",
        "safety_reviews",
        "scheduled_tasks",
        "session_contexts",
        "task_recordings",
        "tasks",
        "tool_calls",
        "tool_results",
        "wakeups",
    }
)
UNSAFE_WHERE_TOKENS = (";", "--", "/*", "*/", "\x00")
WHERE_CONDITION_JOINER_RE = re.compile(r"\s+AND\s+", re.IGNORECASE)
WHERE_OR_RE = re.compile(r"\bOR\b", re.IGNORECASE)
WHERE_COMPARISON_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(=|>=|>|<=|<)\s*(\?|[0-9]+)$", re.IGNORECASE)
WHERE_IN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(\s*\?(?:\s*,\s*\?)*\s*\)$", re.IGNORECASE)
WHERE_NULL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(?:NOT\s+)?NULL$", re.IGNORECASE)
WHERE_ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "approvals": frozenset({"id", "task_id", "step_id", "status", "created_at"}),
    "agent_messages": frozenset({"id", "task_id", "step_id", "created_at"}),
    "audit_events": frozenset({"id", "task_id", "event_type", "actor", "sequence", "created_at"}),
    "chat_messages": frozenset({"id", "created_at"}),
    "document_chunks": frozenset({"id", "file_id", "chunk_index"}),
    "goals": frozenset({"id", "scope", "parent_goal_id", "status", "depth", "created_at", "updated_at"}),
    "indexed_files": frozenset({"id", "normalized_path", "sha256", "name", "extension", "size", "modified_at", "indexed_at"}),
    "llm_usage_events": frozenset({"id", "provider", "model", "mode", "task", "purpose", "created_at"}),
    "memories": frozenset({"id", "kind", "task_id", "created_at", "last_used_at"}),
    "mobile_devices": frozenset({"id", "created_at", "updated_at"}),
    "mobile_pairings": frozenset({"id", "status", "created_at", "expires_at", "used_at", "updated_at"}),
    "perception_observations": frozenset({"id", "task_id", "event_id", "event_type", "suppressed", "created_at"}),
    "perception_suggestions": frozenset({"id", "task_id", "suggestion_id", "status", "severity", "suppressed", "created_at"}),
    "permission_policies": frozenset({"id", "updated_at"}),
    "plans": frozenset({"id", "task_id", "created_at"}),
    "run_events": frozenset({"id", "run_id", "name", "sequence", "created_at"}),
    "runs": frozenset({"id", "task_id", "engine", "phase", "created_at", "updated_at"}),
    "safety_reviews": frozenset({"id", "task_id", "step_id", "created_at"}),
    "scheduled_tasks": frozenset({"id", "enabled", "next_run_at", "last_run_at", "created_at", "updated_at"}),
    "session_contexts": frozenset({"id", "created_at", "updated_at"}),
    "task_recordings": frozenset({"id", "task_id", "step_id", "phase", "captured_at", "created_at"}),
    "tasks": frozenset({"id", "created_at", "updated_at"}),
    "tool_calls": frozenset({"id", "task_id", "step_id", "created_at"}),
    "tool_results": frozenset({"id", "tool_call_id", "created_at"}),
    "wakeups": frozenset({"id", "source", "source_id", "status", "due_at", "created_at", "updated_at"}),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    override = _DATA_DIR_OVERRIDE.get()
    data_dir = Path(override or get_base_settings().data_dir)
    path = data_dir / "lengrvis.db"
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
    if table == "audit_events":
        _insert_audit_event_record(data)
        return
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
    table_name = _data_table_name(table)
    with connect() as conn:
        row = conn.execute(f"SELECT data FROM {table_name} WHERE id = ?", (record_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def fetch_many_by_fields(
    table: str,
    filters: dict[str, Any] | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    table_name = _data_table_name(table)
    clauses: list[str] = []
    args: list[Any] = []
    for column, value in dict(filters or {}).items():
        column_name = _where_column(table_name, column)
        if value is None:
            clauses.append(f"{column_name} IS NULL")
        else:
            clauses.append(f"{column_name} = ?")
            args.append(value)
    return _fetch_many_data(table_name, " AND ".join(clauses), tuple(args), limit)


def fetch_many_in(table: str, column: str, values: list[Any] | tuple[Any, ...], *, limit: int = 200) -> list[dict[str, Any]]:
    table_name = _data_table_name(table)
    column_name = _where_column(table_name, column)
    args = tuple(values)
    if not args:
        return []
    placeholders = ", ".join("?" for _ in args)
    return _fetch_many_data(table_name, f"{column_name} IN ({placeholders})", args, limit)


def fetch_many(table: str, where: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    """Compatibility fetch with a narrow WHERE grammar.

    Prefer ``fetch_many_by_fields`` and ``fetch_many_in`` for new code so SQL
    fragments do not spread beyond this module.
    """
    table_name = _data_table_name(table)
    where_clause = _where_clause(table_name, where, args)
    return _fetch_many_data(table_name, where_clause, args, limit)


def _fetch_many_data(table_name: str, where_clause: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    query = f"SELECT data FROM {table_name}"
    if where_clause:
        query += f" WHERE {where_clause}"
    query += " ORDER BY created_at DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(query, (*args, _query_limit(limit))).fetchall()
    return [json.loads(row["data"]) for row in rows]


def _data_table_name(table: str) -> str:
    table_name = str(table or "").strip()
    if table_name not in DATA_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    return table_name


def _where_clause(table_name: str, where: str, args: tuple[Any, ...]) -> str:
    clause = str(where or "").strip()
    if not clause:
        if args:
            raise ValueError("WHERE arguments require a WHERE clause")
        return ""
    if any(token in clause for token in UNSAFE_WHERE_TOKENS):
        raise ValueError("Unsafe WHERE clause")
    if WHERE_OR_RE.search(clause):
        raise ValueError("Unsupported WHERE clause")
    if clause.count("?") != len(args):
        raise ValueError("WHERE placeholder count does not match arguments")
    _validate_where_conditions(table_name, clause)
    return clause


def _validate_where_conditions(table_name: str, clause: str) -> None:
    allowed_columns = WHERE_ALLOWED_COLUMNS.get(table_name, frozenset())
    if not allowed_columns:
        raise ValueError(f"WHERE clauses are not supported for table: {table_name}")
    if not re.fullmatch(r"[A-Za-z0-9_?\s().,=<>!]+", clause):
        raise ValueError("Unsafe WHERE clause")

    parts = [part.strip() for part in WHERE_CONDITION_JOINER_RE.split(clause) if part.strip()]
    if not parts:
        raise ValueError("Unsafe WHERE clause")
    for part in parts:
        _validate_where_condition_part(table_name, allowed_columns, part)


def _validate_where_condition_part(table_name: str, allowed_columns: frozenset[str], part: str) -> None:
    match = WHERE_COMPARISON_RE.fullmatch(part) or WHERE_IN_RE.fullmatch(part) or WHERE_NULL_RE.fullmatch(part)
    if not match:
        raise ValueError("Unsupported WHERE clause")
    column = match.group(1)
    _where_column(table_name, column, allowed_columns=allowed_columns)


def _where_column(table_name: str, column: str, *, allowed_columns: frozenset[str] | None = None) -> str:
    column_name = str(column or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column_name):
        raise ValueError(f"Unsupported WHERE column for {table_name}: {column}")
    allowed = allowed_columns if allowed_columns is not None else WHERE_ALLOWED_COLUMNS.get(table_name, frozenset())
    if column_name not in allowed:
        raise ValueError(f"Unsupported WHERE column for {table_name}: {column_name}")
    return column_name


def _query_limit(limit: int) -> int:
    value = int(limit)
    if value < 1:
        raise ValueError("Limit must be positive")
    return value


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
    return _insert_audit_event_record(data)


def _insert_audit_event_record(data: dict[str, Any]) -> dict[str, Any]:
    try:
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
            # Update the in-memory head before commit releases the write lock so
            # concurrent writers cannot derive the same sequence from a stale tail.
            _store_audit_chain_head(stored["sequence"], stored["event_hash"])
    except Exception:
        _invalidate_audit_chain_head()
        raise
    return stored


def verify_audit_log(*, limit: int | None = None) -> dict[str, Any]:
    query = """
        SELECT id, task_id, event_type, actor, sequence, prev_hash, event_hash, hmac, data, created_at
        FROM audit_events
        WHERE sequence > 0
        ORDER BY sequence ASC, created_at ASC, id ASC
    """
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (max(1, int(limit)),)

    expected_prev = AUDIT_GENESIS_HASH
    checked = 0
    failures: list[dict[str, Any]] = []
    last_hash = expected_prev
    last_event_id: str | None = None
    last_sequence = 0
    with connect() as conn:
        rows = conn.execute(query, args).fetchall()

    for index, row in enumerate(rows, start=1):
        row_id = str(row["id"] or "")
        row_sequence = int(row["sequence"] or 0)
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            failures.append({"index": index, "id": row_id, "sequence": row_sequence, "reason": "invalid_json"})
            break

        sequence = int(data.get("sequence") or row_sequence or 0)
        prev_hash = str(data.get("prev_hash") or row["prev_hash"] or "")
        event_hash = str(data.get("event_hash") or row["event_hash"] or "")
        event_hmac = str(data.get("hmac") or row["hmac"] or "")

        column_mismatch = _audit_column_mismatch(row, data)
        if column_mismatch:
            failures.append(
                {
                    "index": index,
                    "id": row_id,
                    "sequence": sequence,
                    "reason": "stored_column_mismatch",
                    "fields": column_mismatch,
                }
            )
            break
        if sequence != index:
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "sequence_gap", "expected": index})
            break
        if prev_hash != expected_prev:
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "prev_hash_mismatch"})
            break

        unsigned = dict(data)
        unsigned["sequence"] = sequence
        unsigned["prev_hash"] = prev_hash
        unsigned["event_hash"] = ""
        unsigned["hmac"] = ""
        computed_hash = _audit_event_hash(unsigned)
        computed_hmac = _audit_event_hmac(computed_hash)
        if not hmac.compare_digest(event_hash, computed_hash):
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "event_hash_mismatch"})
            break
        if not hmac.compare_digest(event_hmac, computed_hmac):
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "hmac_mismatch"})
            break

        checked += 1
        last_hash = event_hash
        last_event_id = row_id
        last_sequence = sequence
        expected_prev = event_hash

    failure = failures[0] if failures else {}
    return {
        "ok": not failures,
        "checked": checked,
        "last_event_id": last_event_id,
        "last_sequence": last_sequence,
        "last_hash": last_hash,
        "failure_index": failure.get("index"),
        "failure_event_id": failure.get("id"),
        "failure_sequence": failure.get("sequence"),
        "failure_reason": str(failure.get("reason") or ""),
        "failures": failures,
    }


def _audit_column_mismatch(row: sqlite3.Row, data: dict[str, Any]) -> list[str]:
    mismatched: list[str] = []
    for field in ("id", "task_id", "event_type", "actor", "sequence", "prev_hash", "event_hash", "hmac", "created_at"):
        if field not in data:
            continue
        row_value = row[field]
        data_value = data.get(field)
        if field == "sequence":
            if int(data_value or 0) != int(row_value or 0):
                mismatched.append(field)
            continue
        if data_value is None and row_value is None:
            continue
        if str(data_value or "") != str(row_value or ""):
            mismatched.append(field)
    return mismatched


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
    with connect() as conn:
        for table in tables:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(row[0] or 0)
            conn.execute(f"DELETE FROM {table}")
    with connect() as conn:
        conn.execute("VACUUM")
    if include_settings:
        from app.llm.registry import invalidate_settings_cache

        invalidate_settings_cache()
    return counts


def local_product_diagnostics(*, recent_limit: int = 200) -> dict[str, Any]:
    limit = _query_limit(recent_limit)
    tasks = fetch_many("tasks", limit=limit)
    runs = fetch_many("runs", limit=limit)
    approvals = fetch_many("approvals", limit=limit)
    mobile_devices = fetch_many("mobile_devices", limit=limit)
    mobile_pairings = fetch_many("mobile_pairings", limit=limit)
    tool_results = fetch_many("tool_results", limit=limit)
    audits = fetch_many("audit_events", limit=limit)

    task_success_count = sum(1 for item in tasks if _is_success_state(item.get("status")) or _is_success_state(item.get("phase")))
    task_failure_count = sum(1 for item in tasks if _is_failed_state(item.get("status")) or _is_failed_state(item.get("phase")))
    run_success_count = sum(1 for item in runs if _is_success_state(item.get("phase")))
    run_failure_count = sum(1 for item in runs if _is_failed_state(item.get("phase")))
    tool_result_success_count = sum(1 for item in tool_results if item.get("ok") is True)
    tool_result_failure_count = sum(1 for item in tool_results if item.get("ok") is False)
    approval_status_counts = _status_counts(approvals, "status")
    mobile_device_status_counts = _status_counts(mobile_devices, "status", default="active")
    mobile_pairing_status_counts = _status_counts(mobile_pairings, "status")
    remote_input_grant_counts = _remote_input_grant_counts(mobile_devices)
    audit_failure_like_count = sum(
        1
        for item in audits
        if any(token in str(item.get("event_type") or "").casefold() for token in ("fail", "error"))
    )

    latest_audit_event = None
    if audits:
        latest = audits[0]
        latest_audit_event = {
            "id": latest.get("id"),
            "event_type": latest.get("event_type"),
            "sequence": latest.get("sequence"),
            "created_at": latest.get("created_at"),
        }

    product_metrics = {
        "schema_version": 1,
        "sample_size": limit,
        "paired_devices_count": int(mobile_device_status_counts.get("active", 0)),
        "active_remote_input_grants_count": int(remote_input_grant_counts.get("active", 0)),
        "paired_devices": {
            "total": len(mobile_devices),
            "active": int(mobile_device_status_counts.get("active", 0)),
            "revoked": int(mobile_device_status_counts.get("revoked", 0)),
        },
        "mobile_pairings": {
            "recent_total": len(mobile_pairings),
            "pending": int(mobile_pairing_status_counts.get("pending", 0)),
            "used": int(mobile_pairing_status_counts.get("used", 0)),
            "expired": int(mobile_pairing_status_counts.get("expired", 0)),
        },
        "remote_input_grants": remote_input_grant_counts,
        "tasks": {
            "recent_total": len(tasks),
            "recent_success": task_success_count,
            "recent_failure": task_failure_count,
            "by_status": _status_counts(tasks, "status", "phase"),
        },
        "runs": {
            "recent_total": len(runs),
            "recent_success": run_success_count,
            "recent_failure": run_failure_count,
            "by_phase": _status_counts(runs, "phase"),
        },
        "approvals": {
            "recent_total": len(approvals),
            "pending": int(approval_status_counts.get("pending", 0)),
            "approved": int(approval_status_counts.get("approved", 0)),
            "rejected": int(approval_status_counts.get("rejected", 0)),
            "expired": int(approval_status_counts.get("expired", 0)),
            "consumed": sum(1 for item in approvals if item.get("consumed_at")),
        },
        "tool_results": {
            "recent_total": len(tool_results),
            "recent_success": tool_result_success_count,
            "recent_failure": tool_result_failure_count,
        },
    }
    product_funnel = {
        "schema_version": 1,
        "first_launch": {
            "local_database_present": db_path().exists(),
            "audit_events_recent_count": len(audits),
            "latest_audit_event_type": latest_audit_event.get("event_type") if latest_audit_event else "",
        },
        "pairing": {
            "paired_devices_count": product_metrics["paired_devices_count"],
            "pairings_recent_used_count": product_metrics["mobile_pairings"]["used"],
            "pairings_recent_pending_count": product_metrics["mobile_pairings"]["pending"],
        },
        "remote_input": {
            "active_remote_input_grants_count": product_metrics["active_remote_input_grants_count"],
            "remote_input_grants_recent_total": remote_input_grant_counts["total"],
        },
        "first_task": {
            "tasks_recent_total": len(tasks),
            "tasks_recent_success_count": task_success_count,
            "tasks_recent_failure_count": task_failure_count,
            "runs_recent_total": len(runs),
            "runs_recent_success_count": run_success_count,
            "runs_recent_failure_count": run_failure_count,
        },
        "approval_response": {
            "approval_pending_count": product_metrics["approvals"]["pending"],
            "approval_approved_count": product_metrics["approvals"]["approved"],
            "approval_rejected_count": product_metrics["approvals"]["rejected"],
            "approval_expired_count": product_metrics["approvals"]["expired"],
        },
    }

    return {
        "sample_size": limit,
        "recent_counts": {
            "tasks": len(tasks),
            "runs": len(runs),
            "approvals": len(approvals),
            "mobile_devices": len(mobile_devices),
            "mobile_pairings": len(mobile_pairings),
            "tool_results": len(tool_results),
            "audit_events": len(audits),
        },
        "recent_success_counts": {
            "tasks_completed": task_success_count,
            "runs_completed": run_success_count,
            "tool_results_ok": tool_result_success_count,
        },
        "recent_failure_counts": {
            "tasks_failed": task_failure_count,
            "runs_failed": run_failure_count,
            "tool_results_failed": tool_result_failure_count,
            "audit_events_failure_like": audit_failure_like_count,
        },
        "product_metrics": product_metrics,
        "product_funnel": product_funnel,
        "latest_audit_event": latest_audit_event,
    }


def _status_counts(items: list[dict[str, Any]], *fields: str, default: str = "unknown") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = _first_text(item, *fields) or default
        key = status.casefold()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _remote_input_grant_counts(devices: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": 0, "active": 0, "expired": 0, "revoked": 0, "unknown": 0}
    now = datetime.now(timezone.utc)
    for device in devices:
        grants = device.get("remote_input_grants") or []
        if not isinstance(grants, list):
            continue
        for raw_grant in grants:
            if not isinstance(raw_grant, dict):
                continue
            counts["total"] += 1
            status = _remote_input_grant_status(raw_grant, now)
            counts[status] = counts.get(status, 0) + 1
    return counts


def _remote_input_grant_status(grant: dict[str, Any], now: datetime) -> str:
    status = _first_text(grant, "status") or "active"
    if status == "active":
        expires_at = _parse_iso_datetime(grant.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            return "expired"
    if status in {"active", "expired", "revoked"}:
        return status
    return "unknown"


def _first_text(item: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = item.get(field)
        text = str(getattr(value, "value", value) or "").strip()
        if text:
            return text
    return ""


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_success_state(value: Any) -> bool:
    return str(getattr(value, "value", value) or "").casefold() in {"completed", "complete", "success", "succeeded", "done"}


def _is_failed_state(value: Any) -> bool:
    return str(getattr(value, "value", value) or "").casefold() in {"failed", "failure", "error"}


def _prepare_audit_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    stored = dict(data)
    stored.setdefault("id", f"audit_{uuid4().hex}")
    stored["created_at"] = stored.get("created_at") or _now_iso()

    key = str(db_path())
    with _AUDIT_CACHE_LOCK:
        head = _AUDIT_CHAIN_HEADS.get(key)
    if head is None:
        row = conn.execute(
            "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC, created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        sequence = int(row["sequence"] or 0) + 1 if row else 1
        prev_hash = str(row["event_hash"] or "") if row else AUDIT_GENESIS_HASH
    else:
        sequence = head[0] + 1
        prev_hash = head[1]

    stored["sequence"] = sequence
    stored["prev_hash"] = prev_hash
    stored["event_hash"] = ""
    stored["hmac"] = ""
    event_hash = _audit_event_hash(stored)
    stored["event_hash"] = event_hash
    stored["hmac"] = _audit_event_hmac(event_hash)
    return stored


def _store_audit_chain_head(sequence: int, event_hash: str) -> None:
    with _AUDIT_CACHE_LOCK:
        _AUDIT_CHAIN_HEADS[str(db_path())] = (int(sequence), str(event_hash))


def _invalidate_audit_chain_head() -> None:
    with _AUDIT_CACHE_LOCK:
        _AUDIT_CHAIN_HEADS.pop(str(db_path()), None)


def _audit_event_hash(event: dict[str, Any]) -> str:
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _audit_event_hmac(event_hash: str) -> str:
    return hmac.new(_audit_hmac_secret().encode("utf-8"), event_hash.encode("utf-8"), sha256).hexdigest()


def _audit_hmac_secret() -> str:
    from app.security.local_secret import load_or_create_local_secret

    configured = str(get_env("LENGRVIS_AUDIT_HMAC_SECRET") or "").strip()
    if configured:
        return configured

    secret_path = db_path().parent / AUDIT_HMAC_SECRET_FILE
    key = str(secret_path)
    with _AUDIT_CACHE_LOCK:
        cached = _AUDIT_SECRET_CACHE.get(key)
    if cached:
        return cached

    secret = load_or_create_local_secret(
        secret_path,
        unavailable_message="Audit HMAC secret is unavailable.",
    )
    with _AUDIT_CACHE_LOCK:
        _AUDIT_SECRET_CACHE[key] = secret
    return secret


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
    from app.llm.registry import invalidate_settings_cache

    invalidate_settings_cache()


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
