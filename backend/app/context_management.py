from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

import httpx

from app.config import AppSettings
from app.llm.base import LLMProvider
from app.llm.profiles import ProviderProfile, profile_for_provider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.types import LLMResponse
from app.llm.usage import estimate_usage, record_llm_response

if TYPE_CHECKING:
    from app.core.schemas import AgentMessage


CHARS_PER_TOKEN = 4
JSON_CHARS_PER_TOKEN = 2
IMAGE_OR_DOCUMENT_TOKENS = 2000
SUMMARY_RESERVED_TOKENS = 20000
PROMPT_TOO_LONG_MARKERS = (
    "context_length_exceeded",
    "context window",
    "context_window_exceeded",
    "maximum context",
    "model_context_window_exceeded",
    "prompt is too long",
    "prompt too long",
    "prompt-too-long",
    "too many tokens",
    "input is too long",
    "request too large",
    "maximum prompt length",
)


@dataclass(frozen=True, slots=True)
class TokenWarningState:
    token_count: int
    threshold: int
    percent_left: int
    is_above_warning_threshold: bool
    is_above_error_threshold: bool
    is_above_auto_compact_threshold: bool
    is_at_blocking_limit: bool


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


COMPACT_BOUNDARY_TYPES = {"manual_compact", "auto_compact", "reactive_compact"}


def rough_token_count(content: Any, *, bytes_per_token: int = CHARS_PER_TOKEN) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return max(0, round(len(content) / max(1, bytes_per_token)))
    if isinstance(content, (int, float, bool)):
        return rough_token_count(str(content), bytes_per_token=bytes_per_token)
    if isinstance(content, list):
        return sum(rough_token_count(item, bytes_per_token=bytes_per_token) for item in content)
    if isinstance(content, dict):
        block_type = str(content.get("type") or "")
        if block_type in {"image", "image_url", "document", "input_audio"}:
            return IMAGE_OR_DOCUMENT_TOKENS
        if block_type == "text":
            return rough_token_count(content.get("text", ""), bytes_per_token=bytes_per_token)
        if block_type == "tool_result":
            return rough_token_count(content.get("content", ""), bytes_per_token=bytes_per_token)
        if block_type == "tool_use":
            return rough_token_count(
                f"{content.get('name', '')}{_json(content.get('input') or {})}",
                bytes_per_token=JSON_CHARS_PER_TOKEN,
            )
        return rough_token_count(_json(content), bytes_per_token=JSON_CHARS_PER_TOKEN)
    return rough_token_count(str(content), bytes_per_token=bytes_per_token)


def count_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    tokens = rough_token_count(content)
    if message.get("tool_calls"):
        tokens += rough_token_count(message.get("tool_calls"), bytes_per_token=JSON_CHARS_PER_TOKEN)
    if message.get("name"):
        tokens += rough_token_count(message.get("name"))
    return tokens + 4


def count_messages_tokens(messages: Iterable[dict[str, Any]]) -> int:
    return sum(count_message_tokens(message) for message in messages)


