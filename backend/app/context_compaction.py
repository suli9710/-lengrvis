from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import AppSettings
from app.context_management import (
    compact_boundary_view,
    count_messages_tokens,
    recent_complete_tail_start,
    repair_tool_message_invariants,
    summarize_messages,
)
from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole, new_id, now_iso
from app.core.session_context import SessionContextStore, get_session_context_store
from app.llm.prompts import render_prompt
from app.llm.registry import get_effective_settings
from app.orchestration.agent_bus import AgentBus


MANUAL_COMPACT_BOUNDARY = "manual_compact"


@dataclass(frozen=True, slots=True)
class ManualCompactResult:
    messages: list[dict[str, Any]]
    boundary_message: dict[str, Any]
    summary: str
    pre_compact_tokens: int
    post_compact_tokens: int
    compacted_messages: int
    retained_tail_messages: int
    original_count: int
    compacted_count: int
    compact_metadata: dict[str, Any] | None = None
    session_context: dict[str, Any] | None = None
    task_id: str = ""
    persisted_message_id: str = ""


def manual_compact_messages(
    messages: list[dict[str, Any]],
    settings: AppSettings | None = None,
    *,
    custom_instructions: str = "",
    recent_message_limit: int | None = None,
    session_context: dict[str, Any] | None = None,
) -> ManualCompactResult:
    resolved_settings = settings or get_effective_settings()
    normalized = _normalize_messages(messages)
    visible = compact_boundary_view(normalized)
    pre_tokens = count_messages_tokens(visible)
    recent_limit = max(1, int(recent_message_limit or resolved_settings.context_recent_message_limit))
    head_end = _protected_head_end(visible)
    protected_head = [message for message in visible[:head_end] if not _is_boundary(message)]
    tail_start = recent_complete_tail_start(visible, recent_limit, min_start_index=head_end)
    tail = [dict(message) for message in visible[tail_start:]]
    compacted = visible[head_end:tail_start]
    tail_ids = _message_ids(tail)
    compact_metadata = build_compact_metadata(
        visible,
        compacted,
        tail,
        trigger=MANUAL_COMPACT_BOUNDARY,
        pre_compact_tokens=pre_tokens,
        logical_parent_id=_latest_boundary_id(visible),
    )
    summary = _manual_summary(compacted, resolved_settings, custom_instructions=custom_instructions)
    if session_context:
        session_summary = _session_summary(session_context)
        if session_summary:
            summary = f"{session_summary}\n\n{summary}" if summary else session_summary
    boundary = make_manual_compact_boundary(
        summary,
        custom_instructions=custom_instructions,
        compacted_messages=len(compacted),
        pre_compact_tokens=pre_tokens,
        retained_tail_message_ids=tail_ids,
        compact_metadata=compact_metadata,
    )
    result_messages = repair_tool_message_invariants([*protected_head, boundary, *tail])
    post_tokens = count_messages_tokens(result_messages)
    boundary["metadata"]["post_compact_tokens"] = post_tokens
    boundary["metadata"]["retained_tail_messages"] = len(tail)
    boundary["metadata"]["original_messages"] = len(visible)
    boundary["metadata"]["compacted_count"] = len(result_messages)
    boundary["metadata"]["summary_chars"] = len(summary)
    compact_metadata.update(
        {
            "context_boundary": boundary["metadata"].get("context_boundary"),
            "compact_boundary": boundary["metadata"].get("compact_boundary"),
            "compaction_strategy": boundary["metadata"].get("compaction_strategy"),
            "compacted_at": boundary["metadata"].get("compacted_at"),
            "custom_instructions": boundary["metadata"].get("custom_instructions"),
            "compacted_messages": len(compacted),
            "retained_tail_messages": len(tail),
            "retained_tail_message_ids": tail_ids,
            "original_messages": len(visible),
            "compacted_count": len(result_messages),
            "summary_chars": len(summary),
            "post_compact_tokens": post_tokens,
        }
    )
    boundary["metadata"]["compact_metadata"] = compact_metadata
    boundary_index = _boundary_index(result_messages)
    if boundary_index is not None:
        result_messages[boundary_index] = boundary
    return ManualCompactResult(
        messages=result_messages,
        boundary_message=boundary,
        summary=summary,
        pre_compact_tokens=pre_tokens,
        post_compact_tokens=post_tokens,
        compacted_messages=len(compacted),
        retained_tail_messages=len(tail),
        original_count=len(visible),
        compacted_count=len(result_messages),
        compact_metadata=compact_metadata,
    )


