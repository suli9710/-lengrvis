from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.core import db
from app.core.audit import record
from app.core.content_provenance import (
    coerce_content_envelope,
    content_envelope_integrity_valid,
    create_content_envelope,
    propagate_content_envelope,
    revalidate_content_envelope,
    stable_content_hash,
)
from app.core.memory_namespace import (
    DEFAULT_MEMORY_DOMAIN_SCOPE,
    DEFAULT_MEMORY_PRINCIPAL_ID,
    DEFAULT_MEMORY_WORKSPACE_ID,
    MemoryNamespace,
    normalize_memory_namespace,
)
from app.core.schemas import (
    ContentEnvelope,
    Memory,
    MemoryConflictStatus,
    MemoryState,
    MessageType,
    now_iso,
)
from app.indexer.embedding_service import embed_texts


class MemoryAgent(BaseAgent):
    name = "MemoryAgent"
    domain_summary = "Long-term memory store. Embedding-backed recall over user-confirmed facts and preferences."
    prompt_file = "memory_agent.md"

    def __init__(
        self,
        bus=None,
        *,
        principal_id: str = DEFAULT_MEMORY_PRINCIPAL_ID,
        workspace_id: str = DEFAULT_MEMORY_WORKSPACE_ID,
        domain_scope: str = DEFAULT_MEMORY_DOMAIN_SCOPE,
    ) -> None:
        super().__init__(bus)
        self.namespace = normalize_memory_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )

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
        version: int | None = None,
        supersedes: str = "",
        conflict_status: MemoryConflictStatus | str = MemoryConflictStatus.NONE,
        tags: list[str] | None = None,
        source: str = "user",
        user_confirmed: bool | None = None,
        quarantine: bool | None = None,
        ttl_seconds: int | None = None,
        content_envelope: ContentEnvelope | dict[str, Any] | None = None,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
    ) -> Memory:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        normalized_kind = str(kind or "fact").strip() or "fact"
        normalized_supersedes = str(supersedes or "").strip()
        effective_version = 1 if version is None else int(version)
        if normalized_supersedes:
            parent_row = db.get_memory(
                normalized_supersedes,
                principal_id=namespace.principal_id,
                workspace_id=namespace.workspace_id,
                domain_scope=namespace.domain_scope,
            )
            if parent_row is None:
                raise ValueError("superseded memory was not found in the current namespace")
            parent = self._memory_from_row(parent_row)
            if parent.kind != normalized_kind:
                raise ValueError("a memory can only supersede a record of the same kind")
            effective_version = parent.version + 1 if version is None else int(version)
            if effective_version <= parent.version:
                raise ValueError("a superseding memory must have a higher version")
        confirmed = source.strip().casefold() == "user" if user_confirmed is None else bool(user_confirmed)
        quarantined = not confirmed if quarantine is None else bool(quarantine)
        state = MemoryState.QUARANTINED if quarantined else MemoryState.ACTIVE
        expires_at = ""
        if ttl_seconds is not None:
            expires_at = (datetime.now(UTC) + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()
        normalized_content = content.strip()
        memory = Memory(
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
            content=normalized_content,
            kind=normalized_kind,
            version=effective_version,
            supersedes=normalized_supersedes,
            conflict_status=conflict_status,
            tags=tags or [],
            task_id=task_id,
            source=source,
            state=state,
            user_confirmed=confirmed,
            expires_at=expires_at,
            last_used_at=now_iso(),
        )
        memory.content_envelope = self._memory_envelope(
            normalized_content,
            task_id=task_id or memory.id,
            source=source,
            user_confirmed=confirmed,
            quarantined=quarantined,
            content_envelope=content_envelope,
        )
        vector = await self._embed(memory.content)
        memory.embedding_dim = len(vector)
        payload = memory.model_dump()
        payload["embedding"] = vector
        db.upsert_memory(payload)
        record(
            "memory.remembered",
            self.name,
            {
                "id": memory.id,
                "kind": memory.kind,
                "state": memory.state.value,
                "principal_id": memory.principal_id,
                "workspace_id": memory.workspace_id,
                "domain_scope": memory.domain_scope,
                "version": memory.version,
                "supersedes": memory.supersedes,
                "conflict_status": memory.conflict_status.value,
                "tag_count": len(memory.tags),
            },
            task_id=task_id,
        )
        try:
            if task_id:
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Remembered: {memory.content[:120]}",
                    message_type=MessageType.OBSERVATION,
                    structured_payload={
                        "memory_id": memory.id,
                        "kind": memory.kind,
                        "state": memory.state.value,
                        "principal_id": memory.principal_id,
                        "workspace_id": memory.workspace_id,
                        "domain_scope": memory.domain_scope,
                        "tags": memory.tags,
                    },
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
        user_confirmed: bool = False,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
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
            user_confirmed=user_confirmed,
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )

    async def recall(
        self,
        query: str,
        *,
        k: int = 5,
        tags: list[str] | None = None,
        kind: str | None = None,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
    ) -> list[Memory]:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        rows = db.list_memories(
            tags=tags,
            kind=kind,
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
            limit=500,
        )
        active_rows: list[tuple[Memory, dict[str, Any]]] = []
        for row in rows:
            try:
                memory = self._memory_from_row(row)
            except ValidationError as exc:
                record("memory.recall_row_invalid", self.name, {"error": str(exc)})
                continue
            if not self._is_recallable(memory):
                continue
            integrity_error = self._recall_integrity_error(memory)
            if integrity_error:
                self._quarantine_recall_failure(memory, row, integrity_error)
                continue
            active_rows.append((memory, row))
        if not active_rows:
            return []
        query_vector = await self._embed(query)
        scored: list[tuple[float, Memory, dict[str, Any]]] = []
        for memory, row in active_rows:
            vector = row.get("embedding") or []
            similarity = _cosine_similarity(query_vector, vector) + _lexical_overlap_score(
                query,
                memory.content,
            )
            scored.append((similarity, memory, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[Memory] = []
        for _similarity, memory, row in scored[:k]:
            memory.use_count = int(row.get("use_count", 0)) + 1
            memory.last_used_at = now_iso()
            payload = memory.model_dump()
            payload["embedding"] = row.get("embedding") or []
            db.upsert_memory(payload)
            results.append(memory)
        return results

    def promote(
        self,
        memory_id: str,
        *,
        reviewed_by: str = "user",
        conflict_status: MemoryConflictStatus | str | None = None,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
    ) -> Memory | None:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        row = db.get_memory(
            memory_id,
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
        )
        if row is None:
            return None
        try:
            memory = self._memory_from_row(row)
        except ValidationError as exc:
            record("memory.promote_row_invalid", self.name, {"id": memory_id, "error": str(exc)})
            return None
        memory.state = MemoryState.ACTIVE
        memory.user_confirmed = True
        memory.reviewed_at = now_iso()
        memory.reviewed_by = str(reviewed_by or "user")
        if conflict_status is not None:
            raw_conflict_status = getattr(conflict_status, "value", conflict_status)
            memory.conflict_status = MemoryConflictStatus(str(raw_conflict_status))
        if memory.content_envelope is not None:
            try:
                memory.content_envelope = revalidate_content_envelope(
                    memory.content_envelope,
                    memory.content,
                    task_scope=memory.task_id or memory.id,
                )
            except ValueError:
                memory.content_envelope = create_content_envelope(
                    memory.content,
                    source_kind="memory_revalidation",
                    source_id=memory.id,
                    origin=memory.source,
                    trust_level="user_confirmed",
                    task_scope=memory.task_id or memory.id,
                    user_confirmed=True,
                    sanitizers_applied=["user_revalidated"],
                )
        else:
            user_source = memory.source.strip().casefold() == "user"
            memory.content_envelope = create_content_envelope(
                memory.content,
                source_kind="user_input" if user_source else "agent_message",
                source_id=memory.task_id,
                origin=memory.source,
                trust_level="user_confirmed" if user_source else "unknown",
                taint_flags=[] if user_source else ["derived_content", "unreviewed_memory"],
                task_scope=memory.task_id or memory.id,
                user_confirmed=True,
            )
        payload = memory.model_dump()
        payload["embedding"] = row.get("embedding") or []
        db.upsert_memory(payload)
        record("memory.promoted", self.name, {"id": memory.id, "reviewed_by": memory.reviewed_by})
        return memory

    def revoke(
        self,
        memory_id: str,
        *,
        reviewed_by: str = "user",
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
    ) -> Memory | None:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        row = db.get_memory(
            memory_id,
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
        )
        if row is None:
            return None
        try:
            memory = self._memory_from_row(row)
        except ValidationError as exc:
            record("memory.revoke_row_invalid", self.name, {"id": memory_id, "error": str(exc)})
            return None
        memory.state = MemoryState.REVOKED
        memory.reviewed_at = now_iso()
        memory.reviewed_by = str(reviewed_by or "user")
        payload = memory.model_dump()
        payload["embedding"] = row.get("embedding") or []
        db.upsert_memory(payload)
        record("memory.revoked", self.name, {"id": memory.id, "reviewed_by": memory.reviewed_by})
        return memory

    def forget(
        self,
        memory_id: str,
        *,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
    ) -> bool:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        ok = db.delete_memory(
            memory_id,
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
        )
        if ok:
            record("memory.forgotten", self.name, {"id": memory_id})
        return ok

    def list_all(
        self,
        *,
        kind: str | None = None,
        principal_id: str | None = None,
        workspace_id: str | None = None,
        domain_scope: str | None = None,
        limit: int = 200,
    ) -> list[Memory]:
        namespace = self._resolve_namespace(
            principal_id=principal_id,
            workspace_id=workspace_id,
            domain_scope=domain_scope,
        )
        rows = db.list_memories(
            kind=kind,
            principal_id=namespace.principal_id,
            workspace_id=namespace.workspace_id,
            domain_scope=namespace.domain_scope,
            limit=limit,
        )
        result: list[Memory] = []
        for row in rows:
            try:
                result.append(self._memory_from_row(row))
            except ValidationError as exc:
                record("memory.list_row_invalid", self.name, {"error": str(exc)})
                continue
        return result

    def _memory_envelope(
        self,
        content: str,
        *,
        task_id: str,
        source: str,
        user_confirmed: bool,
        quarantined: bool,
        content_envelope: ContentEnvelope | dict[str, Any] | None,
    ) -> ContentEnvelope:
        if content_envelope is None:
            return create_content_envelope(
                content,
                source_kind="user_input" if user_confirmed else "agent_message",
                source_id=task_id,
                origin=source,
                trust_level="user_confirmed" if user_confirmed else "unknown",
                taint_flags=["derived_content", "unreviewed_memory"] if quarantined else [],
                task_scope=task_id,
                user_confirmed=user_confirmed,
            )
        envelope = propagate_content_envelope(
            coerce_content_envelope(content_envelope),
            content,
            user_confirmed=user_confirmed,
            taint_flags=["unreviewed_memory"] if quarantined else None,
        )
        return envelope

    def _is_recallable(self, memory: Memory) -> bool:
        if memory.state != MemoryState.ACTIVE:
            return False
        if memory.conflict_status not in {MemoryConflictStatus.NONE, MemoryConflictStatus.RESOLVED}:
            return False
        if not memory.expires_at:
            return True
        try:
            expires_at = datetime.fromisoformat(memory.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

    def _recall_integrity_error(self, memory: Memory) -> str:
        envelope = memory.content_envelope
        if envelope is None:
            return "content envelope is missing"
        if not content_envelope_integrity_valid(envelope):
            return "content envelope authentication failed"
        if envelope.content_hash != stable_content_hash(memory.content):
            return "content hash does not match stored memory"
        if not memory.user_confirmed or not envelope.user_confirmed:
            return "memory is not user confirmed"
        if envelope.trust_level not in {"user_confirmed", "trusted"}:
            return "content envelope trust is insufficient"
        expected_scope = memory.task_id or memory.id
        if envelope.task_scope != expected_scope:
            return "content envelope scope does not match memory"
        return ""

    def _quarantine_recall_failure(
        self,
        memory: Memory,
        row: dict[str, Any],
        reason: str,
    ) -> None:
        memory.state = MemoryState.QUARANTINED
        memory.user_confirmed = False
        payload = memory.model_dump()
        payload["embedding"] = row.get("embedding") or []
        db.upsert_memory(payload)
        record(
            "memory.recall_integrity_failed",
            self.name,
            {"id": memory.id, "reason": reason},
            task_id=memory.task_id,
        )

    def _memory_from_row(self, row: dict[str, Any]) -> Memory:
        payload = dict(row)
        if "state" not in payload:
            legacy_user_memory = str(payload.get("source") or "user").strip().casefold() == "user"
            payload["state"] = MemoryState.ACTIVE if legacy_user_memory else MemoryState.QUARANTINED
            payload["user_confirmed"] = legacy_user_memory
        return Memory.model_validate(payload)

    def _resolve_namespace(
        self,
        *,
        principal_id: str | None,
        workspace_id: str | None,
        domain_scope: str | None,
    ) -> MemoryNamespace:
        return normalize_memory_namespace(
            principal_id=self.namespace.principal_id if principal_id is None else principal_id,
            workspace_id=self.namespace.workspace_id if workspace_id is None else workspace_id,
            domain_scope=self.namespace.domain_scope if domain_scope is None else domain_scope,
        )


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
