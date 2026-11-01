from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from app.config import AppSettings
from app.context.compaction_helpers import (
    _protected_head_end,
    _system_context_message,
    recent_complete_tail_start,
    repair_tool_message_invariants,
)
from app.context.message_text import json_text as _json
from app.context.micro_compact import (
    micro_compact_messages as _micro_compact_messages,
)
from app.context.summarization import summarize_messages
from app.context.summary_provenance import build_summary_content_envelope
from app.context.tokens import (
    auto_compact_threshold,
    count_messages_tokens,
)
from app.llm.prompts import render_prompt


def micro_compact_messages(messages: list[dict[str, Any]], settings: AppSettings) -> tuple[list[dict[str, Any]], bool]:
    return _micro_compact_messages(messages, settings)


def snip_history_if_needed(messages: list[dict[str, Any]], settings: AppSettings) -> tuple[list[dict[str, Any]], bool]:
    threshold = max(0, int(settings.context_history_snip_threshold))
    keep_recent = max(1, int(settings.context_history_snip_keep_recent))
    if threshold <= 0 or len(messages) <= threshold:
        return messages, False
    head_end = _protected_head_end(messages)
    protected_head = copy.deepcopy(messages[:head_end])
    tail_start = recent_complete_tail_start(messages, keep_recent, min_start_index=head_end)
    tail = copy.deepcopy(messages[tail_start:])
    removed = max(0, tail_start - head_end)
    if removed <= 0:
        return messages, False
    boundary = _system_context_message(
        render_prompt("context_history_snip.md", {"removed": removed}),
        {"context_boundary": "history_snip", "removed_messages": removed},
    )
    return repair_tool_message_invariants([*protected_head, boundary, *tail]), True


def inject_session_summary(
    messages: list[dict[str, Any]],
    session_context: dict[str, Any],
    settings: AppSettings,
) -> tuple[list[dict[str, Any]], bool]:
    from app.core.context_egress import filter_session_context

    session_context = filter_session_context(session_context, settings.mode) or {}
    summary = _session_summary_text(session_context, limit=max(500, int(settings.context_session_summary_limit)))
    if not summary:
        return messages, False
    system_message = _system_context_message(summary, {"context_boundary": "session_memory"})
    insertion_index = 0
    while insertion_index < len(messages) and messages[insertion_index].get("role") in {"system", "developer"}:
        insertion_index += 1
    return [*messages[:insertion_index], system_message, *messages[insertion_index:]], True


def auto_compact_messages(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    threshold = auto_compact_threshold(settings)
    if count_messages_tokens(messages) < threshold:
        return messages, False

    recent_limit = max(4, int(settings.context_recent_message_limit))
    head_end = _protected_head_end(messages)
    tail_start = recent_complete_tail_start(messages, recent_limit, min_start_index=head_end)
    recent = copy.deepcopy(messages[tail_start:])
    head = copy.deepcopy(messages[:head_end])
    middle = messages[head_end:tail_start]
    summary_text = summarize_messages(middle, settings)
    if session_context:
        session_summary = _session_summary_text(session_context, limit=2000)
        if session_summary:
            summary_text = f"{session_summary}\n\n{summary_text}" if summary_text else session_summary
    if not summary_text:
        return messages, False
    source_message_ids = [
        str(message.get("id") or "").strip() for message in middle if str(message.get("id") or "").strip()
    ]
    summary_anchor = _projection_summary_anchor(messages)
    summary_envelope = build_summary_content_envelope(
        summary_text,
        middle,
        session_id=str((session_context or {}).get("session_id") or "projection"),
        last_message_id=summary_anchor,
        source_message_ids=source_message_ids,
        existing_summary=str((session_context or {}).get("conversation_summary") or ""),
        existing_envelope=(session_context or {}).get("_conversation_summary_envelope"),
        existing_last_message_id=str((session_context or {}).get("last_summarized_message_id") or ""),
        context_inputs={"session_summary": session_summary} if session_context and session_summary else None,
        require_message_ids=False,
    )
    boundary = _system_context_message(
        render_prompt("context_auto_compaction.md", {"summary_text": summary_text}),
        {
            "context_boundary": "auto_compact",
            "compacted_messages": len(middle),
            "pre_compact_tokens": count_messages_tokens(messages),
            "summary": summary_text,
            "summary_content_envelope": summary_envelope.model_dump(mode="json"),
        },
    )
    compacted = repair_tool_message_invariants([*head, boundary, *recent])
    return compacted, count_messages_tokens(compacted) < count_messages_tokens(messages)


def _projection_summary_anchor(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        message_id = str(message.get("id") or "").strip()
        if message_id:
            return message_id
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"projection:{hashlib.sha256(payload).hexdigest()}"


def _session_summary_text(session_context: dict[str, Any], *, limit: int) -> str:
    lines: list[str] = []
    workflow = session_context.get("current_workflow_state") or {}
    if workflow:
        lines.append(f"- Current workflow state: {_json(workflow)[:1200]}")
    unfinished = list(session_context.get("unfinished_task_ids") or [])
    if unfinished:
        lines.append(f"- Unfinished tasks: {', '.join(str(item) for item in unfinished[:12])}")
    preferences = session_context.get("learned_preferences") or {}
    if preferences:
        lines.append(f"- Learned preferences: {_json(preferences)[:1200]}")
    notes = list(session_context.get("notes") or [])
    for note in notes[-8:]:
        text = str(note).strip()
        if text:
            lines.append(f"- Note: {text[:500]}")
    conversation_summary = str(session_context.get("conversation_summary") or "").strip()
    if conversation_summary:
        lines.append(f"- Conversation summary: {conversation_summary[:4000]}")
    if not lines:
        return ""
    text = "Session continuity context:\n" + "\n".join(lines)
    return text[:limit]


__all__ = [
    "auto_compact_messages",
    "inject_session_summary",
    "micro_compact_messages",
    "snip_history_if_needed",
]
