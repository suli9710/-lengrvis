from __future__ import annotations

import threading
from typing import Any

from pydantic import BaseModel, Field

from app.core import db
from app.core.schemas import new_id, now_iso


DEFAULT_SESSION_ID = "session_current"


class SessionContext(BaseModel):
    id: str = Field(default_factory=lambda: new_id("session"))
    parent_session_id: str = ""
    resumed_from_task_id: str = ""
    resumed_from_boundary_id: str = ""
    active_task_ids: list[str] = Field(default_factory=list)
    current_workflow_state: dict[str, Any] = Field(default_factory=dict)
    unfinished_task_ids: list[str] = Field(default_factory=list)
    learned_preferences: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    conversation_summary: str = ""
    last_summarized_message_id: str = ""
    token_stats: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def context_for_planning(self) -> dict[str, Any]:
        lineage = self.lineage_diagnostics()
        return {
            "session_id": self.id,
            "parent_session_id": self.parent_session_id,
            "resumed_from_task_id": self.resumed_from_task_id,
            "resumed_from_boundary_id": self.resumed_from_boundary_id,
            "active_task_ids": self.active_task_ids,
            "current_workflow_state": self.current_workflow_state,
            "unfinished_task_ids": self.unfinished_task_ids,
            "learned_preferences": self.learned_preferences,
            "notes": self.notes[-5:],
            "conversation_summary": self.conversation_summary,
            "last_summarized_message_id": self.last_summarized_message_id,
            "token_stats": self.token_stats,
            "lineage": lineage,
            "updated_at": self.updated_at,
        }

    def lineage_diagnostics(self) -> dict[str, Any]:
        return lineage_diagnostics_from_metadata(
            session_id=self.id,
            parent_session_id=self.parent_session_id,
            resumed_from_task_id=self.resumed_from_task_id,
            resumed_from_boundary_id=self.resumed_from_boundary_id,
            active_task_ids=self.active_task_ids,
            summary_anchor=self.last_summarized_message_id,
            token_stats=self.token_stats,
            summary=self.conversation_summary,
            updated_at=self.updated_at,
        )

    def matches_boundary_id(self, boundary_id: str) -> bool:
        wanted = _str(boundary_id)
        if not wanted:
            return False
        lineage = self.lineage_diagnostics()
        candidates = {
            _str(self.resumed_from_boundary_id),
            _str(lineage.get("latest_boundary_id")),
            _str(_compact_metadata(self.token_stats).get("logical_parent_id")),
        }
        return wanted in (candidate for candidate in candidates if candidate)


