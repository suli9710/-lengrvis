from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.core import db


def upsert_tasks(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        """
        INSERT INTO tasks (id, data, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
        """,
        (data["id"], db._json(data), data.get("created_at", now), now),
    )


def upsert_chat_messages(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO chat_messages (id, data, created_at) VALUES (?, ?, ?)",
        (data["id"], db._json(data), data.get("created_at", now)),
    )


def upsert_plans(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        """
        INSERT INTO plans (id, task_id, data, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            data=excluded.data,
            task_id=excluded.task_id
        """,
        (data["id"], data["task_id"], db._json(data), data.get("created_at", now)),
    )


def upsert_goals(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
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
            db._json(data.get("related_task_ids") or data.get("task_ids") or []),
            db._json(data),
            data.get("created_at", now),
            now,
        ),
    )


def upsert_agent_messages(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO agent_messages (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
        (data["id"], data["task_id"], data.get("step_id"), db._json(data), data.get("created_at", now)),
    )


def upsert_runs(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
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
            db._json(data),
            data.get("created_at", now),
            data.get("updated_at", now),
        ),
    )


def upsert_safety_reviews(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO safety_reviews (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
        (data["id"], data["task_id"], data.get("step_id"), db._json(data), data.get("created_at", now)),
    )


def upsert_tool_calls(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        """
        INSERT INTO tool_calls (id, task_id, step_id, execution_key, status, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            task_id=excluded.task_id,
            step_id=excluded.step_id,
            execution_key=excluded.execution_key,
            status=excluded.status,
            data=excluded.data
        """,
        (
            data["id"],
            data["task_id"],
            data["step_id"],
            data.get("execution_key", ""),
            data.get("status", "created"),
            db._json(data),
            data.get("created_at", now),
        ),
    )


def upsert_tool_results(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
        (data["id"], data["tool_call_id"], db._json(data), data.get("created_at", now)),
    )


def upsert_approvals(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            data["id"],
            data["task_id"],
            data.get("step_id"),
            db._json(data),
            status or data.get("status", "pending"),
            data.get("created_at", now),
        ),
    )
    db._store_sensitive_record_integrity(conn, "approvals", data["id"], db._json(data))


def upsert_scheduled_tasks(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        """
        INSERT INTO scheduled_tasks (
            id, cron, goal, mode, enabled, next_run_at, last_run_at,
            data, created_at, updated_at
        )
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
            db._json(data),
            data.get("created_at", now),
            now,
        ),
    )


def upsert_wakeups(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
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
            db._json(data),
            data.get("created_at", now),
            now,
        ),
    )


def upsert_memories(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
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
            data.pop("embedding_blob", None)
            if isinstance(data.get("embedding_blob", None), bytes | bytearray)
            else None,
            db._json(data),
            data.get("created_at", now),
            data.get("last_used_at") or None,
        ),
    )


def upsert_session_contexts(conn: sqlite3.Connection, data: dict[str, Any], now: str, status: str | None) -> None:
    conn.execute(
        """
        INSERT INTO session_contexts (id, data, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
        """,
        (data["id"], db._json(data), data.get("created_at", now), now),
    )


UPSERT_HANDLERS: dict[str, Callable[[sqlite3.Connection, dict[str, Any], str, str | None], None]] = {
    "tasks": upsert_tasks,
    "chat_messages": upsert_chat_messages,
    "plans": upsert_plans,
    "goals": upsert_goals,
    "agent_messages": upsert_agent_messages,
    "runs": upsert_runs,
    "safety_reviews": upsert_safety_reviews,
    "tool_calls": upsert_tool_calls,
    "tool_results": upsert_tool_results,
    "approvals": upsert_approvals,
    "scheduled_tasks": upsert_scheduled_tasks,
    "wakeups": upsert_wakeups,
    "memories": upsert_memories,
    "session_contexts": upsert_session_contexts,
}


def upsert_model(table: str, model: BaseModel, *, task_id: str | None = None, status: str | None = None) -> None:
    data = json.loads(model.model_dump_json())
    now = data.get("updated_at") or data.get("created_at") or db._now_iso()
    if table == "audit_events":
        db._insert_audit_event_record(data)
        return
    if table == "run_events":
        db._insert_run_event_record(data)
        return
    handler = UPSERT_HANDLERS.get(table)
    if handler is None:
        raise ValueError(f"Unsupported table: {table}")
    with db.connect() as conn:
        if table in db.SENSITIVE_RECORD_INTEGRITY_KINDS:
            db._begin_immediate_transaction(conn)
        handler(conn, data, now, status)
