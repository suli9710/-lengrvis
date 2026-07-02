from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.config import AppSettings
from app.context.agent_message_projection import llm_safe_agent_message
from app.context.compact_boundaries import (
    compact_metadata as _compact_metadata,
)
from app.context.compact_boundaries import (
    expand_tool_pair_message_ids as _expand_tool_pair_message_ids,
)
from app.context.compact_boundaries import (
    is_compact_boundary as _is_compact_boundary,
)
from app.context.compact_boundaries import (
    latest_compact_boundary as _latest_compact_boundary,
)
from app.context.compact_boundaries import (
    latest_compact_boundary_index as _latest_compact_boundary_index,
)
from app.context.compact_boundaries import (
    preserved_segment_messages as _preserved_segment_messages,
)
from app.context.compact_boundaries import (
    preserved_segment_with_tool_call_owners as _preserved_segment_with_tool_call_owners,
)
from app.context.compact_boundaries import (
    redact_compact_metadata,
)
from app.context.compact_boundaries import (
    retained_tail_message_ids as _retained_tail_message_ids,
)
from app.context.compact_boundaries import (
    tool_call_ids as _tool_call_ids,
)
from app.context.prompt_errors import (
    PROMPT_TOO_LONG_MARKERS as PROMPT_TOO_LONG_MARKERS,
)
from app.context.prompt_errors import (
    PromptTooLongError as PromptTooLongError,
)
from app.context.prompt_errors import (
    _error_text as _error_text,
)
from app.context.prompt_errors import (
    _response_error_text as _response_error_text,
)
from app.context.prompt_errors import (
    is_prompt_too_long_error,
)
from app.context.prompt_errors import (
    parse_prompt_too_long_token_counts as parse_prompt_too_long_token_counts,
)
from app.context.prompt_errors import (
    prompt_too_long_error_from_exception as prompt_too_long_error_from_exception,
)
from app.context.tokens import (
    ATTACHMENT_BLOCK_TYPES as ATTACHMENT_BLOCK_TYPES,
)
from app.context.tokens import (
    CHARS_PER_TOKEN as CHARS_PER_TOKEN,
)
from app.context.tokens import (
    CJK_CHARS_PER_TOKEN as CJK_CHARS_PER_TOKEN,
)
from app.context.tokens import (
    IMAGE_OR_DOCUMENT_TOKENS as IMAGE_OR_DOCUMENT_TOKENS,
)
from app.context.tokens import (
    JSON_CHARS_PER_TOKEN as JSON_CHARS_PER_TOKEN,
)
from app.context.tokens import (
    SUMMARY_RESERVED_TOKENS as SUMMARY_RESERVED_TOKENS,
)
from app.context.tokens import (
    TokenWarningState as TokenWarningState,
)
from app.context.tokens import (
    auto_compact_threshold,
    count_message_tokens,
    count_messages_tokens,
    effective_context_window,
    warning_state,
)
from app.context.tokens import (
    rough_token_count as rough_token_count,
)
from app.llm.base import LLMProvider
from app.llm.profiles import ProviderProfile, profile_for_provider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.types import LLMResponse
from app.llm.usage import estimate_usage, record_llm_response
from app.observability.best_effort import log_best_effort_failure

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