class SessionContextStore:
    def __init__(self, *, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.session_id = session_id
        self.current = SessionContext(id=session_id)
        self._lock = threading.RLock()
        db.init_db()

    def load(self, session_id: str | None = None) -> SessionContext:
        with self._lock:
            if session_id:
                self.session_id = session_id
            with db.connect() as conn:
                row = conn.execute("SELECT data FROM session_contexts WHERE id = ?", (self.session_id,)).fetchone()
            if row:
                self.current = SessionContext.model_validate_json(row["data"])
            else:
                self.current = SessionContext(id=self.session_id)
                self.save(self.current)
            return self.current

    def load_latest(self) -> SessionContext:
        return self.load()

    def load_global_latest(self) -> SessionContext:
        with self._lock:
            with db.connect() as conn:
                row = conn.execute("SELECT data FROM session_contexts ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row:
                self.current = SessionContext.model_validate_json(row["data"])
                self.session_id = self.current.id
            else:
                self.current = SessionContext(id=self.session_id)
                self.save(self.current)
            return self.current

    def load_by_boundary_id(self, boundary_id: str) -> SessionContext | None:
        wanted = _str(boundary_id)
        if not wanted:
            return None
        with self._lock:
            with db.connect() as conn:
                rows = conn.execute("SELECT data FROM session_contexts ORDER BY updated_at DESC").fetchall()
            for row in rows:
                context = SessionContext.model_validate_json(row["data"])
                if context.matches_boundary_id(wanted):
                    self.current = context
                    self.session_id = context.id
                    return context
            return None

    def save(self, context: SessionContext | None = None) -> SessionContext:
        with self._lock:
            target = context or self.current
            target.updated_at = now_iso()
            db.upsert_model("session_contexts", target)
            self.current = target
            return target

    def remember_task(self, task_id: str, *, workflow_state: dict[str, Any] | None = None) -> SessionContext:
        with self._lock:
            if task_id and task_id not in self.current.unfinished_task_ids:
                self.current.unfinished_task_ids.append(task_id)
            if task_id and task_id not in self.current.active_task_ids:
                self.current.active_task_ids.append(task_id)
            if workflow_state:
                self.current.current_workflow_state.update(workflow_state)
            return self.save()

    def complete_task(self, task_id: str) -> SessionContext:
        with self._lock:
            self.current.unfinished_task_ids = [item for item in self.current.unfinished_task_ids if item != task_id]
            return self.save()

    def learn_preference(self, key: str, value: Any) -> SessionContext:
        with self._lock:
            if key:
                self.current.learned_preferences[key] = value
            return self.save()

    def remember_summary(
        self,
        summary: str,
        *,
        last_message_id: str = "",
        token_stats: dict[str, Any] | None = None,
        resumed_from_task_id: str = "",
        resumed_from_boundary_id: str = "",
        parent_session_id: str = "",
    ) -> SessionContext:
        with self._lock:
            text = summary.strip()
            if text:
                self.current.conversation_summary = text
            if last_message_id:
                self.current.last_summarized_message_id = last_message_id
            if token_stats:
                self.current.token_stats.update(token_stats)
            if resumed_from_task_id:
                self.current.resumed_from_task_id = resumed_from_task_id
            if resumed_from_boundary_id:
                self.current.resumed_from_boundary_id = resumed_from_boundary_id
            if parent_session_id:
                self.current.parent_session_id = parent_session_id
            return self.save()

    def planning_context(self) -> dict[str, Any]:
        with self._lock:
            return self.current.context_for_planning()


_store: SessionContextStore | None = None


def get_session_context_store() -> SessionContextStore:
    global _store
    if _store is None:
        _store = SessionContextStore()
    return _store


def reset_session_context_store() -> None:
    global _store
    _store = None


def lineage_diagnostics_from_metadata(
    *,
    session_id: str = "",
    parent_session_id: str = "",
    resumed_from_task_id: str = "",
    resumed_from_boundary_id: str = "",
    active_task_ids: list[str] | None = None,
    summary_anchor: str = "",
    token_stats: dict[str, Any] | None = None,
    compact_metadata: dict[str, Any] | None = None,
    summary: str = "",
    updated_at: str = "",
) -> dict[str, Any]:
    stats = token_stats or {}
    metadata = dict(compact_metadata or _compact_metadata(stats))
    preserved_tail_ids = _preserved_tail_message_ids(metadata)
    latest_boundary_id = (
        _str(resumed_from_boundary_id)
        or _str(metadata.get("latest_boundary_id"))
        or _str(metadata.get("boundary_id"))
        or _str(metadata.get("logical_parent_id"))
    )
    return {
        "session_id": _str(session_id),
        "parent_session_id": _str(parent_session_id),
        "resumed_from_task_id": _str(resumed_from_task_id),
        "resumed_from_boundary_id": _str(resumed_from_boundary_id),
        "active_task_ids": list(active_task_ids or []),
        "summary_anchor": _str(summary_anchor),
        "summary_anchor_message_id": _str(summary_anchor),
        "latest_boundary_id": latest_boundary_id,
        "latest_boundary_count": 1 if latest_boundary_id else 0,
        "preserved_tail_message_count": _preserved_tail_count(metadata, preserved_tail_ids),
        "preserved_tail_message_ids": preserved_tail_ids,
        "summarized_message_count": _int_value(
            metadata.get("messages_summarized"),
            metadata.get("compacted_messages"),
            stats.get("summarized_messages") if isinstance(stats, dict) else None,
        ),
        "summary_chars": _int_value(metadata.get("summary_chars")) or len(str(summary or "")),
        "updated_at": _str(updated_at),
    }


def _compact_metadata(token_stats: dict[str, Any]) -> dict[str, Any]:
    compact_metadata = token_stats.get("compact_metadata") if isinstance(token_stats, dict) else {}
    return dict(compact_metadata) if isinstance(compact_metadata, dict) else {}


def _preserved_tail_message_ids(compact_metadata: dict[str, Any]) -> list[str]:
    raw_values: list[Any] = [
        compact_metadata.get("retained_tail_message_ids"),
        compact_metadata.get("messages_to_keep_ids"),
        compact_metadata.get("preserved_message_ids"),
        compact_metadata.get("preserved_segment_message_ids"),
    ]
    preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment")
    if isinstance(preserved, dict):
        raw_values.extend(
            [
                preserved.get("message_ids"),
                preserved.get("messages_to_keep_ids"),
                preserved.get("preserved_message_ids"),
            ]
        )
        messages = preserved.get("messages")
        if isinstance(messages, list):
            raw_values.append([message.get("id") for message in messages if isinstance(message, dict)])
    elif isinstance(preserved, list):
        raw_values.append([message.get("id") for message in preserved if isinstance(message, dict)])

    ids: list[str] = []
    for value in raw_values:
        for item in _as_list(value):
            message_id = _str(item)
            if message_id and message_id not in ids:
                ids.append(message_id)
    return ids


def _preserved_tail_count(compact_metadata: dict[str, Any], preserved_tail_ids: list[str]) -> int:
    explicit = _int_value(
        compact_metadata.get("retained_tail_messages"),
        compact_metadata.get("messages_kept"),
        compact_metadata.get("preserved_tail_message_count"),
    )
    if explicit:
        return explicit
    preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment")
    if isinstance(preserved, dict):
        messages = preserved.get("messages")
        if isinstance(messages, list):
            return len([message for message in messages if isinstance(message, dict)])
    elif isinstance(preserved, list):
        return len([message for message in preserved if isinstance(message, dict)])
    return len(preserved_tail_ids)


def _int_value(*values: Any) -> int:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _str(value: Any) -> str:
    return str(value or "").strip()