def compact_session_context(
    messages: list[dict[str, Any]],
    settings: AppSettings | None = None,
    *,
    custom_instructions: str = "",
    recent_message_limit: int | None = None,
    session_store: SessionContextStore | None = None,
    session_id: str | None = None,
) -> ManualCompactResult:
    store = session_store or get_session_context_store()
    if session_id:
        store.load(session_id)
    session_context = store.planning_context()
    result = manual_compact_messages(
        messages,
        settings,
        custom_instructions=custom_instructions,
        recent_message_limit=recent_message_limit,
        session_context=session_context,
    )
    store.remember_summary(
        result.summary,
        last_message_id=str(_last_message_id(messages) or ""),
        token_stats={
            "strategy": MANUAL_COMPACT_BOUNDARY,
            "session_id": store.current.id,
            "pre_compact_tokens": result.pre_compact_tokens,
            "post_compact_tokens": result.post_compact_tokens,
            "compacted_messages": result.compacted_messages,
            "retained_tail_messages": result.retained_tail_messages,
            "original_messages": result.original_count,
            "compacted_count": result.compacted_count,
            "compact_metadata": result.compact_metadata or {},
        },
        resumed_from_boundary_id=str(result.boundary_message.get("id") or ""),
    )
    return ManualCompactResult(
        messages=result.messages,
        boundary_message=result.boundary_message,
        summary=result.summary,
        pre_compact_tokens=result.pre_compact_tokens,
        post_compact_tokens=result.post_compact_tokens,
        compacted_messages=result.compacted_messages,
        retained_tail_messages=result.retained_tail_messages,
        original_count=result.original_count,
        compacted_count=result.compacted_count,
        compact_metadata=result.compact_metadata,
        session_context=store.planning_context(),
    )


def compact_task_context(
    task_id: str,
    settings: AppSettings | None = None,
    *,
    custom_instructions: str = "",
    recent_message_limit: int | None = None,
    session_store: SessionContextStore | None = None,
    session_id: str | None = None,
    bus: AgentBus | None = None,
    persist_session_context: bool = True,
    persist_agent_boundary: bool = True,
) -> ManualCompactResult:
    task_messages = load_task_messages(task_id, bus=bus)
    if persist_session_context:
        result = compact_session_context(
            task_messages,
            settings,
            custom_instructions=custom_instructions,
            recent_message_limit=recent_message_limit,
            session_store=session_store,
            session_id=session_id,
        )
    else:
        result = manual_compact_messages(
            task_messages,
            settings,
            custom_instructions=custom_instructions,
            recent_message_limit=recent_message_limit,
        )
    if not persist_agent_boundary:
        return _copy_result(result, task_id=task_id)

    boundary = persist_compact_boundary(task_id, result.boundary_message, bus=bus)
    boundary_payload = boundary.to_openai_dict(include_legacy=False)
    result_messages = [dict(message) for message in result.messages]
    boundary_index = _boundary_index(result_messages)
    if boundary_index is None:
        result_messages = [boundary_payload, *result_messages]
    else:
        result_messages[boundary_index] = boundary_payload
    return ManualCompactResult(
        messages=result_messages,
        boundary_message=boundary_payload,
        summary=result.summary,
        pre_compact_tokens=result.pre_compact_tokens,
        post_compact_tokens=result.post_compact_tokens,
        compacted_messages=result.compacted_messages,
        retained_tail_messages=result.retained_tail_messages,
        original_count=result.original_count,
        compacted_count=result.compacted_count,
        compact_metadata=result.compact_metadata,
        session_context=result.session_context,
        task_id=task_id,
        persisted_message_id=boundary.id,
    )


