from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from app.core.content_provenance import (
    coerce_content_envelope,
    content_envelope_integrity_valid,
    create_content_envelope,
    merge_content_envelopes,
    stable_content_hash,
)
from app.core.schemas import ContentEnvelope

SUMMARY_PROVENANCE_VERSION = "conversation-summary/v1"
SUMMARY_ENVELOPE_TOKEN_KEY = "summary_content_envelope"  # noqa: S105 - metadata key, not a credential.
SUMMARY_SOURCE_KIND = "conversation_summary"
LEGACY_SUMMARY_SOURCE_KIND = "legacy_session_summary"
CONVERSATION_SEGMENT_SOURCE_KIND = "conversation_segment"
LEGACY_SUMMARY_TAINT = "legacy_summary_lineage_unavailable"


class SummaryProvenanceError(ValueError):
    """Raised when persisted summary provenance cannot be authenticated."""


def build_summary_content_envelope(
    summary: str,
    messages: Iterable[Mapping[str, Any]],
    *,
    session_id: str,
    last_message_id: str,
    source_message_ids: Iterable[str] | None = None,
    existing_summary: str = "",
    existing_envelope: ContentEnvelope | Mapping[str, Any] | None = None,
    existing_last_message_id: str = "",
    custom_instructions: str = "",
    context_inputs: Mapping[str, Any] | None = None,
    task_id: str = "",
    require_message_ids: bool = True,
    allow_message_id_count_mismatch: bool = False,
) -> ContentEnvelope:
    """Bind one summary to its exact inputs and message-id compaction segment."""

    summary_text = str(summary or "").strip()
    if not summary_text:
        raise SummaryProvenanceError("summary provenance requires non-empty summary content")
    normalized_session_id = _required_text(session_id, "session_id")
    normalized_anchor = _required_text(last_message_id, "last_message_id")
    normalized_messages = [dict(message) for message in messages]
    explicit_source_ids = source_message_ids is not None
    if explicit_source_ids:
        message_ids = _normalize_message_ids(source_message_ids)
    else:
        raw_message_ids = [str(message.get("id") or "").strip() for message in normalized_messages]
        message_ids = (
            _normalize_message_ids(raw_message_ids)
            if require_message_ids
            else _normalize_message_ids(message_id for message_id in raw_message_ids if message_id)
        )
    if require_message_ids and not allow_message_id_count_mismatch and len(message_ids) != len(normalized_messages):
        raise SummaryProvenanceError(
            "persisted summary provenance requires one stable message id per summarized message"
        )
    if require_message_ids and normalized_messages and not message_ids:
        raise SummaryProvenanceError("persisted summary provenance requires stable source message ids")

    input_bundle: dict[str, Any] = {
        "version": SUMMARY_PROVENANCE_VERSION,
        "messages": normalized_messages,
        "source_message_ids": message_ids,
    }
    instructions = str(custom_instructions or "").strip()
    if instructions:
        input_bundle["custom_instructions"] = instructions
    if context_inputs:
        input_bundle["context_inputs"] = dict(context_inputs)
    if task_id:
        input_bundle["task_id"] = str(task_id)

    segment = create_content_envelope(
        input_bundle,
        source_kind=CONVERSATION_SEGMENT_SOURCE_KIND,
        source_id=conversation_segment_source_id(message_ids),
        origin="context.summary",
        trust_level="unknown",
        task_scope=normalized_session_id,
    )
    parents = [segment]
    parent_contents: dict[str, Any] = {segment.content_hash: input_bundle}
    field_lineage: list[dict[str, str]] = [
        {
            "output_pointer": "",
            "source_pointer": "",
            "source_kind": segment.source_kind,
            "source_id": segment.source_id,
            "source_content_hash": segment.content_hash,
            "operation": "summarize",
        }
    ]

    prior_text = str(existing_summary or "").strip()
    if prior_text:
        prior = (
            validate_summary_content_envelope(
                prior_text,
                existing_envelope,
                session_id=normalized_session_id,
                last_message_id=str(existing_last_message_id or "").strip(),
            )
            if existing_envelope is not None
            else create_legacy_summary_content_envelope(
                prior_text,
                session_id=normalized_session_id,
                last_message_id=str(existing_last_message_id or normalized_anchor),
            )
        )
        parents.insert(0, prior)
        parent_contents[prior.content_hash] = prior_text
        field_lineage.insert(
            0,
            {
                "output_pointer": "",
                "source_pointer": "",
                "source_kind": prior.source_kind,
                "source_id": prior.source_id,
                "source_content_hash": prior.content_hash,
                "operation": "merge",
            },
        )

    try:
        return merge_content_envelopes(
            parents,
            summary_text,
            source_kind=SUMMARY_SOURCE_KIND,
            source_id=summary_anchor_source_id(normalized_session_id, normalized_anchor),
            origin="context.summary",
            task_scope=normalized_session_id,
            field_lineage=field_lineage,
            parent_contents=parent_contents,
        )
    except (TypeError, ValueError) as exc:
        raise SummaryProvenanceError("summary field lineage could not be authenticated") from exc


