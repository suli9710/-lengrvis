from __future__ import annotations

import re
from typing import Any

import httpx

from .constants import PROMPT_TOO_LONG_MARKERS
from .text_utils import _json


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
