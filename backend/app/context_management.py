from __future__ import annotations

import copy
import json
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
    "prompt too long",
    "prompt-too-long",
    "too many tokens",
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
) -> ContextProjection:
    original = _normalize_messages(messages)
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
    )
    if projection.compacted:
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
    protected_head = [message for message in messages[:2] if message.get("role") in {"system", "developer"}]
    tail = messages[-keep_recent:]
    removed = max(0, len(messages) - len(protected_head) - len(tail))
    if removed <= 0:
        return messages, False
    boundary = _system_context_message(
        render_prompt("context_history_snip.md", {"removed": removed}),
        {"context_boundary": "history_snip", "removed_messages": removed},
    )
    return [*protected_head, boundary, *tail], True


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
    recent = copy.deepcopy(messages[-recent_limit:])
    head = [message for message in messages[:2] if message.get("role") in {"system", "developer"}]
    middle = messages[len(head) : max(len(head), len(messages) - recent_limit)]
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
    compacted = [*head, boundary, *recent]
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
    return project_messages_for_llm(raw, settings, source=source)


def is_prompt_too_long_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in PROMPT_TOO_LONG_MARKERS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {400, 413}:
            try:
                body = exc.response.text.lower()
            except Exception:
                body = ""
            return any(marker in body for marker in PROMPT_TOO_LONG_MARKERS)
    return False


class PromptTooLongError(RuntimeError):
    """Raised for context-window errors that should trigger compaction, not circuit breaking."""


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
                "strategy": projection.strategy,
                "original_tokens": projection.original_tokens,
                "projected_tokens": projection.projected_tokens,
                "compacted": projection.compacted,
            },
        )
        return response

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        if not self.profile.capabilities.structured_json:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support structured JSON.")
        projection = self.prepare(messages, purpose=f"{self.task}:structured")
        try:
            return await self.provider.structured_chat(
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
            return await self.provider.structured_chat(
                retry_projection.messages,  # type: ignore[arg-type]
                output_schema,
            )

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
            return ContextProjection(
                messages=normalized,
                original_count=len(normalized),
                projected_count=len(normalized),
                original_tokens=count_messages_tokens(normalized),
                projected_tokens=count_messages_tokens(normalized),
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
            compacted = [
                _system_context_message(
                    load_prompt("context_reactive_compaction.md"),
                    {"context_boundary": "reactive_compact"},
                ),
                *normalized[-keep_recent:],
            ]
    return ContextProjection(
        messages=compacted,
        original_count=len(normalized),
        projected_count=len(compacted),
        original_tokens=count_messages_tokens(normalized),
        projected_tokens=count_messages_tokens(compacted),
        compacted=True,
        history_snipped=True,
        strategy="reactive_compact",
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
