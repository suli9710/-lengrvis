from __future__ import annotations

import json
import math
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.core import db
from app.core.audit import record
from app.core.schemas import Memory, MessageType, now_iso
from app.indexer.embedding_service import embed_texts


class MemoryAgent(BaseAgent):
    name = "MemoryAgent"
    domain_summary = "Long-term memory store. Embedding-backed recall over user-confirmed facts and preferences."
    prompt_file = "memory_agent.md"

    async def _embed(self, text: str) -> list[float]:
        try:
            vectors = await embed_texts([text])
            if vectors and isinstance(vectors[0], list):
                return [float(value) for value in vectors[0]]
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            record("memory.embed_failed", self.name, {"error": str(exc)})
        return []

    async def remember(
        self,
        content: str,
        *,
        task_id: str = "",
        kind: str = "fact",
        tags: list[str] | None = None,
        source: str = "user",
    ) -> Memory:
        memory = Memory(
            content=content.strip(),
            kind=kind,
            tags=tags or [],
            task_id=task_id,
            source=source,
            last_used_at=now_iso(),
        )
        vector = await self._embed(memory.content)
        memory.embedding_dim = len(vector)
        payload = memory.model_dump()
        payload["embedding"] = vector
        db.upsert_memory(payload)
        record(
            "memory.remembered",
            self.name,
            {"id": memory.id, "kind": kind, "tag_count": len(memory.tags)},
            task_id=task_id,
        )
        try:
            if task_id:
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Remembered: {memory.content[:120]}",
                    message_type=MessageType.OBSERVATION,
                    structured_payload={"memory_id": memory.id, "kind": kind, "tags": memory.tags},
                )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: bus failures should not block memory persistence.
            record("memory.bus_publish_failed", self.name, {"error": str(exc)}, task_id=task_id)
        return memory

    async def remember_lesson(
        self,
        lesson: dict[str, Any],
        *,
        task_id: str = "",
        tags: list[str] | None = None,
        source: str = "system",
    ) -> Memory:
        """Store a structured post-task lesson for future planning."""
        normalized = {
            "goal_pattern": str(lesson.get("goal_pattern") or "").strip(),
            "tool": str(lesson.get("tool") or "").strip(),
            "args_pattern": lesson.get("args_pattern") or {},
            "outcome": str(lesson.get("outcome") or "").strip(),
            "reason": str(lesson.get("reason") or "").strip(),
        }
        tool_tag = normalized["tool"] or "unknown_tool"
        content = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return await self.remember(
            content,
            task_id=task_id,
            kind="lesson",
            tags=["lesson", tool_tag, *(tags or [])],
            source=source,
        )

    async def recall(
        self,
        query: str,
        *,
        k: int = 5,
        tags: list[str] | None = None,
    ) -> list[Memory]:
        rows = db.list_memories(tags=tags, limit=500)
        if not rows:
            return []
        query_vector = await self._embed(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            vector = row.get("embedding") or []
            similarity = _cosine_similarity(query_vector, vector) + _lexical_overlap_score(
                query,
                str(row.get("content") or ""),
            )
            scored.append((similarity, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[Memory] = []
        for _similarity, row in scored[:k]:
            try:
                memory = Memory.model_validate(row)
            except ValidationError as exc:
                record("memory.recall_row_invalid", self.name, {"error": str(exc)})
                continue
            memory.use_count = int(row.get("use_count", 0)) + 1
            memory.last_used_at = now_iso()
            payload = memory.model_dump()
            payload["embedding"] = row.get("embedding") or []
            db.upsert_memory(payload)
            results.append(memory)
        return results

    def forget(self, memory_id: str) -> bool:
        ok = db.delete_memory(memory_id)
        if ok:
            record("memory.forgotten", self.name, {"id": memory_id})
        return ok

    def list_all(self, *, limit: int = 200) -> list[Memory]:
        rows = db.list_memories(limit=limit)
        result: list[Memory] = []
        for row in rows:
            try:
                result.append(Memory.model_validate(row))
            except ValidationError as exc:
                record("memory.list_row_invalid", self.name, {"error": str(exc)})
                continue
        return result


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(left * right for left, right in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _lexical_overlap_score(query: str, content: str) -> float:
    query_chars = {char for char in str(query or "").lower() if not char.isspace()}
    content_chars = {char for char in str(content or "").lower() if not char.isspace()}
    if not query_chars or not content_chars:
        return 0.0
    return 0.1 * (len(query_chars & content_chars) / len(query_chars))