def build_llm_request_snapshot(
    projection: ContextProjection,
    settings: AppSettings,
    *,
    task: str,
    purpose: str,
    provider: str,
    model: str,
    tools: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(
        json.dumps(projection.messages, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        from app.policy.approval_binding import permission_policy_version
        from app.policy.permissions import PermissionStore

        policy_version = permission_policy_version(PermissionStore().updated_at())
    except Exception as exc:  # noqa: BLE001 - context snapshots tolerate policy-store failures.
        log_best_effort_failure(logger, "context_snapshot.policy_version", exc, purpose=purpose)
        policy_version = ""
    return {
        "snapshot_id": f"ctx_{prompt_hash[:16]}",
        "prompt_hash": prompt_hash,
        "visible_tool_ids": _visible_tool_ids(tools),
        "policy_version": policy_version,
        "context_projection": projection.to_dict(),
        "routing": {
            "mode": settings.mode,
            "permission_mode": getattr(settings, "permission_mode", "default"),
            "task": task,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "profile": profile or {},
        },
    }


def _visible_tool_ids(tools: list[dict[str, Any]] | None) -> list[str]:
    result: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            result.append(str(function.get("name")))
        elif tool.get("name"):
            result.append(str(tool.get("name")))
    return sorted({item for item in result if item})


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
    projected = list(original)
    micro_compacted = False
    micro_compact_metadata: dict[str, Any] = _empty_micro_compact_metadata()
    history_snipped = False
    session_summary_added = False

    if settings.context_micro_compact_enabled:
        projected, micro_compacted, micro_compact_metadata = _micro_compact_messages_with_metadata(projected, settings)

    if settings.context_history_snip_enabled:
        projected, history_snipped = snip_history_if_needed(projected, settings)

    if (
        settings.context_session_memory_enabled
        and session_context
        and _should_inject_session_context(
            projected,
            session_context,
            settings,
        )
    ):
        projected, session_summary_added = inject_session_summary(projected, session_context, settings)

    projected_tokens = count_messages_tokens(projected)
    compacted = micro_compacted or history_snipped or session_summary_added
    if settings.context_auto_compact_enabled and projected_tokens >= auto_compact_threshold(settings):
        projected, auto_compacted = auto_compact_messages(projected, settings, session_context=session_context)
        compacted = compacted or auto_compacted
        projected_tokens = count_messages_tokens(projected)

    projected = (
        repair_tool_message_invariants(projected)
        if _has_tool_protocol_messages(projected)
        else [dict(message) for message in projected]
    )
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
    result = list(messages)
    tool_context_by_id = _tool_context_by_id(messages)
    for index, message in enumerate(messages):
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
                collapsed_attachment_content = collapse_summary["content"]

        if not cleared_attachment_ids and not compacted_tool_id:
            continue

        updated = dict(message)
        updated["content"] = collapsed_attachment_content
        after_tokens = count_message_tokens(updated)
        tokens_saved = max(0, before_tokens - after_tokens)
        message_metadata = dict(updated.get("metadata") or {})
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
                    "projected_chars": len(str(updated.get("content") or "")),
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
        updated["metadata"] = message_metadata
        result[index] = updated
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


def _append_unique(values: list[str], value: str) -> None:
    normalized = str(value or "").strip()
    if normalized and normalized not in values:
        values.append(normalized)


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
            arguments.get("query") or arguments.get("pattern") or arguments.get("q") or arguments.get("raw") or ""
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


def compact_boundary_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the LLM-visible view after the newest compact boundary.

    Manual compaction persists a boundary/summary message plus a recent tail.
    If callers later pass a longer transcript that still contains older items,
    this keeps stable system/developer instructions and starts history at the
    latest compact boundary.
    """

    boundary_index = _latest_compact_boundary_index(messages)
    if boundary_index is None:
        return messages
    boundary = copy.deepcopy(messages[boundary_index])
    retained_tail_ids = _retained_tail_message_ids(boundary)
    preserved_segment = _preserved_segment_with_tool_call_owners(
        messages[:boundary_index],
        _preserved_segment_messages(boundary),
    )
    retained_tail_ids = _expand_tool_pair_message_ids(
        messages[:boundary_index],
        retained_tail_ids,
    )
    protected_head = [
        copy.deepcopy(message)
        for message in messages[:boundary_index]
        if message.get("role") in {"system", "developer"} and not _is_compact_boundary(message)
    ]
    tail_from_metadata: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if retained_tail_ids:
        for message in messages[:boundary_index]:
            message_id = str(message.get("id") or "").strip()
            if message_id in retained_tail_ids and message_id not in seen_ids and not _is_compact_boundary(message):
                tail_from_metadata.append(copy.deepcopy(message))
                seen_ids.add(message_id)
    for message in preserved_segment:
        if _is_compact_boundary(message):
            continue
        message_id = str(message.get("id") or "").strip()
        if message_id and message_id in seen_ids:
            continue
        tail_from_metadata.append(copy.deepcopy(message))
        if message_id:
            seen_ids.add(message_id)
    tail_after_boundary = copy.deepcopy(messages[boundary_index + 1 :])
    if tail_after_boundary:
        after_ids = {str(message.get("id") or "").strip() for message in tail_after_boundary}
        tail_from_metadata = [
            message for message in tail_from_metadata if str(message.get("id") or "").strip() not in after_ids
        ]
    return [*protected_head, boundary, *tail_from_metadata, *tail_after_boundary]


def recent_complete_tail_start(messages: list[dict[str, Any]], keep_recent: int, *, min_start_index: int = 0) -> int:
    """Return a recent-tail start index that does not orphan tool results.

    OpenAI-compatible chat history is sensitive to assistant ``tool_calls`` and
    subsequent ``tool`` messages staying together. A plain ``messages[-N:]`` can
    start on a tool result and leave its assistant call behind, so compaction
    expands the tail backward until visible tool results have their call site.
    """

    if not messages:
        return 0
    floor = max(0, min(len(messages), int(min_start_index or 0)))
    start = max(floor, len(messages) - max(1, int(keep_recent or 1)))
    while start > floor:
        missing = _orphan_tool_result_ids(messages[start:])
        if not missing and str(messages[start].get("role") or "") != "tool":
            break
        previous = _nearest_prior_tool_call_index(messages, start, missing)
        if previous is None:
            if str(messages[start].get("role") or "") == "tool":
                start -= 1
                continue
            break
        if previous < floor:
            break
        start = previous
    return start


def select_recent_complete_tail(
    messages: list[dict[str, Any]],
    keep_recent: int,
    *,
    min_start_index: int = 0,
) -> list[dict[str, Any]]:
    return copy.deepcopy(messages[recent_complete_tail_start(messages, keep_recent, min_start_index=min_start_index) :])


def repair_tool_message_invariants(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a provider-safe view with atomic assistant/tool message blocks.

    Tool-call messages are only kept when their matching tool result is present
    in the immediately following tool-result block. Otherwise the structured
    envelope is removed and the readable content is preserved.
    """

    if not messages:
        return []

    repaired: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        item = copy.deepcopy(messages[index])
        role = str(item.get("role") or "")
        if role == "tool":
            repaired.append(_demote_orphan_tool_result(item))
            index += 1
            continue

        tool_calls = _valid_tool_calls(item)
        if not tool_calls:
            repaired.append(_drop_tool_calls(item) if item.get("tool_calls") else item)
            index += 1
            continue

        next_index = index + 1
        contiguous_tool_results: list[dict[str, Any]] = []
        while next_index < len(messages) and str(messages[next_index].get("role") or "") == "tool":
            contiguous_tool_results.append(copy.deepcopy(messages[next_index]))
            next_index += 1

        result_ids = {
            str(result.get("tool_call_id") or "").strip()
            for result in contiguous_tool_results
            if str(result.get("tool_call_id") or "").strip()
        }
        kept_tool_calls = [
            tool_call for tool_call in tool_calls if str(tool_call.get("id") or "").strip() in result_ids
        ]
        if kept_tool_calls:
            kept_ids = {str(tool_call.get("id") or "").strip() for tool_call in kept_tool_calls}
            item["tool_calls"] = kept_tool_calls
            repaired.append(item)
            emitted_result_ids: set[str] = set()
            delayed_demotions: list[dict[str, Any]] = []
            for result in contiguous_tool_results:
                result_id = str(result.get("tool_call_id") or "").strip()
                if result_id in kept_ids and result_id not in emitted_result_ids:
                    repaired.append(result)
                    emitted_result_ids.add(result_id)
                else:
                    delayed_demotions.append(_demote_orphan_tool_result(result))
            repaired.extend(delayed_demotions)
        else:
            repaired.append(_drop_tool_calls(item))
            repaired.extend(_demote_orphan_tool_result(result) for result in contiguous_tool_results)
        index = next_index

    return repaired


def _has_tool_protocol_messages(messages: list[dict[str, Any]]) -> bool:
    return any(str(message.get("role") or "") == "tool" or bool(message.get("tool_calls")) for message in messages)


def _protected_head_end(messages: list[dict[str, Any]]) -> int:
    index = 0
    while index < len(messages) and messages[index].get("role") in {"system", "developer"}:
        if _is_compact_boundary(messages[index]):
            break
        index += 1
    return index


def _valid_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        tool_call
        for tool_call in message.get("tool_calls") or []
        if isinstance(tool_call, dict) and str(tool_call.get("id") or "").strip()
    ]


def _demote_orphan_tool_result(message: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(message)
    item["role"] = "assistant"
    item.pop("tool_call_id", None)
    metadata = dict(item.get("metadata") or {})
    metadata["orphan_tool_result_compacted"] = True
    item["metadata"] = metadata
    if not str(item.get("content") or "").strip():
        item["content"] = "[Tool result omitted during context compaction because its tool call is not in view.]"
    return item


def _drop_tool_calls(message: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(message)
    item.pop("tool_calls", None)
    metadata = dict(item.get("metadata") or {})
    metadata["tool_calls_compacted"] = True
    item["metadata"] = metadata
    if not str(item.get("content") or "").strip():
        item["content"] = "[Tool call omitted during context compaction because its result is not in view.]"
    return item


def _orphan_tool_result_ids(messages: list[dict[str, Any]]) -> set[str]:
    open_tool_call_ids: set[str] = set()
    missing: set[str] = set()
    for message in messages:
        role = str(message.get("role") or "")
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id and tool_call_id not in open_tool_call_ids:
                missing.add(tool_call_id)
            continue
        open_tool_call_ids.update(_tool_call_ids(message))
    return missing


def _nearest_prior_tool_call_index(
    messages: list[dict[str, Any]],
    start: int,
    wanted_ids: set[str],
) -> int | None:
    wanted = set(wanted_ids)
    if not wanted and 0 <= start < len(messages) and str(messages[start].get("role") or "") == "tool":
        wanted.add(str(messages[start].get("tool_call_id") or "").strip())
        wanted.discard("")
    for index in range(start - 1, -1, -1):
        ids = _tool_call_ids(messages[index])
        if wanted:
            if ids & wanted:
                return index
        elif ids:
            return index
    return None


def summarize_messages(messages: list[dict[str, Any]], settings: AppSettings) -> str:
    if not messages:
        return ""
    limit = max(500, int(settings.context_session_summary_limit))
    entries = _semantic_summary_entries(messages)
    if not entries:
        return ""
    return _fit_summary_entries(entries, limit)


def _semantic_summary_entries(messages: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    role_counts: dict[str, int] = {}
    latest_user_index = -1
    latest_user_text = ""
    for index, message in enumerate(messages):
        role = str(message.get("role") or "assistant")
        role_counts[role] = role_counts.get(role, 0) + 1
        if role == "user":
            text = _single_line(_content_text(message.get("content")))
            if text:
                latest_user_index = index
                latest_user_text = text

    entries: list[tuple[int, int, str]] = []
    counts = ", ".join(f"{role}={count}" for role, count in sorted(role_counts.items()))
    entries.append((0, -2, f"- Covered {len(messages)} earlier message(s) before compaction ({counts})."))
    if latest_user_text:
        entries.append((0, -1, f"- Latest user intent before compaction: {_clip_summary_text(latest_user_text, 420)}"))

    tool_context_by_id = _tool_context_by_id(messages)
    for index, message in enumerate(messages):
        line = _semantic_summary_line(message, tool_context_by_id=tool_context_by_id)
        if not line:
            continue
        role = str(message.get("role") or "assistant")
        if role == "user" and index == latest_user_index:
            continue
        priority = 1 if _is_high_value_summary_message(message, line) else 2
        entries.append((priority, index, line))
    return sorted(entries, key=lambda item: (item[0], item[1]))


def _semantic_summary_line(message: dict[str, Any], *, tool_context_by_id: dict[str, dict[str, Any]]) -> str:
    role = str(message.get("role") or "assistant")
    name = str(message.get("name") or message.get("metadata", {}).get("from_agent") or "").strip()
    label = f"{role}:{name}" if name else role
    tool_calls = [tool_call for tool_call in message.get("tool_calls") or [] if isinstance(tool_call, dict)]
    text = _single_line(_content_text(message.get("content")))

    if tool_calls:
        tool_summary = ", ".join(_tool_call_brief(tool_call) for tool_call in tool_calls[:6])
        suffix = f"; note: {_clip_summary_text(text, 220)}" if text else ""
        return f"- {label} requested tool(s): {tool_summary}{suffix}"

    if role == "tool":
        tool_call_id = str(message.get("tool_call_id") or "").strip()
        tool_context = tool_context_by_id.get(tool_call_id, {})
        summary = _tool_result_collapse_summary(
            message,
            text,
            max_chars=520,
            tool_context=tool_context,
        )
        return f"- tool result: {_clip_summary_text(_single_line(summary['content']), 520)}"

    if not text:
        return ""
    return f"- {label}: {_clip_summary_text(text, 420)}"


def _tool_call_brief(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    function = function if isinstance(function, dict) else {}
    arguments = _parse_tool_arguments(function.get("arguments"))
    arg_keys = ", ".join(sorted(arguments.keys())[:6])
    return f"{_tool_call_name(tool_call)}({arg_keys})" if arg_keys else f"{_tool_call_name(tool_call)}()"


def _is_high_value_summary_message(message: dict[str, Any], line: str) -> bool:
    role = str(message.get("role") or "")
    if role == "tool" or message.get("tool_calls"):
        return True
    lowered = line.casefold()
    markers = (
        "approved",
        "blocked",
        "decision",
        "denied",
        "error",
        "failed",
        "next",
        "permission",
        "plan",
        "risk",
        "todo",
    )
    return any(marker in lowered for marker in markers)


def _fit_summary_entries(entries: list[tuple[int, int, str]], limit: int) -> str:
    header = "Earlier conversation summary:\n"
    body_budget = max(1, limit - len(header))
    selected: list[str] = []
    used = 0
    omitted = 0
    for _priority, _index, raw_line in entries:
        line = _clip_summary_text(raw_line, min(700, body_budget)).rstrip()
        line_size = len(line) + (1 if selected else 0)
        if used + line_size <= body_budget or not selected:
            selected.append(line)
            used += line_size
        else:
            omitted += 1
    if omitted:
        marker = f"- [omitted {omitted} lower-priority summary item(s) due to context budget]"
        marker_size = len(marker) + (1 if selected else 0)
        while selected and used + marker_size > body_budget:
            removed = selected.pop()
            used -= len(removed) + (1 if selected else 0)
            omitted += 1
            marker = f"- [omitted {omitted} lower-priority summary item(s) due to context budget]"
            marker_size = len(marker) + (1 if selected else 0)
        if used + marker_size <= body_budget:
            selected.append(marker)
    return header + "\n".join(selected)


def _clip_summary_text(text: str, max_chars: int) -> str:
    normalized = _single_line(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(1, max_chars - 15)].rstrip() + " [truncated]"


def agent_messages_to_openai(
    messages: list[AgentMessage],
    settings: AppSettings,
    *,
    source: str = "agent_bus",
) -> ContextProjection:
    raw = [_message_to_llm_dict(message) for message in messages]
    return project_ledger_for_llm(raw, settings, source=source)


class LLMCapabilityError(RuntimeError):
    """Raised when the active model profile cannot satisfy a requested capability."""


class ContextAwareProvider(LLMProvider):
    name = "context_aware"

    def __init__(
        self,
        provider: LLMProvider,
        settings: AppSettings,
        *,
        task: str = "default",
        profile: ProviderProfile | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.task = task
        self.name = getattr(provider, "name", self.name)
        self.profile = profile or profile_for_provider(provider, settings)

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        return (await self.chat_result(messages, model=model, temperature=temperature, tools=tools)).content

    async def chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if tools and not self.profile.capabilities.tools:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support tool calls.")
        projection = self.prepare(messages, purpose=f"{self.task}:chat")
        try:
            response = await self._provider_chat_result(
                projection.messages,  # type: ignore[arg-type]
                model=model,
                temperature=temperature,
                tools=tools,
            )
        except Exception as exc:
            if not isinstance(exc, PromptTooLongError) and not is_prompt_too_long_error(exc):
                raise
            retry_projection = force_compact_for_retry(projection.messages, self.settings)
            _record_event(
                "context.reactive_retry",
                "ContextManager",
                {
                    "task": self.task,
                    "original_tokens": retry_projection.original_tokens,
                    "projected_tokens": retry_projection.projected_tokens,
                },
            )
            try:
                response = await self._provider_chat_result(
                    retry_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = retry_projection
            except Exception as retry_exc:
                if not isinstance(retry_exc, PromptTooLongError) and not is_prompt_too_long_error(retry_exc):
                    raise
                fallback_projection = provider_safe_projection_fallback(
                    projection.messages,
                    self.settings,
                    source="reactive_retry_fallback",
                )
                _record_event(
                    "context.reactive_retry_fallback",
                    "ContextManager",
                    {
                        "task": self.task,
                        "original_tokens": fallback_projection.original_tokens,
                        "projected_tokens": fallback_projection.projected_tokens,
                        "projected_messages": fallback_projection.projected_count,
                    },
                )
                response = await self._provider_chat_result(
                    fallback_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = fallback_projection
        response = self._with_request_snapshot(response, projection, purpose="chat", tools=tools)
        response = self._with_cost(response)
        request_snapshot = response.metadata.get("request_snapshot") if isinstance(response.metadata, dict) else {}
        record_llm_response(
            response,
            self.settings,
            task=self.task,
            purpose="chat",
            profile=self.profile.to_dict(),
            projection={
                **projection.to_dict(),
                "context_usage": _safe_context_usage_snapshot(projection, self.settings),
                "request_snapshot": request_snapshot,
            },
        )
        return response

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        if not self.profile.capabilities.structured_json:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support structured JSON.")
        projection = self.prepare(messages, purpose=f"{self.task}:structured")
        try:
            payload = await self.provider.structured_chat(
                projection.messages,  # type: ignore[arg-type]
                output_schema,
            )
        except Exception as exc:
            if not isinstance(exc, PromptTooLongError) and not is_prompt_too_long_error(exc):
                raise
            retry_projection = force_compact_for_retry(projection.messages, self.settings)
            _record_event(
                "context.reactive_retry",
                "ContextManager",
                {
                    "task": self.task,
                    "structured": True,
                    "original_tokens": retry_projection.original_tokens,
                    "projected_tokens": retry_projection.projected_tokens,
                },
            )
            try:
                payload = await self.provider.structured_chat(
                    retry_projection.messages,  # type: ignore[arg-type]
                    output_schema,
                )
                projection = retry_projection
            except Exception as retry_exc:
                if not isinstance(retry_exc, PromptTooLongError) and not is_prompt_too_long_error(retry_exc):
                    raise
                fallback_projection = provider_safe_projection_fallback(
                    projection.messages,
                    self.settings,
                    source="reactive_retry_fallback",
                )
                _record_event(
                    "context.reactive_retry_fallback",
                    "ContextManager",
                    {
                        "task": self.task,
                        "structured": True,
                        "original_tokens": fallback_projection.original_tokens,
                        "projected_tokens": fallback_projection.projected_tokens,
                        "projected_messages": fallback_projection.projected_count,
                    },
                )
                payload = await self.provider.structured_chat(
                    fallback_projection.messages,  # type: ignore[arg-type]
                    output_schema,
                )
                projection = fallback_projection
        payload_json = _json(payload)
        structured_response = LLMResponse(
            content=payload_json,
            provider=getattr(self.provider, "name", self.profile.provider_name),
            model=self.profile.model,
            usage=estimate_usage(projection.messages, payload_json),
            metadata={"structured": True},
        )
        structured_response = self._with_request_snapshot(
            structured_response,
            projection,
            purpose="structured_chat",
            tools=None,
        )
        structured_response = self._with_cost(structured_response)
        request_snapshot = (
            structured_response.metadata.get("request_snapshot")
            if isinstance(structured_response.metadata, dict)
            else {}
        )
        record_llm_response(
            structured_response,
            self.settings,
            task=self.task,
            purpose="structured_chat",
            profile=self.profile.to_dict(),
            projection={
                **projection.to_dict(),
                "context_usage": _safe_context_usage_snapshot(projection, self.settings),
                "request_snapshot": request_snapshot,
            },
        )
        return payload

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not self.profile.capabilities.embeddings:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support embeddings.")
        return await self.provider.embed(texts, model=model)

    async def rerank(self, query: str, documents: list[str]) -> list[int]:
        return await self.provider.rerank(query, documents)

    async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
        if not self.profile.capabilities.vision:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support vision.")
        try:
            return await self.provider.vision(image_path, prompt, model=model)  # type: ignore[call-arg]
        except TypeError:
            return await self.provider.vision(image_path, prompt)

    async def ocr(self, image_path: str) -> str:
        return await self.provider.ocr(image_path)

    async def summarize(self, text: str) -> str:
        return await self.provider.summarize(text)

    def prepare(self, messages: list[dict[str, Any]], *, purpose: str) -> ContextProjection:
        if purpose.endswith(":compact") or purpose.endswith(":session_memory"):
            normalized = _normalize_messages(messages)
            token_count = count_messages_tokens(normalized)
            return ContextProjection(
                messages=normalized,
                original_count=len(normalized),
                projected_count=len(normalized),
                original_tokens=token_count,
                projected_tokens=token_count,
                source=purpose,
            )
        return project_messages_for_llm(
            messages,
            self.settings,
            session_context=_load_session_context(),
            source=purpose,
        )

    async def _provider_chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        chat_result = getattr(self.provider, "chat_result", None)
        if callable(chat_result):
            return await chat_result(messages, model=model, temperature=temperature, tools=tools)
        content = await self.provider.chat(messages, model=model, temperature=temperature, tools=tools)
        return LLMResponse(
            content=content,
            provider=getattr(self.provider, "name", self.profile.provider_name),
            model=model or self.profile.model,
            usage=estimate_usage(messages, content),
        )

    def _with_cost(self, response: LLMResponse) -> LLMResponse:
        if response.cost is not None:
            return response

        return replace(response, cost=self.profile.estimate_cost(response.usage))

    def _with_request_snapshot(
        self,
        response: LLMResponse,
        projection: ContextProjection,
        *,
        purpose: str,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse:
        snapshot = build_llm_request_snapshot(
            projection,
            self.settings,
            task=self.task,
            purpose=purpose,
            provider=response.provider,
            model=response.model,
            tools=tools,
            profile=self.profile.to_dict(),
        )
        metadata = {**(response.metadata or {}), "request_snapshot": snapshot}
        return replace(response, metadata=metadata)


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
            index - 1 if index > remove_index else index for index in protected_indexes if index != remove_index
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
    if latest_tool_block_index is not None and (
        latest_boundary_index is None or latest_tool_block_index > latest_boundary_index
    ):
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


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        role = str(item.get("role") or "user")
        item["role"] = role
        if item.get("content") is None:
            item["content"] = ""
        normalized.append(item)
    return normalized


def _system_context_message(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "system",
        "content": content,
        "metadata": metadata,
    }


def _message_to_llm_dict(message: AgentMessage) -> dict[str, Any]:
    payload = llm_safe_agent_message(message)
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("from_agent", message.from_agent)
    metadata.setdefault("message_type", message.message_type.value)
    payload["metadata"] = metadata
    return payload


def _load_session_context() -> dict[str, Any] | None:
    try:
        from app.core.session_context import get_session_context_store

        return get_session_context_store().planning_context()
    except Exception as exc:  # noqa: BLE001 - optional session context should not block projection.
        log_best_effort_failure(logger, "context.load_session_context", exc)
        return None


def _record_event(event_type: str, actor: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from app.core.audit import record

        record(event_type, actor, payload or {})
    except Exception as exc:  # noqa: BLE001 - audit failures are best-effort here.
        log_best_effort_failure(logger, "context.record_event", exc, actor=actor, event_type=event_type)


def _safe_context_usage_snapshot(projection: ContextProjection, settings: AppSettings) -> dict[str, Any]:
    try:
        from app.context.usage import analyze_context_usage, context_usage_to_dict

        return context_usage_to_dict(
            analyze_context_usage(
                messages=projection.messages,
                settings=settings,
                include_registered_tools=False,
                include_session_memory=False,
                include_projection=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - context usage diagnostics should not block LLM calls.
        log_best_effort_failure(logger, "context.safe_usage_snapshot", exc)
        return {"error": str(exc)}


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


def _preview_text(content: str, max_chars: int) -> str:
    head = max(1, max_chars // 2)
    tail = max(1, max_chars - head)
    return (
        f"{content[:head]}\n"
        f"[Old tool result content cleared: original {len(content)} chars, preview retained for context budget]\n"
        f"{content[-tail:]}"
    )


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "tool_result":
                    parts.append(_content_text(item.get("content")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return _json(content)


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


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
