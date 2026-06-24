from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config import AppSettings
from app.llm.prompts import load_prompt, render_prompt

from .boundaries import (
    _compact_metadata,
    _is_compact_boundary,
    _latest_compact_boundary,
    _retained_tail_message_ids,
    _tool_call_ids,
    compact_boundary_view,
    redact_compact_metadata,
)
from .constants import ATTACHMENT_BLOCK_TYPES
from .messages import (
    _message_to_llm_dict,
    _normalize_messages,
    _protected_head_end,
    _system_context_message,
    _valid_tool_calls,
    recent_complete_tail_start,
    repair_tool_message_invariants,
    select_recent_complete_tail,
)
from .text_utils import _append_unique, _content_text, _json, _single_line
from .thresholds import auto_compact_threshold, effective_context_window, warning_state
from .tokens import count_message_tokens, count_messages_tokens

if TYPE_CHECKING:
    from app.core.schemas import AgentMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextProjection:
    messages: list[dict[str, Any]]
    original_count: int
    projected_count: int
    original_tokens: int
    projected_tokens: int
    compacted: bool = False
    micro_compacted: bool = False
    history_snipped: bool = False
    session_summary_added: bool = False
    strategy: str = "none"
    source: str = "llm"
    boundary_id: str = ""
    compact_metadata: dict[str, Any] | None = None
    retained_tail_message_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "original_count": self.original_count,
            "projected_count": self.projected_count,
            "original_tokens": self.original_tokens,
            "projected_tokens": self.projected_tokens,
            "compacted": self.compacted,
            "micro_compacted": self.micro_compacted,
            "history_snipped": self.history_snipped,
            "session_summary_added": self.session_summary_added,
            "strategy": self.strategy,
            "boundary_id": self.boundary_id,
            "compact_metadata": redact_compact_metadata(self.compact_metadata or {}),
            "retained_tail_message_ids": list(self.retained_tail_message_ids or []),
        }


def project_messages_for_llm(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None = None,
    source: str = "llm",
    record_projection_event: bool = True,
) -> ContextProjection:
    original = compact_boundary_view(_normalize_messages(messages))
    boundary = _latest_compact_boundary(original)
    original_tokens = count_messages_tokens(original)
    projected = copy.deepcopy(original)
    micro_compacted = False
    micro_compact_metadata: dict[str, Any] = _empty_micro_compact_metadata()
    history_snipped = False
    session_summary_added = False

    if settings.context_micro_compact_enabled:
        projected, micro_compacted, micro_compact_metadata = _micro_compact_messages_with_metadata(projected, settings)

    if settings.context_history_snip_enabled:
        projected, history_snipped = snip_history_if_needed(projected, settings)

    if settings.context_session_memory_enabled and session_context and _should_inject_session_context(
        projected,
        session_context,
        settings,
    ):
        projected, session_summary_added = inject_session_summary(projected, session_context, settings)

    projected_tokens = count_messages_tokens(projected)
    compacted = micro_compacted or history_snipped or session_summary_added
    if settings.context_auto_compact_enabled and projected_tokens >= auto_compact_threshold(settings):
        projected, auto_compacted = auto_compact_messages(projected, settings, session_context=session_context)
        compacted = compacted or auto_compacted
        projected_tokens = count_messages_tokens(projected)

    projected = repair_tool_message_invariants(projected)
    projected_tokens = count_messages_tokens(projected)

    projection = ContextProjection(
        messages=projected,
        original_count=len(original),
        projected_count=len(projected),
        original_tokens=original_tokens,
        projected_tokens=projected_tokens,
        compacted=compacted,
        micro_compacted=micro_compacted,
        history_snipped=history_snipped,
        session_summary_added=session_summary_added,
        strategy=_strategy(micro_compacted, history_snipped, session_summary_added, compacted),
        source=source,
        boundary_id=str((boundary or {}).get("id") or ""),
        compact_metadata=_projection_compact_metadata(boundary or {}, micro_compact_metadata),
        retained_tail_message_ids=sorted(_retained_tail_message_ids(boundary or {})),
    )
    if projection.compacted and record_projection_event:
        _record_event(
            "context.projected",
            "ContextManager",
            {
                "source": source,
                "strategy": projection.strategy,
                "original_messages": projection.original_count,
                "projected_messages": projection.projected_count,
                "original_tokens": projection.original_tokens,
                "projected_tokens": projection.projected_tokens,
                "tokens_saved": max(0, projection.original_tokens - projection.projected_tokens),
            },
        )
    return projection


