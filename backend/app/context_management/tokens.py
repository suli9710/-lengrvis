from __future__ import annotations

from typing import Any, Iterable

from .constants import (
    CHARS_PER_TOKEN,
    CJK_CHARS_PER_TOKEN,
    IMAGE_OR_DOCUMENT_TOKENS,
    JSON_CHARS_PER_TOKEN,
    _CJK_CHARS_RE,
)
from .text_utils import _json


def _string_token_estimate(text: str, bytes_per_token: int) -> int:
    non_cjk_length = len(_CJK_CHARS_RE.sub("", text))
    cjk_length = len(text) - non_cjk_length
    return max(0, round(cjk_length / CJK_CHARS_PER_TOKEN + non_cjk_length / max(1, bytes_per_token)))


def rough_token_count(content: Any, *, bytes_per_token: int = CHARS_PER_TOKEN) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return _string_token_estimate(content, bytes_per_token)
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
