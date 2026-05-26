from __future__ import annotations

import threading
from typing import Any

from pydantic import BaseModel, Field

from app.core import db
from app.core.schemas import new_id, now_iso


DEFAULT_SESSION_ID = "session_current"


class SessionContext(BaseModel):
    id: str = Field(default_factory=lambda: new_id("session"))
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
        return {
            "current_workflow_state": self.current_workflow_state,
            "unfinished_task_ids": self.unfinished_task_ids,
            "learned_preferences": self.learned_preferences,
            "notes": self.notes[-5:],
            "conversation_summary": self.conversation_summary,
            "last_summarized_message_id": self.last_summarized_message_id,
            "token_stats": self.token_stats,
            "updated_at": self.updated_at,
        }


class SessionContextStore:
    def __init__(self, *, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.session_id = session_id
        self.current = SessionContext(id=session_id)
        self._lock = threading.RLock()
        db.init_db()

    def load_latest(self) -> SessionContext:
        with self._lock:
            with db.connect() as conn:
                row = conn.execute("SELECT data FROM session_contexts ORDER BY updated_at DESC LIMIT 1").fetchone()
            if row:
                self.current = SessionContext.model_validate_json(row["data"])
            else:
                self.current = SessionContext(id=self.session_id)
                self.save(self.current)
            return self.current

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
    ) -> SessionContext:
        with self._lock:
            text = summary.strip()
            if text:
                self.current.conversation_summary = text
            if last_message_id:
                self.current.last_summarized_message_id = last_message_id
            if token_stats:
                self.current.token_stats.update(token_stats)
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
