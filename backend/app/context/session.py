from __future__ import annotations

import logging
from typing import Any

from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)


def _load_session_context() -> dict[str, Any] | None:
    try:
        from app.core.session_context import get_session_context_store

        return get_session_context_store().planning_context(include_private_summary_envelope=True)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: optional session context should not block projection.
        log_best_effort_failure(logger, "context.load_session_context", exc)
        return None
