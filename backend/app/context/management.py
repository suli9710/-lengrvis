from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.config import AppSettings
from app.context.agent_message_projection import llm_safe_agent_message
from app.context.compaction_helpers import (
    compact_boundary_view,
    recent_complete_tail_start,
    repair_tool_message_invariants,
    select_recent_complete_tail,
)
from app.context.compaction_strategies import (
    auto_compact_messages,
    inject_session_summary,
    micro_compact_messages,
    snip_history_if_needed,
)
from app.context.projection import (
    ContextProjection,
    _record_event,
    build_llm_request_snapshot,
    project_ledger_for_llm,
    project_messages_for_llm,
)
from app.context.prompt_errors import (
    PROMPT_TOO_LONG_MARKERS,
    PromptTooLongError,
    _error_text,
    _response_error_text,
    is_prompt_too_long_error,
    parse_prompt_too_long_token_counts,
    prompt_too_long_error_from_exception,
)
from app.context.provider import (
    ContextAwareProvider,
    LLMCapabilityError,
    force_compact_for_retry,
    provider_safe_projection_fallback,
)
from app.context.summarization import summarize_messages
from app.context.tokens import (
    CHARS_PER_TOKEN,
    CJK_CHARS_PER_TOKEN,
    IMAGE_OR_DOCUMENT_TOKENS,
    JSON_CHARS_PER_TOKEN,
    SUMMARY_RESERVED_TOKENS,
    TokenWarningState,
    auto_compact_threshold,
    count_messages_tokens,
    effective_context_window,
    rough_token_count,
    warning_state,
)

if TYPE_CHECKING:
    from app.core.schemas import AgentMessage

logger = logging.getLogger(__name__)


def agent_messages_to_openai(
    messages: list[AgentMessage],
    settings: AppSettings,
    *,
    source: str = "agent_bus",
) -> ContextProjection:
    raw = [_message_to_llm_dict(message) for message in messages]
    return project_ledger_for_llm(raw, settings, source=source)


def _message_to_llm_dict(message: AgentMessage) -> dict[str, Any]:
    payload = llm_safe_agent_message(message)
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("from_agent", message.from_agent)
    metadata.setdefault("message_type", message.message_type.value)
    payload["metadata"] = metadata
    return payload


__all__ = [
    "CHARS_PER_TOKEN",
    "json",
    "CJK_CHARS_PER_TOKEN",
    "IMAGE_OR_DOCUMENT_TOKENS",
    "JSON_CHARS_PER_TOKEN",
    "PROMPT_TOO_LONG_MARKERS",
    "SUMMARY_RESERVED_TOKENS",
    "ContextAwareProvider",
    "ContextProjection",
    "LLMCapabilityError",
    "PromptTooLongError",
    "TokenWarningState",
    "agent_messages_to_openai",
    "auto_compact_messages",
    "auto_compact_threshold",
    "build_llm_request_snapshot",
    "compact_boundary_view",
    "count_messages_tokens",
    "effective_context_window",
    "force_compact_for_retry",
    "inject_session_summary",
    "is_prompt_too_long_error",
    "micro_compact_messages",
    "parse_prompt_too_long_token_counts",
    "project_ledger_for_llm",
    "project_messages_for_llm",
    "prompt_too_long_error_from_exception",
    "provider_safe_projection_fallback",
    "recent_complete_tail_start",
    "repair_tool_message_invariants",
    "rough_token_count",
    "select_recent_complete_tail",
    "snip_history_if_needed",
    "_error_text",
    "_record_event",
    "_response_error_text",
    "summarize_messages",
    "warning_state",
]
