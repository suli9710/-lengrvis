from __future__ import annotations

import threading
from typing import Any


class ToolAbortedError(RuntimeError):
    """Raised when a sync tool worker observes a cooperative cancel request."""


def raise_if_tool_aborted(context: dict[str, Any] | None) -> None:
    abort = tool_abort_event(context)
    if abort is not None and abort.is_set():
        raise ToolAbortedError("Tool execution was cancelled.")


def tool_abort_event(context: dict[str, Any] | None) -> threading.Event | None:
    abort = (context or {}).get("_tool_abort_event")
    return abort if isinstance(abort, threading.Event) else None
