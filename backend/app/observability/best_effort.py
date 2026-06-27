from __future__ import annotations

import logging
import traceback
from typing import Any

from app.policy.redaction import redact_value


def log_best_effort_failure(
    logger: logging.Logger,
    operation: str,
    exc: BaseException,
    **context: Any,
) -> None:
    context_text = ""
    if context:
        safe_context = ", ".join(f"{key}={redact_value(value)}" for key, value in sorted(context.items()))
        context_text = f" ({safe_context})"
    logger.warning(
        "Best-effort operation failed during %s%s: %s\n%s",
        operation,
        context_text,
        _redacted_error(exc),
        _redacted_traceback(exc),
    )


def _redacted_error(error: BaseException | str) -> str:
    return str(redact_value(str(error)))


def _redacted_traceback(error: BaseException) -> str:
    return str(redact_value("".join(traceback.format_exception(type(error), error, error.__traceback__))))
