from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.config import AppSettings

CHARS_PER_TOKEN = 4
JSON_CHARS_PER_TOKEN = 2
# CJK text tokenizes far denser than ASCII (~1-1.5 chars/token vs ~4); a flat
# len/4 underestimates Chinese contexts 3-5x, so auto-compaction would never
# trigger before the provider rejects the prompt.
CJK_CHARS_PER_TOKEN = 1.6
IMAGE_OR_DOCUMENT_TOKENS = 2000
SUMMARY_RESERVED_TOKENS = 20000
ATTACHMENT_BLOCK_TYPES = {"image", "image_url", "document", "input_audio"}

_CJK_CHARS_RE = re.compile(
    "["
    "\u3000-\u303f"  # CJK punctuation
    "\u3040-\u30ff"  # Hiragana / Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "\uff00-\uffef"  # Fullwidth forms
    "]"
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


def rough_token_count(content: Any, *, bytes_per_token: int = CHARS_PER_TOKEN) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return _string_token_estimate(content, bytes_per_token)
    if isinstance(content, int | float | bool):
        return rough_token_count(str(content), bytes_per_token=bytes_per_token)
    if isinstance(content, list):
        return sum(rough_token_count(item, bytes_per_token=bytes_per_token) for item in content)
    if isinstance(content, dict):
        block_type = str(content.get("type") or "")
        if block_type in ATTACHMENT_BLOCK_TYPES:
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
    threshold = (
        auto_compact_threshold(settings)
        if settings.context_auto_compact_enabled
        else effective_context_window(settings)
    )
    warning_threshold = max(0, threshold - max(0, int(settings.context_warning_buffer_tokens)))
    error_threshold = max(0, threshold - max(0, int(settings.context_error_buffer_tokens)))
    blocking_limit = max(
        1,
        effective_context_window(settings) - max(0, int(settings.context_manual_compact_buffer_tokens)),
    )
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


def _string_token_estimate(text: str, bytes_per_token: int) -> int:
    non_cjk_length = len(_CJK_CHARS_RE.sub("", text))
    cjk_length = len(text) - non_cjk_length
    return max(0, round(cjk_length / CJK_CHARS_PER_TOKEN + non_cjk_length / max(1, bytes_per_token)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
