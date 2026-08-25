from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.core import db
from app.core.schemas import ContentEnvelope, new_id, now_iso

DEFAULT_SESSION_ID = "session_current"
_SESSION_CONTEXT_WRITE_RETRIES = 3


class SessionSummaryConflictError(RuntimeError):
    """Raised when a summary write was derived from a stale session snapshot."""


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
    conversation_summary_envelope: ContentEnvelope | None = None
    token_stats: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def context_for_planning(self) -> dict[str, Any]:
        self.ensure_summary_provenance()
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
            "token_stats": self.public_token_stats(),
            "lineage": lineage,
            "updated_at": self.updated_at,
        }

    def lineage_diagnostics(self) -> dict[str, Any]:
        envelope = self.ensure_summary_provenance()
        diagnostics = lineage_diagnostics_from_metadata(
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
        if envelope is None:
            diagnostics.update(
                {
                    "summary_provenance_status": "none",
                    "summary_anchor_authenticated": False,
                    "summary_field_mapping_count": 0,
                    "summary_source_message_count": 0,
                    "summary_message_id_digests": [],
                }
            )
            return diagnostics
        from app.context.summary_provenance import summary_provenance_diagnostics

        diagnostics.update(summary_provenance_diagnostics(envelope))
        diagnostics["summary_anchor_authenticated"] = bool(self.last_summarized_message_id)
        return diagnostics

    def ensure_summary_provenance(self) -> ContentEnvelope | None:
        """Hydrate legacy summaries conservatively and reject altered provenance."""

        from app.context.summary_provenance import (
            LEGACY_SUMMARY_SOURCE_KIND,
            SUMMARY_ENVELOPE_TOKEN_KEY,
            SUMMARY_PROVENANCE_VERSION,
            SummaryProvenanceError,
            create_legacy_summary_content_envelope,
            validate_summary_content_envelope,
        )

        summary = str(self.conversation_summary or "").strip()
        sidecar = self.token_stats.get(SUMMARY_ENVELOPE_TOKEN_KEY)
        provenance_version = str(self.token_stats.get("summary_provenance_version") or "").strip()
        if not summary:
            if self.conversation_summary_envelope is not None or sidecar is not None or provenance_version:
                raise ValueError("summary provenance exists without summary content")
            return None
        envelope = self.conversation_summary_envelope
        if sidecar is not None:
            try:
                sidecar_envelope = ContentEnvelope.model_validate(sidecar)
            except ValueError as exc:
                raise SummaryProvenanceError("summary content envelope sidecar is malformed") from exc
            if envelope is not None and envelope != sidecar_envelope:
                raise SummaryProvenanceError("summary content envelope conflicts with its compatibility sidecar")
            envelope = sidecar_envelope
        if envelope is None:
            if provenance_version:
                raise ValueError("summary content envelope is missing after provenance migration")
            envelope = create_legacy_summary_content_envelope(
                summary,
                session_id=self.id,
                last_message_id=self.last_summarized_message_id,
            )
            self.conversation_summary_envelope = envelope
        validated = validate_summary_content_envelope(
            summary,
            envelope,
            session_id=self.id,
            last_message_id=self.last_summarized_message_id,
        )
        if provenance_version and provenance_version != SUMMARY_PROVENANCE_VERSION:
            raise ValueError("summary provenance version is unsupported")
        if validated.source_kind == LEGACY_SUMMARY_SOURCE_KIND and provenance_version:
            raise ValueError("summary provenance cannot downgrade to a legacy root")
        if validated.source_kind != LEGACY_SUMMARY_SOURCE_KIND:
            self.token_stats["summary_provenance_version"] = SUMMARY_PROVENANCE_VERSION
        source_message_ids = _summary_source_message_ids(self.token_stats)
        if validated.source_kind != LEGACY_SUMMARY_SOURCE_KIND and source_message_ids is not None:
            validated = validate_summary_content_envelope(
                summary,
                validated,
                session_id=self.id,
                last_message_id=self.last_summarized_message_id,
                source_message_ids=source_message_ids,
            )
        self.conversation_summary_envelope = validated
        self.token_stats[SUMMARY_ENVELOPE_TOKEN_KEY] = validated.model_dump(mode="json")
        return validated

    def public_token_stats(self) -> dict[str, Any]:
        from app.context.agent_message_projection import strip_private_provenance
        from app.context.summary_provenance import SUMMARY_ENVELOPE_TOKEN_KEY

        public_stats = {key: value for key, value in self.token_stats.items() if key != SUMMARY_ENVELOPE_TOKEN_KEY}
        return strip_private_provenance(public_stats)

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
        self._loaded = False
        db.init_db()

    def load(self, session_id: str | None = None) -> SessionContext:
        with self._lock:
            if session_id:
                self.session_id = session_id
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT data, updated_at FROM session_contexts WHERE id = ?",
                    (self.session_id,),
                ).fetchone()
            if row:
                self.current = SessionContext.model_validate_json(row["data"])
                self.current.updated_at = str(row["updated_at"] or self.current.updated_at)
                self.current.ensure_summary_provenance()
            else:
                self.current = SessionContext(id=self.session_id)
                try:
                    self.save(self.current, expected_updated_at="")
                except SessionSummaryConflictError:
                    return self.load(self.session_id)
            self._loaded = True
            return self.current

    def load_latest(self) -> SessionContext:
        return self.load()

    def load_global_latest(self) -> SessionContext:
        with self._lock:
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT data, updated_at FROM session_contexts ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            if row:
                self.current = SessionContext.model_validate_json(row["data"])
                self.current.updated_at = str(row["updated_at"] or self.current.updated_at)
                self.current.ensure_summary_provenance()
                self.session_id = self.current.id
            else:
                self.current = SessionContext(id=self.session_id)
                try:
                    self.save(self.current, expected_updated_at="")
                except SessionSummaryConflictError:
                    return self.load_global_latest()
            self._loaded = True
            return self.current

    def load_by_boundary_id(self, boundary_id: str) -> SessionContext | None:
        wanted = _str(boundary_id)
        if not wanted:
            return None
        with self._lock:
            with db.connect() as conn:
                rows = conn.execute("SELECT data, updated_at FROM session_contexts ORDER BY updated_at DESC").fetchall()
            for row in rows:
                context = SessionContext.model_validate_json(row["data"])
                context.updated_at = str(row["updated_at"] or context.updated_at)
                context.ensure_summary_provenance()
                if context.matches_boundary_id(wanted):
                    self.current = context
                    self.session_id = context.id
                    self._loaded = True
                    return context
            return None

    def save(
        self,
        context: SessionContext | None = None,
        *,
        expected_updated_at: str | None = None,
    ) -> SessionContext:
        with self._lock:
            target = context or self.current
            target.ensure_summary_provenance()
            previous_updated_at = target.updated_at
            target.updated_at = _next_updated_at(previous_updated_at)
            try:
                with db.connect() as conn:
                    if not conn.in_transaction:
                        conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT updated_at FROM session_contexts WHERE id = ?",
                        (target.id,),
                    ).fetchone()
                    actual_updated_at = str(row["updated_at"] or "") if row else ""
                    wanted_updated_at = (
                        str(expected_updated_at)
                        if expected_updated_at is not None
                        else (str(previous_updated_at) if row else "")
                    )
                    if actual_updated_at != wanted_updated_at:
                        raise SessionSummaryConflictError("session context changed after its inputs were read")
                    db.upsert_model("session_contexts", target)
            except Exception:  # noqa: BLE001 - broad-exception-boundary: restore timestamp before re-raising save failures.
                target.updated_at = previous_updated_at
                raise
            self.current = target
            self._loaded = True
            return target

    def remember_task(self, task_id: str, *, workflow_state: dict[str, Any] | None = None) -> SessionContext:
        with self._lock:

            def mutate(candidate: SessionContext) -> None:
                if task_id and task_id not in candidate.unfinished_task_ids:
                    candidate.unfinished_task_ids.append(task_id)
                if task_id and task_id not in candidate.active_task_ids:
                    candidate.active_task_ids.append(task_id)
                if workflow_state:
                    candidate.current_workflow_state.update(workflow_state)

            return self._mutate_latest(mutate)

    def complete_task(self, task_id: str) -> SessionContext:
        with self._lock:

            def mutate(candidate: SessionContext) -> None:
                candidate.unfinished_task_ids = [item for item in candidate.unfinished_task_ids if item != task_id]

            return self._mutate_latest(mutate)

    def learn_preference(self, key: str, value: Any) -> SessionContext:
        with self._lock:

            def mutate(candidate: SessionContext) -> None:
                if key:
                    candidate.learned_preferences[key] = value

            return self._mutate_latest(mutate)

    def remember_summary(
        self,
        summary: str,
        *,
        last_message_id: str = "",
        summary_envelope: ContentEnvelope | dict[str, Any] | None = None,
        expected_updated_at: str | None = None,
        token_stats: dict[str, Any] | None = None,
        resumed_from_task_id: str = "",
        resumed_from_boundary_id: str = "",
        parent_session_id: str = "",
    ) -> SessionContext:
        with self._lock:
            if not self._loaded:
                self.load(self.session_id)
            if expected_updated_at is None:
                expected_updated_at = self.current.updated_at
            if expected_updated_at is not None and self.current.updated_at != expected_updated_at:
                raise SessionSummaryConflictError("session summary changed after provenance inputs were read")
            candidate = self.current.model_copy(deep=True)
            text = summary.strip()
            if text:
                from app.context.summary_provenance import (
                    SUMMARY_ENVELOPE_TOKEN_KEY,
                    SUMMARY_PROVENANCE_VERSION,
                    SUMMARY_SOURCE_KIND,
                    create_legacy_summary_content_envelope,
                    validate_summary_content_envelope,
                )

                effective_anchor = str(last_message_id or candidate.last_summarized_message_id or "").strip()
                current_version = str(candidate.token_stats.get("summary_provenance_version") or "").strip()
                if summary_envelope is None and current_version:
                    raise ValueError("authenticated summary provenance cannot be replaced without an envelope")
                candidate_envelope = summary_envelope or create_legacy_summary_content_envelope(
                    text,
                    session_id=candidate.id,
                    last_message_id=effective_anchor,
                )
                effective_token_stats = dict(candidate.token_stats)
                if token_stats:
                    effective_token_stats.update(token_stats)
                if summary_envelope is not None:
                    effective_token_stats["summary_provenance_version"] = SUMMARY_PROVENANCE_VERSION
                candidate_envelope = validate_summary_content_envelope(
                    text,
                    candidate_envelope,
                    session_id=candidate.id,
                    last_message_id=effective_anchor,
                )
                if summary_envelope is not None and candidate_envelope.source_kind == SUMMARY_SOURCE_KIND:
                    source_message_ids = _summary_source_message_ids(dict(token_stats or {}))
                    if source_message_ids is None:
                        raise ValueError("authenticated summary writes require canonical source message ids")
                    candidate_envelope = validate_summary_content_envelope(
                        text,
                        candidate_envelope,
                        session_id=candidate.id,
                        last_message_id=effective_anchor,
                        source_message_ids=source_message_ids,
                    )
                    effective_token_stats["summary_source_message_ids"] = source_message_ids
                candidate.conversation_summary = text
                candidate.conversation_summary_envelope = candidate_envelope
                effective_token_stats[SUMMARY_ENVELOPE_TOKEN_KEY] = candidate_envelope.model_dump(mode="json")
                candidate.token_stats = effective_token_stats
            if last_message_id:
                candidate.last_summarized_message_id = last_message_id
            if token_stats and not text:
                candidate.token_stats.update(token_stats)
            if resumed_from_task_id:
                candidate.resumed_from_task_id = resumed_from_task_id
            if resumed_from_boundary_id:
                candidate.resumed_from_boundary_id = resumed_from_boundary_id
            if parent_session_id:
                candidate.parent_session_id = parent_session_id
            candidate.ensure_summary_provenance()
            return self.save(candidate, expected_updated_at=expected_updated_at)

    def _mutate_latest(self, mutate: Callable[[SessionContext], None]) -> SessionContext:
        last_conflict: SessionSummaryConflictError | None = None
        for _attempt in range(_SESSION_CONTEXT_WRITE_RETRIES):
            latest = self.load(self.session_id)
            expected_updated_at = latest.updated_at
            candidate = latest.model_copy(deep=True)
            mutate(candidate)
            try:
                return self.save(candidate, expected_updated_at=expected_updated_at)
            except SessionSummaryConflictError as exc:
                last_conflict = exc
        raise last_conflict or SessionSummaryConflictError("session context changed while applying a state update")

    def planning_context(self, *, include_private_summary_envelope: bool = False) -> dict[str, Any]:
        with self._lock:
            context = self.current.context_for_planning()
            if include_private_summary_envelope:
                envelope = self.current.ensure_summary_provenance()
                context["_conversation_summary_envelope"] = (
                    envelope.model_dump(mode="json") if envelope is not None else None
                )
            return context


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


def _summary_source_message_ids(token_stats: dict[str, Any]) -> list[str] | None:
    if not isinstance(token_stats, dict):
        return None
    direct = token_stats.get("summary_source_message_ids")
    if isinstance(direct, list):
        return [str(item or "").strip() for item in direct]
    metadata = _compact_metadata(token_stats)
    compacted = metadata.get("messages_to_summarize_ids")
    if isinstance(compacted, list):
        return [str(item or "").strip() for item in compacted]
    return None


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


def _next_updated_at(previous_updated_at: str) -> str:
    candidate = now_iso()
    try:
        previous = datetime.fromisoformat(str(previous_updated_at).replace("Z", "+00:00"))
        current = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if current <= previous:
            return (previous + timedelta(microseconds=1)).isoformat()
    except (TypeError, ValueError):
        pass
    return candidate