def load_task_messages(task_id: str, *, bus: AgentBus | None = None) -> list[dict[str, Any]]:
    task_bus = bus or AgentBus()
    messages = sorted(task_bus.get_messages(task_id), key=lambda message: (message.created_at, message.id))
    return [message.to_openai_dict(include_legacy=False) for message in messages]


def persist_compact_boundary(
    task_id: str,
    boundary_message: dict[str, Any],
    *,
    bus: AgentBus | None = None,
) -> AgentMessage:
    task_bus = bus or AgentBus()
    metadata = dict(boundary_message.get("metadata") or {})
    metadata.setdefault("context_boundary", MANUAL_COMPACT_BOUNDARY)
    metadata.setdefault("compact_boundary", True)
    return task_bus.publish(
        AgentMessage(
            id=str(boundary_message.get("id") or new_id("msg")),
            task_id=task_id,
            role=OpenAIMessageRole.SYSTEM,
            name=None,
            metadata=metadata,
            from_agent="ContextManager",
            to_agent=None,
            message_type=MessageType.NOTIFICATION,
            content=str(boundary_message.get("content") or ""),
            structured_payload={
                "kind": "context_compact_boundary",
                "context_boundary": metadata.get("context_boundary"),
                "pre_compact_tokens": metadata.get("pre_compact_tokens"),
                "post_compact_tokens": metadata.get("post_compact_tokens"),
                "compacted_messages": metadata.get("compacted_messages"),
                "retained_tail_messages": metadata.get("retained_tail_messages"),
                "original_messages": metadata.get("original_messages"),
                "compacted_count": metadata.get("compacted_count"),
                "summary_chars": metadata.get("summary_chars"),
                "compacted_at": metadata.get("compacted_at"),
                "retained_tail_message_ids": metadata.get("retained_tail_message_ids") or [],
                "compact_metadata": metadata.get("compact_metadata") or {},
            },
        )
    )


def manual_compact_result_to_dict(result: ManualCompactResult) -> dict[str, Any]:
    return {
        "messages": result.messages,
        "boundary_message": result.boundary_message,
        "summary": result.summary,
        "pre_compact_tokens": result.pre_compact_tokens,
        "post_compact_tokens": result.post_compact_tokens,
        "compacted_messages": result.compacted_messages,
        "retained_tail_messages": result.retained_tail_messages,
        "original_count": result.original_count,
        "compacted_count": result.compacted_count,
        "compact_metadata": result.compact_metadata or {},
        "session_context": result.session_context,
        "task_id": result.task_id,
        "persisted_message_id": result.persisted_message_id,
    }


