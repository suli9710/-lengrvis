from __future__ import annotations

from dataclasses import dataclass

from app.config import AppSettings

from .constants import SUMMARY_RESERVED_TOKENS


@dataclass(frozen=True, slots=True)
class TokenWarningState:
    token_count: int
    threshold: int
    percent_left: int
    is_above_warning_threshold: bool
    is_above_error_threshold: bool
    is_above_auto_compact_threshold: bool
    is_at_blocking_limit: bool


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