def effective_context_window(settings: AppSettings) -> int:
    context_window = max(1, int(settings.model_context_window or 1))
    reserved = min(context_window // 2, SUMMARY_RESERVED_TOKENS, max(1, int(settings.max_tokens or 1)))
    return max(1, context_window - reserved)


def auto_compact_threshold(settings: AppSettings) -> int:
    configured = int(settings.model_auto_compact_token_limit or 0)
    if configured > 0:
        return configured
    effective = effective_context_window(settings)
    return max(1, int(effective * 0.6), effective - 13000)


def warning_state(token_count: int, settings: AppSettings) -> TokenWarningState:
    threshold = auto_compact_threshold(settings) if settings.context_auto_compact_enabled else effective_context_window(settings)
    warning_threshold = max(0, threshold - max(0, int(settings.context_warning_buffer_tokens)))
    error_threshold = max(0, threshold - max(0, int(settings.context_error_buffer_tokens)))
    blocking_limit = max(1, effective_context_window(settings) - max(0, int(settings.context_manual_compact_buffer_tokens)))
    percent_left = max(0, round(((threshold - token_count) / max(1, threshold)) * 100))
    return TokenWarningState(
        token_count=token_count,
        threshold=threshold,
        percent_left=percent_left,
        is_above_warning_threshold=token_count >= warning_threshold,
        is_above_error_threshold=token_count >= error_threshold,
        is_above_auto_compact_threshold=settings.context_auto_compact_enabled and token_count >= threshold,
        is_at_blocking_limit=token_count >= blocking_limit,
    )


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
    history_snipped = False
    session_summary_added = False

    if settings.context_micro_compact_enabled:
        projected, micro_compacted = micro_compact_messages(projected, settings)

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
        compact_metadata=_compact_metadata(boundary or {}),
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
    carries Claude Code compact-boundary semantics through Mavris metadata
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
    max_chars = max(0, int(settings.context_micro_compact_tool_result_chars))
    age = max(0, int(settings.context_micro_compact_age))
    if max_chars <= 0 or not messages:
        return messages, False

    compactable_limit = max(0, len(messages) - age)
    changed = False
    result = copy.deepcopy(messages)
    for index, message in enumerate(result):
        if index >= compactable_limit:
            continue
        role = str(message.get("role") or "")
        if role != "tool":
            continue
        content = message.get("content") or ""
        if not isinstance(content, str) or len(content) <= max_chars:
            continue
        message["content"] = _preview_text(content, max_chars)
        metadata = dict(message.get("metadata") or {})
        metadata["micro_compacted"] = True
        metadata["original_chars"] = len(content)
        message["metadata"] = metadata
        changed = True
    return result, changed


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
        tail_from_metadata = [message for message in tail_from_metadata if str(message.get("id") or "").strip() not in after_ids]
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
        kept_tool_calls = [tool_call for tool_call in tool_calls if str(tool_call.get("id") or "").strip() in result_ids]
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


def _protected_head_end(messages: list[dict[str, Any]]) -> int:
    index = 0
    while index < len(messages) and messages[index].get("role") in {"system", "developer"}:
        if _is_compact_boundary(messages[index]):
            break
        index += 1
    return index


def _tool_call_ids(message: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "").strip()
        if tool_call_id:
            ids.add(tool_call_id)
    return ids


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


def is_prompt_too_long_error(exc: BaseException) -> bool:
    if isinstance(exc, PromptTooLongError):
        return True
    text = _error_text(exc).lower()
    if any(marker in text for marker in PROMPT_TOO_LONG_MARKERS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {400, 413}:
            body = _response_error_text(exc.response).lower()
            return any(marker in body for marker in PROMPT_TOO_LONG_MARKERS)
    return False


class PromptTooLongError(RuntimeError):
    """Raised for context-window errors that should trigger compaction, not circuit breaking."""

    def __init__(
        self,
        message: str,
        *,
        actual_tokens: int | None = None,
        limit_tokens: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        raw: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.actual_tokens = actual_tokens
        self.limit_tokens = limit_tokens
        self.provider = provider
        self.model = model
        self.raw = raw

    @property
    def token_gap(self) -> int | None:
        if self.actual_tokens is None or self.limit_tokens is None:
            return None
        gap = self.actual_tokens - self.limit_tokens
        return gap if gap > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "actual_tokens": self.actual_tokens,
            "limit_tokens": self.limit_tokens,
            "token_gap": self.token_gap,
            "provider": self.provider,
            "model": self.model,
        }


def prompt_too_long_error_from_exception(
    exc: BaseException,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> PromptTooLongError:
    actual, limit = parse_prompt_too_long_token_counts(_error_text(exc))
    return PromptTooLongError(
        str(exc),
        actual_tokens=actual,
        limit_tokens=limit,
        provider=provider,
        model=model,
        raw=exc,
    )


def parse_prompt_too_long_token_counts(raw_message: str) -> tuple[int | None, int | None]:
    text = str(raw_message or "")
    patterns = [
        r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
        r"(\d+)\s*tokens?\s*>\s*(\d+)\s*(?:maximum|max|limit)",
        r"requested\s+(\d+)\s*tokens?.*?(?:maximum|limit).*?(\d+)",
        r"input.*?(\d+)\s*tokens?.*?(?:maximum|limit).*?(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        first = int(match.group(1))
        second = int(match.group(2))
        return max(first, second), min(first, second)
    return None, None


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
            response = await self._provider_chat_result(
                retry_projection.messages,  # type: ignore[arg-type]
                model=model,
                temperature=temperature,
                tools=tools,
            )
            projection = retry_projection
        response = self._with_cost(response)
        record_llm_response(
            response,
            self.settings,
            task=self.task,
            purpose="chat",
            profile=self.profile.to_dict(),
            projection={
                **projection.to_dict(),
                "context_usage": _safe_context_usage_snapshot(projection, self.settings),
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
            payload = await self.provider.structured_chat(
                retry_projection.messages,  # type: ignore[arg-type]
                output_schema,
            )
            projection = retry_projection
        structured_response = self._with_cost(
            LLMResponse(
                content=_json(payload),
                provider=getattr(self.provider, "name", self.profile.provider_name),
                model=self.profile.model,
                usage=estimate_usage(projection.messages, _json(payload)),
                metadata={"structured": True},
            )
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
        from dataclasses import replace

        return replace(response, cost=self.profile.estimate_cost(response.usage))


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


def _latest_compact_boundary_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if _is_compact_boundary(messages[index]):
            return index
    return None


def _latest_compact_boundary(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    index = _latest_compact_boundary_index(messages)
    if index is None:
        return None
    return messages[index]


def _is_compact_boundary(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    boundary = str(metadata.get("context_boundary") or "")
    compact_metadata = _compact_metadata(message)
    compact_boundary = str(
        compact_metadata.get("context_boundary")
        or compact_metadata.get("boundary_type")
        or compact_metadata.get("type")
        or ""
    )
    return (
        boundary in COMPACT_BOUNDARY_TYPES
        or compact_boundary in COMPACT_BOUNDARY_TYPES
        or bool(metadata.get("compact_boundary"))
        or bool(compact_metadata.get("compact_boundary"))
    )


def _retained_tail_message_ids(boundary: dict[str, Any]) -> set[str]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return set()
    compact_metadata = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    raw_values = [metadata.get("retained_tail_message_ids")]
    if isinstance(compact_metadata, dict):
        raw_values.extend(
            [
                compact_metadata.get("retained_tail_message_ids"),
                compact_metadata.get("messages_to_keep_ids"),
                compact_metadata.get("messagesToKeep"),
                compact_metadata.get("preserved_message_ids"),
                compact_metadata.get("preserved_segment_message_ids"),
            ]
        )
        preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment") or {}
        if isinstance(preserved, dict):
            raw_values.append(preserved.get("message_ids") or preserved.get("messageIds"))
    message_ids: set[str] = set()
    for raw_ids in raw_values:
        if isinstance(raw_ids, list):
            message_ids.update(str(item).strip() for item in raw_ids if str(item).strip())
    return message_ids


def _preserved_segment_with_tool_call_owners(
    prior_messages: list[dict[str, Any]],
    preserved_segment: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not preserved_segment:
        return []
    tool_call_owners: dict[str, dict[str, Any]] = {}
    for message in prior_messages:
        for tool_call_id in _tool_call_ids(message):
            tool_call_owners[tool_call_id] = message

    result: list[dict[str, Any]] = []
    emitted_ids = {str(message.get("id") or "").strip() for message in preserved_segment if str(message.get("id") or "").strip()}
    for message in preserved_segment:
        tool_call_id = str(message.get("tool_call_id") or "").strip() if str(message.get("role") or "") == "tool" else ""
        owner = tool_call_owners.get(tool_call_id)
        owner_id = str((owner or {}).get("id") or "").strip()
        if owner and owner_id not in emitted_ids:
            result.append(copy.deepcopy(owner))
            emitted_ids.add(owner_id)
        result.append(copy.deepcopy(message))
    return result


def _compact_metadata(boundary: dict[str, Any]) -> dict[str, Any]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    compact_metadata = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    if isinstance(compact_metadata, dict):
        return dict(compact_metadata)
    return {}


def redact_compact_metadata(compact_metadata: dict[str, Any]) -> dict[str, Any]:
    """Return compact metadata safe for API responses and telemetry."""

    redacted = copy.deepcopy(compact_metadata)
    preserved = redacted.get("preserved_segment") or redacted.get("preservedSegment")
    if isinstance(preserved, dict):
        raw_messages = preserved.pop("messages", [])
        if isinstance(raw_messages, list):
            preserved["message_count"] = len([message for message in raw_messages if isinstance(message, dict)])
        redacted["preserved_segment"] = preserved
        redacted.pop("preservedSegment", None)
    return redacted


def _preserved_segment_messages(boundary: dict[str, Any]) -> list[dict[str, Any]]:
    compact_metadata = _compact_metadata(boundary)
    preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment") or []
    raw_messages = preserved.get("messages") if isinstance(preserved, dict) else preserved
    if not isinstance(raw_messages, list):
        return []
    return [copy.deepcopy(message) for message in raw_messages if isinstance(message, dict)]


def _expand_tool_pair_message_ids(messages: list[dict[str, Any]], ids: set[str]) -> set[str]:
    if not ids:
        return set()
    expanded = set(ids)
    id_by_tool_call: dict[str, str] = {}
    tool_call_owner_ids: dict[str, str] = {}
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        for tool_call_id in _tool_call_ids(message):
            tool_call_owner_ids[tool_call_id] = message_id
        if str(message.get("role") or "") == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id and message_id:
                id_by_tool_call[tool_call_id] = message_id
    for tool_call_id, owner_id in tool_call_owner_ids.items():
        result_id = id_by_tool_call.get(tool_call_id, "")
        if owner_id in expanded and result_id:
            expanded.add(result_id)
        if result_id in expanded and owner_id:
            expanded.add(owner_id)
    return expanded


def _message_to_llm_dict(message: "AgentMessage") -> dict[str, Any]:
    payload = message.to_openai_dict(include_legacy=False)
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("from_agent", message.from_agent)
    metadata.setdefault("message_type", message.message_type.value)
    payload["metadata"] = metadata
    return payload


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
    except Exception:
        pass


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc} {_response_error_text(exc.response)}"
    return str(exc)


def _response_error_text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return response.text
    return _json(data)


def _safe_context_usage_snapshot(projection: ContextProjection, settings: AppSettings) -> dict[str, Any]:
    try:
        from app.context_usage import analyze_context_usage, context_usage_to_dict

        return context_usage_to_dict(
            analyze_context_usage(
                messages=projection.messages,
                settings=settings,
                include_registered_tools=False,
                include_session_memory=False,
                include_projection=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
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