def project_ledger_for_llm(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None = None,
    source: str = "agent_bus",
    record_projection_event: bool = True,
) -> ContextProjection:
    """Project the durable message ledger into a provider-safe prompt view.

    The ledger remains ``agent_messages``/OpenAI-like dicts. This adapter
    carries Lengrvis Code compact-boundary semantics through Lengrvis metadata
    rather than importing the TypeScript session runtime.
    """

    return project_messages_for_llm(
        messages,
        settings,
        session_context=session_context,
        source=source,
        record_projection_event=record_projection_event,
    )


def micro_compact_messages(messages: list[dict[str, Any]], settings: AppSettings) -> tuple[list[dict[str, Any]], bool]:
    result, changed, _metadata = _micro_compact_messages_with_metadata(messages, settings)
    return result, changed


def _micro_compact_messages_with_metadata(
    messages: list[dict[str, Any]],
    settings: AppSettings,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    max_chars = max(0, int(settings.context_micro_compact_tool_result_chars))
    age = max(0, int(settings.context_micro_compact_age))
    metadata = _empty_micro_compact_metadata()
    if max_chars <= 0 or not messages:
        return messages, False, metadata

    compactable_limit = max(0, len(messages) - age)
    changed = False
    result = copy.deepcopy(messages)
    tool_context_by_id = _tool_context_by_id(result)
    for index, message in enumerate(result):
        if index >= compactable_limit:
            continue
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            continue

        before_tokens = count_message_tokens(message)
        cleared_attachment_ids: list[str] = []
        collapsed_attachment_content, cleared_attachment_ids = _collapse_attachment_blocks(
            message.get("content"),
            message=message,
        )
        if cleared_attachment_ids:
            message["content"] = collapsed_attachment_content

        compacted_tool_id = ""
        collapse_summary: dict[str, Any] = {}
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            tool_context = tool_context_by_id.get(tool_call_id, {})
            content = message.get("content") or ""
            content_text = _content_text(content)
            if len(content_text) > max_chars:
                compacted_tool_id = tool_call_id or str(message.get("id") or "").strip()
                collapse_summary = _tool_result_collapse_summary(
                    message,
                    content_text,
                    max_chars=max_chars,
                    tool_context=tool_context,
                )
                message["content"] = collapse_summary["content"]

        if not cleared_attachment_ids and not compacted_tool_id:
            continue

        after_tokens = count_message_tokens(message)
        tokens_saved = max(0, before_tokens - after_tokens)
        message_metadata = dict(message.get("metadata") or {})
        message_metadata["micro_compacted"] = True
        message_metadata["original_tokens"] = before_tokens
        message_metadata["projected_tokens"] = after_tokens
        if tokens_saved:
            message_metadata["tokens_saved"] = tokens_saved
        if compacted_tool_id:
            message_metadata["original_chars"] = collapse_summary.get("original_chars", len(_content_text(content)))
            message_metadata["collapse_kind"] = collapse_summary.get("kind", "tool_result")
            message_metadata["tool_name"] = collapse_summary.get("tool_name", "")
            message_metadata["compacted_tool_id"] = compacted_tool_id
            _append_unique(metadata["compacted_tool_ids"], compacted_tool_id)
            metadata["collapsed_tool_results"].append(
                {
                    "message_id": str(message.get("id") or "").strip(),
                    "tool_call_id": compacted_tool_id,
                    "tool_name": collapse_summary.get("tool_name", ""),
                    "kind": collapse_summary.get("kind", "tool_result"),
                    "original_chars": collapse_summary.get("original_chars", 0),
                    "projected_chars": len(str(message.get("content") or "")),
                    "tokens_saved": tokens_saved,
                }
            )
        if cleared_attachment_ids:
            message_metadata["cleared_attachment_ids"] = cleared_attachment_ids
            for attachment_id in cleared_attachment_ids:
                _append_unique(metadata["cleared_attachment_ids"], attachment_id)
            metadata["cleared_attachments"].extend(
                {
                    "message_id": str(message.get("id") or "").strip(),
                    "attachment_id": attachment_id,
                }
                for attachment_id in cleared_attachment_ids
            )
        message["metadata"] = message_metadata
        metadata["tokens_saved"] += tokens_saved
        changed = True
    return result, changed, metadata


def _empty_micro_compact_metadata() -> dict[str, Any]:
    return {
        "tokens_saved": 0,
        "compacted_tool_ids": [],
        "cleared_attachment_ids": [],
        "collapsed_tool_results": [],
        "cleared_attachments": [],
    }


def _projection_compact_metadata(boundary: dict[str, Any], micro_metadata: dict[str, Any]) -> dict[str, Any]:
    compact_metadata = _compact_metadata(boundary)
    if not _has_micro_compact_metadata(micro_metadata):
        return compact_metadata

    merged = dict(compact_metadata)
    merged["tokens_saved"] = max(0, int(merged.get("tokens_saved") or 0)) + max(
        0,
        int(micro_metadata.get("tokens_saved") or 0),
    )
    for key in ("compacted_tool_ids", "cleared_attachment_ids"):
        values = list(merged.get(key) or [])
        for value in micro_metadata.get(key) or []:
            _append_unique(values, str(value))
        merged[key] = values
    merged["micro_compact"] = {
        "tokens_saved": max(0, int(micro_metadata.get("tokens_saved") or 0)),
        "compacted_tool_ids": list(micro_metadata.get("compacted_tool_ids") or []),
        "cleared_attachment_ids": list(micro_metadata.get("cleared_attachment_ids") or []),
        "collapsed_tool_results": list(micro_metadata.get("collapsed_tool_results") or []),
        "cleared_attachments": list(micro_metadata.get("cleared_attachments") or []),
    }
    return merged


def _has_micro_compact_metadata(metadata: dict[str, Any]) -> bool:
    return bool(
        int(metadata.get("tokens_saved") or 0) > 0
        or metadata.get("compacted_tool_ids")
        or metadata.get("cleared_attachment_ids")
    )


def _tool_context_by_id(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            if not tool_call_id:
                continue
            function = tool_call.get("function") or {}
            function = function if isinstance(function, dict) else {}
            context[tool_call_id] = {
                "tool_name": _tool_call_name(tool_call),
                "arguments": _parse_tool_arguments(function.get("arguments")),
            }
    return context


def _tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    return str(tool_call.get("name") or tool_call.get("type") or "unknown")


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return dict(arguments)
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"raw": arguments}
    return dict(parsed) if isinstance(parsed, dict) else {"value": parsed}


def _collapse_attachment_blocks(content: Any, *, message: dict[str, Any]) -> tuple[Any, list[str]]:
    if isinstance(content, dict):
        block_type = str(content.get("type") or "")
        if block_type not in ATTACHMENT_BLOCK_TYPES:
            return content, []
        attachment_id = _attachment_id(content, message, 0)
        return _attachment_placeholder(block_type, attachment_id), [attachment_id]

    if not isinstance(content, list):
        return content, []

    collapsed: list[Any] = []
    cleared_ids: list[str] = []
    changed = False
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            collapsed.append(item)
            continue
        block_type = str(item.get("type") or "")
        if block_type not in ATTACHMENT_BLOCK_TYPES:
            collapsed.append(copy.deepcopy(item))
            continue
        attachment_id = _attachment_id(item, message, index)
        cleared_ids.append(attachment_id)
        collapsed.append({"type": "text", "text": _attachment_placeholder(block_type, attachment_id)})
        changed = True
    return (collapsed if changed else content), cleared_ids


def _attachment_id(item: dict[str, Any], message: dict[str, Any], index: int) -> str:
    for key in ("id", "attachment_id", "attachmentId", "file_id", "fileId", "name", "path", "source"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    message_id = str(message.get("id") or "").strip() or "message"
    return f"{message_id}:attachment:{index}"


def _attachment_placeholder(block_type: str, attachment_id: str) -> str:
    return f"[{block_type} attachment cleared from projection: {attachment_id}]"


def _tool_result_collapse_summary(
    message: dict[str, Any],
    content_text: str,
    *,
    max_chars: int,
    tool_context: dict[str, Any],
) -> dict[str, Any]:
    metadata = message.get("metadata") or {}
    tool_name = str(tool_context.get("tool_name") or metadata.get("tool_name") or "unknown")
    arguments = tool_context.get("arguments") if isinstance(tool_context.get("arguments"), dict) else {}
    kind = _tool_result_kind(tool_name)
    detail = _tool_result_detail(kind, arguments)

    lines = [
        "[Tool result collapsed for projection]",
        f"tool: {tool_name}",
        f"kind: {kind}",
        f"original_chars: {len(content_text)}",
    ]
    if detail:
        lines.append(detail)
    header = "\n".join(lines)
    if len(header) > max_chars:
        header = header[: max(1, max_chars - 24)].rstrip() + "\n[summary truncated]"
    return {
        "content": header,
        "kind": kind,
        "tool_name": tool_name,
        "original_chars": len(content_text),
    }


def _tool_result_kind(tool_name: str) -> str:
    normalized = tool_name.casefold()
    if any(marker in normalized for marker in ("shell", "bash", "command", "terminal")):
        return "bash"
    if any(marker in normalized for marker in ("search", "grep", "glob", "query", "fetch_result", "summarize_results")):
        return "search"
    if any(marker in normalized for marker in ("read", "list", "metadata", "hash", "diff", "preview", "get")):
        return "read"
    return "tool_result"


def _tool_result_detail(kind: str, arguments: dict[str, Any]) -> str:
    if kind == "bash":
        command = str(arguments.get("command") or arguments.get("raw") or "").strip()
        return f"command: {_single_line(command)[:240]}" if command else ""
    if kind == "search":
        query = str(
            arguments.get("query")
            or arguments.get("pattern")
            or arguments.get("q")
            or arguments.get("raw")
            or ""
        ).strip()
        return f"query: {_single_line(query)[:240]}" if query else ""
    if kind == "read":
        path = str(
            arguments.get("path")
            or arguments.get("paths")
            or arguments.get("source")
            or arguments.get("source_path")
            or ""
        ).strip()
        return f"target: {_single_line(path)[:240]}" if path else ""
    return ""


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
    boundary = _system_context_message(
        render_prompt("context_auto_compaction.md", {"summary_text": summary_text}),
        {
            "context_boundary": "auto_compact",
            "compacted_messages": len(middle),
            "pre_compact_tokens": count_messages_tokens(messages),
        },
    )
    compacted = repair_tool_message_invariants([*head, boundary, *recent])
    return compacted, count_messages_tokens(compacted) < count_messages_tokens(messages)


def summarize_messages(messages: list[dict[str, Any]], settings: AppSettings) -> str:
    if not messages:
        return ""
    limit = max(500, int(settings.context_session_summary_limit))
    chunks: list[str] = []
    for message in messages:
        role = str(message.get("role") or "assistant")
        name = str(message.get("name") or message.get("metadata", {}).get("from_agent") or "").strip()
        label = f"{role}:{name}" if name else role
        text = _content_text(message.get("content"))
        if not text and message.get("tool_calls"):
            text = _json(message.get("tool_calls"))
        if not text:
            continue
        chunks.append(f"- {label}: {_single_line(text)[:600]}")
    if not chunks:
        return ""
    body = "\n".join(chunks)
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n- [summary truncated]"
    return "Earlier conversation summary:\n" + body


def agent_messages_to_openai(messages: list[AgentMessage], settings: AppSettings, *, source: str = "agent_bus") -> ContextProjection:
    raw = [_message_to_llm_dict(message) for message in messages]
    return project_ledger_for_llm(raw, settings, source=source)


def force_compact_for_retry(messages: list[dict[str, Any]], settings: AppSettings) -> ContextProjection:
    normalized = _normalize_messages(messages)
    session_context = _load_session_context()
    compacted, _changed = auto_compact_messages(normalized, settings, session_context=session_context)
    if compacted == normalized:
        keep_recent = max(2, int(settings.context_recent_message_limit // 2 or 2))
        compacted, _ = snip_history_if_needed(normalized, settings)
        if compacted == normalized and len(normalized) > keep_recent:
            tail = select_recent_complete_tail(normalized, keep_recent)
            compacted = [
                _system_context_message(
                    load_prompt("context_reactive_compaction.md"),
                    {"context_boundary": "reactive_compact"},
                ),
                *tail,
            ]
    compacted = repair_tool_message_invariants(compacted)
    return ContextProjection(
        messages=compacted,
        original_count=len(normalized),
        projected_count=len(compacted),
        original_tokens=count_messages_tokens(normalized),
        projected_tokens=count_messages_tokens(compacted),
        compacted=True,
        history_snipped=True,
        strategy="reactive_compact",
        source="reactive_retry",
        boundary_id=str((_latest_compact_boundary(compacted) or {}).get("id") or ""),
        compact_metadata=_compact_metadata(_latest_compact_boundary(compacted) or {}),
        retained_tail_message_ids=sorted(_retained_tail_message_ids(_latest_compact_boundary(compacted) or {})),
    )


def provider_safe_projection_fallback(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    source: str = "reactive_retry_fallback",
) -> ContextProjection:
    normalized = repair_tool_message_invariants(compact_boundary_view(_normalize_messages(messages)))
    original_tokens = count_messages_tokens(normalized)
    target_tokens = _fallback_target_tokens(settings)
    compacted = _trim_oldest_unprotected_blocks(normalized, target_tokens)
    compacted = repair_tool_message_invariants(compacted)
    boundary = _latest_compact_boundary(compacted) or _latest_compact_boundary(normalized) or {}
    metadata = _compact_metadata(boundary)
    metadata.update(
        {
            "context_boundary": metadata.get("context_boundary") or "reactive_compact",
            "fallback_strategy": "trim_oldest_unprotected",
            "tokens_saved": max(0, original_tokens - count_messages_tokens(compacted)),
            "target_tokens": target_tokens,
        }
    )
    return ContextProjection(
        messages=compacted,
        original_count=len(normalized),
        projected_count=len(compacted),
        original_tokens=original_tokens,
        projected_tokens=count_messages_tokens(compacted),
        compacted=True,
        history_snipped=True,
        strategy="reactive_compact+fallback_trim",
        source=source,
        boundary_id=str(boundary.get("id") or ""),
        compact_metadata=metadata,
        retained_tail_message_ids=sorted(_retained_tail_message_ids(boundary)),
    )


def _fallback_target_tokens(settings: AppSettings) -> int:
    return max(1, effective_context_window(settings) - max(0, int(settings.context_manual_compact_buffer_tokens)))


def _trim_oldest_unprotected_blocks(messages: list[dict[str, Any]], target_tokens: int) -> list[dict[str, Any]]:
    blocks = _message_blocks(messages)
    protected_indexes = _protected_fallback_block_indexes(blocks)
    while count_messages_tokens(_flatten_blocks(blocks)) > target_tokens:
        remove_index = next((index for index in range(len(blocks)) if index not in protected_indexes), None)
        if remove_index is None:
            break
        blocks.pop(remove_index)
        protected_indexes = {
            index - 1 if index > remove_index else index
            for index in protected_indexes
            if index != remove_index
        }
    return _flatten_blocks(blocks)


def _message_blocks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = copy.deepcopy(messages[index])
        block = [message]
        if _valid_tool_calls(message):
            index += 1
            while index < len(messages) and str(messages[index].get("role") or "") == "tool":
                block.append(copy.deepcopy(messages[index]))
                index += 1
            blocks.append(block)
            continue
        blocks.append(block)
        index += 1
    return blocks


def _protected_fallback_block_indexes(blocks: list[list[dict[str, Any]]]) -> set[int]:
    protected: set[int] = set()
    latest_boundary_index: int | None = None
    latest_user_index: int | None = None
    latest_tool_block_index: int | None = None
    for index, block in enumerate(blocks):
        first = block[0] if block else {}
        if str(first.get("role") or "") in {"system", "developer"}:
            protected.add(index)
        if any(_is_compact_boundary(message) for message in block):
            latest_boundary_index = index
        if str(first.get("role") or "") == "user":
            latest_user_index = index
        if _block_has_complete_tool_pair(block):
            latest_tool_block_index = index
    if latest_boundary_index is not None:
        protected.add(latest_boundary_index)
    if latest_user_index is not None:
        protected.add(latest_user_index)
    if latest_tool_block_index is not None and (latest_boundary_index is None or latest_tool_block_index > latest_boundary_index):
        protected.add(latest_tool_block_index)
    return protected


def _block_has_complete_tool_pair(block: list[dict[str, Any]]) -> bool:
    if not block:
        return False
    call_ids = _tool_call_ids(block[0])
    if not call_ids:
        return False
    result_ids = {
        str(message.get("tool_call_id") or "").strip()
        for message in block[1:]
        if str(message.get("role") or "") == "tool" and str(message.get("tool_call_id") or "").strip()
    }
    return bool(call_ids) and call_ids.issubset(result_ids)


def _flatten_blocks(blocks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for block in blocks for message in block]


def _load_session_context() -> dict[str, Any] | None:
    try:
        from app.core.session_context import get_session_context_store

        return get_session_context_store().planning_context()
    except Exception:
        return None


def _record_event(event_type: str, actor: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from app.core.audit import record

        record(event_type, actor, payload or {})
    except Exception as exc:
        logger.debug("audit record failed for %s by %s: %s", event_type, actor, exc, exc_info=True)


def _should_inject_session_context(
    messages: list[dict[str, Any]],
    session_context: dict[str, Any],
    settings: AppSettings,
) -> bool:
    if str(session_context.get("conversation_summary") or "").strip():
        return True
    return warning_state(count_messages_tokens(messages), settings).is_above_warning_threshold


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


def _strategy(micro_compacted: bool, history_snipped: bool, session_summary_added: bool, compacted: bool) -> str:
    parts: list[str] = []
    if micro_compacted:
        parts.append("micro")
    if history_snipped:
        parts.append("snip")
    if session_summary_added:
        parts.append("session")
    if compacted and not parts:
        parts.append("auto")
    return "+".join(parts) if parts else "none"
