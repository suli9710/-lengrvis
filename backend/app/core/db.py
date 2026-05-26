from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel

from app.config import get_base_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    settings = get_base_settings()
    path = Path(settings.data_dir) / "marvis.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
            """
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(file_id, path, text)"
            )
        except sqlite3.OperationalError:
            # Some Python builds may not ship FTS5. The search service falls back to LIKE.
            pass


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
            conn.execute(
                "INSERT OR REPLACE INTO audit_events (id, task_id, event_type, actor, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["id"],
                    data.get("task_id"),
                    data["event_type"],
                    data["actor"],
                    _json(data),
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


def fetch_many(table: str, where: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    query = f"SELECT data FROM {table}"
    if where:
        query += f" WHERE {where}"
    query += " ORDER BY created_at DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(query, (*args, limit)).fetchall()
    return [json.loads(row["data"]) for row in rows]


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
