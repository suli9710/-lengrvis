from __future__ import annotations

import logging
import traceback
from typing import Any

from app.policy.redaction import redact_public_text, redact_value


def log_best_effort_failure(
    logger: logging.Logger,
    operation: str,
    exc: BaseException,
    **context: Any,
) -> None:
    context_text = ""
    if context:
        safe_context = ", ".join(f"{key}={_redacted_text(value)}" for key, value in sorted(context.items()))
        context_text = f" ({safe_context})"
    logger.warning(
        "Best-effort operation failed during %s%s: %s\n%s",
        operation,
        context_text,
        _redacted_error(exc),
        _redacted_traceback(exc),
    )


def _redacted_error(error: BaseException | str) -> str:
    return _redacted_text(error)


def _redacted_traceback(error: BaseException) -> str:
    return _redacted_text("".join(traceback.format_exception(type(error), error, error.__traceback__)))


def _redacted_text(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))