def create_legacy_summary_content_envelope(
    summary: str,
    *,
    session_id: str,
    last_message_id: str,
) -> ContentEnvelope:
    """Authenticate legacy text without inventing unavailable message ancestry."""

    summary_text = str(summary or "").strip()
    if not summary_text:
        raise SummaryProvenanceError("legacy summary provenance requires non-empty summary content")
    normalized_session_id = _required_text(session_id, "session_id")
    normalized_anchor = str(last_message_id or "").strip()
    return create_content_envelope(
        summary_text,
        source_kind=LEGACY_SUMMARY_SOURCE_KIND,
        source_id=summary_anchor_source_id(normalized_session_id, normalized_anchor),
        origin="context.legacy_summary_migration",
        trust_level="untrusted",
        taint_flags=[LEGACY_SUMMARY_TAINT],
        task_scope=normalized_session_id,
        sanitizers_applied=["legacy_summary_root_only"],
    )


def validate_summary_content_envelope(
    summary: str,
    envelope: ContentEnvelope | Mapping[str, Any] | None,
    *,
    session_id: str,
    last_message_id: str,
    source_message_ids: Iterable[str] | None = None,
) -> ContentEnvelope:
    """Validate summary text, session/anchor binding, and optional message-id segment."""

    if envelope is None:
        raise SummaryProvenanceError("summary content envelope is missing")
    try:
        candidate = coerce_content_envelope(envelope)
    except (TypeError, ValueError) as exc:
        raise SummaryProvenanceError("summary content envelope is malformed") from exc
    if not content_envelope_integrity_valid(candidate):
        raise SummaryProvenanceError("summary content envelope integrity check failed")

    summary_text = str(summary or "").strip()
    normalized_session_id = _required_text(session_id, "session_id")
    normalized_anchor = str(last_message_id or "").strip()
    if candidate.content_hash != stable_content_hash(summary_text):
        raise SummaryProvenanceError("summary content does not match its authenticated envelope")
    if candidate.source_kind not in {SUMMARY_SOURCE_KIND, LEGACY_SUMMARY_SOURCE_KIND}:
        raise SummaryProvenanceError("summary content envelope has an unsupported source kind")
    if candidate.source_kind == SUMMARY_SOURCE_KIND and not normalized_anchor:
        raise SummaryProvenanceError("authenticated summary lineage requires a message anchor")
    if candidate.source_id != summary_anchor_source_id(normalized_session_id, normalized_anchor):
        raise SummaryProvenanceError("summary content envelope does not match the session anchor")
    if candidate.task_scope != normalized_session_id:
        raise SummaryProvenanceError("summary content envelope belongs to another session")
    if candidate.source_kind == SUMMARY_SOURCE_KIND and not candidate.field_lineage:
        raise SummaryProvenanceError("summary content envelope is missing field lineage")
    if candidate.source_kind == LEGACY_SUMMARY_SOURCE_KIND and candidate.field_lineage:
        raise SummaryProvenanceError("legacy summary provenance cannot claim field lineage")

    if source_message_ids is not None:
        normalized_ids = _normalize_message_ids(source_message_ids)
        expected_segment_id = conversation_segment_source_id(normalized_ids)
        segment_edges = [
            edge for edge in candidate.field_lineage if edge.source_kind == CONVERSATION_SEGMENT_SOURCE_KIND
        ]
        if len(segment_edges) != 1 or segment_edges[0].source_id != expected_segment_id:
            raise SummaryProvenanceError(
                "summary message-id metadata does not match its authenticated compaction segment"
            )
    return candidate


def summary_provenance_diagnostics(envelope: ContentEnvelope) -> dict[str, Any]:
    segment_edges = [edge for edge in envelope.field_lineage if edge.source_kind == CONVERSATION_SEGMENT_SOURCE_KIND]
    return {
        "summary_provenance_status": (
            "legacy_root" if envelope.source_kind == LEGACY_SUMMARY_SOURCE_KIND else "authenticated"
        ),
        "summary_anchor_authenticated": True,
        "summary_field_mapping_count": len(envelope.field_lineage),
        "summary_source_message_count": sum(_conversation_segment_count(edge.source_id) for edge in segment_edges),
        "summary_message_id_digests": [edge.source_id.rsplit(":", 1)[-1] for edge in segment_edges],
    }


def summary_anchor_source_id(session_id: str, last_message_id: str) -> str:
    payload = json.dumps(
        {
            "version": SUMMARY_PROVENANCE_VERSION,
            "session_id": str(session_id),
            "last_message_id": str(last_message_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"session-summary:v1:{hashlib.sha256(payload).hexdigest()}"


def conversation_segment_source_id(message_ids: Iterable[str]) -> str:
    normalized_ids = _normalize_message_ids(message_ids)
    payload = json.dumps(
        normalized_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"conversation-segment:v1:{len(normalized_ids)}:{hashlib.sha256(payload).hexdigest()}"


def _normalize_message_ids(values: Iterable[Any]) -> list[str]:
    message_ids = [str(value or "").strip() for value in values]
    if any(not message_id for message_id in message_ids):
        raise SummaryProvenanceError("summary message ids must be non-empty")
    if len(set(message_ids)) != len(message_ids):
        raise SummaryProvenanceError("summary message ids must be unique")
    return message_ids


def _required_text(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise SummaryProvenanceError(f"summary provenance requires {name}")
    return normalized


def _conversation_segment_count(source_id: str) -> int:
    parts = str(source_id or "").split(":")
    if len(parts) != 4 or parts[:2] != ["conversation-segment", "v1"]:
        return 0
    try:
        count = int(parts[2])
    except ValueError:
        return 0
    return max(0, count)
