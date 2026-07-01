from __future__ import annotations

import json
from typing import Any

from app.core import db


def upsert_memory(payload: dict[str, Any]) -> None:
    """Persist memory embedding as JSON in the data column."""
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
        "created_at": payload.get("created_at") or db._now_iso(),
        "embedding": list(embedding),
    }
    with db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO memories (
                id, kind, content, tags, task_id, embedding, data, created_at, last_used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["id"],
                kind,
                content,
                ",".join(tags) if tags else "",
                body["task_id"],
                None,
                db._json(body),
                body["created_at"],
                body["last_used_at"] or None,
            ),
        )


def list_memories(*, tags: list[str] | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with db.connect() as conn:
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
    with db.connect() as conn:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cursor.rowcount > 0