def make_manual_compact_boundary(
    summary: str,
    *,
    custom_instructions: str = "",
    compacted_messages: int = 0,
    pre_compact_tokens: int = 0,
    retained_tail_message_ids: list[str] | None = None,
    compact_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = render_prompt("context_auto_compaction.md", {"summary_text": summary})
    instructions = custom_instructions.strip()
    if instructions:
        body = f"{body}\n\nManual compact instructions:\n{instructions}"
    compact_meta = dict(compact_metadata or {})
    compact_meta.setdefault("trigger", MANUAL_COMPACT_BOUNDARY)
    compact_meta.setdefault("messages_to_keep_ids", list(retained_tail_message_ids or []))
    return {
        "id": new_id("msg"),
        "role": "system",
        "content": body,
        "metadata": {
            "context_boundary": MANUAL_COMPACT_BOUNDARY,
            "compact_boundary": True,
            "trigger": compact_meta.get("trigger", MANUAL_COMPACT_BOUNDARY),
            "compaction_strategy": MANUAL_COMPACT_BOUNDARY,
            "compacted_at": now_iso(),
            "summary": summary,
            "custom_instructions": instructions,
            "compacted_messages": max(0, int(compacted_messages or 0)),
            "pre_compact_tokens": max(0, int(pre_compact_tokens or 0)),
            "retained_tail_message_ids": list(retained_tail_message_ids or []),
            "compact_metadata": compact_meta,
        },
    }


def _manual_summary(messages: list[dict[str, Any]], settings: AppSettings, *, custom_instructions: str) -> str:
    summary = summarize_messages(messages, settings)
    instructions = custom_instructions.strip()
    if not summary and messages:
        summary = "Earlier conversation summary:\n- Conversation content was compacted."
    if instructions:
        summary = f"{summary}\n\nUser compact instructions:\n{instructions}" if summary else instructions
    return summary


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        item["role"] = str(item.get("role") or "user")
        if item.get("content") is None:
            item["content"] = ""
        normalized.append(item)
    return normalized


def _is_boundary(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") or {}
    return isinstance(metadata, dict) and bool(metadata.get("compact_boundary"))


def _protected_head_end(messages: list[dict[str, Any]]) -> int:
    index = 0
    while index < len(messages) and messages[index].get("role") in {"system", "developer"}:
        if _is_boundary(messages[index]):
            break
        index += 1
    return index


def _last_message_id(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        message_id = str(message.get("id") or "").strip()
        if message_id:
            return message_id
    return ""


def _session_summary(session_context: dict[str, Any]) -> str:
    text = str(session_context.get("conversation_summary") or "").strip()
    return f"Existing session summary:\n{text}" if text else ""


def _boundary_index(messages: list[dict[str, Any]]) -> int | None:
    for index, message in enumerate(messages):
        if _is_boundary(message):
            return index
    return None


def build_compact_metadata(
    visible: list[dict[str, Any]],
    summarized: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    *,
    trigger: str,
    pre_compact_tokens: int,
    logical_parent_id: str = "",
) -> dict[str, Any]:
    tail_ids = _message_ids(tail)
    summarized_ids = _message_ids(summarized)
    return {
        "trigger": trigger,
        "last_pre_compact_id": _last_message_id(visible),
        "messages_to_summarize_ids": summarized_ids,
        "messages_to_keep_ids": tail_ids,
        "messages_summarized": len(summarized),
        "messages_kept": len(tail),
        "logical_parent_id": logical_parent_id,
        "pre_compact_tokens": max(0, int(pre_compact_tokens or 0)),
        "preserved_segment": {
            "head_id": tail_ids[0] if tail_ids else "",
            "anchor_id": _anchor_message_id(summarized, tail),
            "tail_id": tail_ids[-1] if tail_ids else "",
            "message_ids": tail_ids,
            "messages": [dict(message) for message in tail],
        },
    }


def _compact_metadata_from_boundary(boundary: dict[str, Any]) -> dict[str, Any]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    compact_metadata = metadata.get("compact_metadata")
    if isinstance(compact_metadata, dict):
        return dict(compact_metadata)
    keys = {
        "context_boundary",
        "compact_boundary",
        "compaction_strategy",
        "compacted_at",
        "pre_compact_tokens",
        "post_compact_tokens",
        "compacted_messages",
        "retained_tail_messages",
        "retained_tail_message_ids",
        "original_messages",
        "compacted_count",
        "summary_chars",
        "custom_instructions",
    }
    return {key: metadata.get(key) for key in keys if key in metadata}


def _message_ids(messages: list[dict[str, Any]]) -> list[str]:
    return [message_id for message_id in (str(message.get("id") or "").strip() for message in messages) if message_id]


def _anchor_message_id(summarized: list[dict[str, Any]], tail: list[dict[str, Any]]) -> str:
    if summarized:
        return str(summarized[-1].get("id") or "").strip()
    if tail:
        return str(tail[0].get("id") or "").strip()
    return ""


def _latest_boundary_id(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if _is_boundary(message):
            return str(message.get("id") or "").strip()
    return ""


def _copy_result(
    result: ManualCompactResult,
    *,
    task_id: str = "",
    persisted_message_id: str = "",
) -> ManualCompactResult:
    return ManualCompactResult(
        messages=result.messages,
        boundary_message=result.boundary_message,
        summary=result.summary,
        pre_compact_tokens=result.pre_compact_tokens,
        post_compact_tokens=result.post_compact_tokens,
        compacted_messages=result.compacted_messages,
        retained_tail_messages=result.retained_tail_messages,
        original_count=result.original_count,
        compacted_count=result.compacted_count,
        compact_metadata=result.compact_metadata,
        session_context=result.session_context,
        task_id=task_id or result.task_id,
        persisted_message_id=persisted_message_id or result.persisted_message_id,
    )
