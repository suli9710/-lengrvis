from __future__ import annotations

import threading
from typing import Any


class ToolAbortedError(RuntimeError):
    """Raised when a sync tool worker observes a cooperative cancel request."""


def raise_if_tool_aborted(context: dict[str, Any] | None) -> None:
    abort = (context or {}).get("_tool_abort_event")
    if isinstance(abort, threading.Event) and abort.is_set():
        raise ToolAbortedError("Tool execution was cancelled.")
