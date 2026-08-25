"""Lightweight in-process tracing primitives.

``span`` is a context manager that establishes trace/span correlation IDs and
records a ``span_duration_seconds`` histogram labelled by span name and status.
``traced`` is a convenience decorator around ``span``.
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager

import app.observability.context as obs_context
import app.observability.metrics as metrics

_logger = logging.getLogger("lengrvis.observability.tracing")
_INHERIT_PARENT = object()
_SENSITIVE_ATTRIBUTE_SEGMENTS = frozenset(
    {
        "args",
        "arguments",
        "body",
        "content",
        "contents",
        "input",
        "message",
        "messages",
        "output",
        "payload",
        "prompt",
        "request_body",
        "response_body",
        "tool_args",
    }
)


class Span:
    """A minimal span carrying a name, correlation IDs, and attributes."""

    def __init__(self, name: str, trace_id: str, span_id: str, parent_span_id: str | None = None):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.status = "unset"
        self.outcome_unknown = False
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        normalized_key = str(key).strip()
        if not normalized_key or _sensitive_attribute_key(normalized_key):
            return
        self.attributes[normalized_key] = value

    def set_status(self, status: str) -> None:
        self.status = str(status or "unset")

    def mark_outcome_unknown(self) -> None:
        self.outcome_unknown = True
        self.set_attribute("lengrvis.outcome_unknown", True)


def _sensitive_attribute_key(key: str) -> bool:
    normalized = str(key).strip().replace("-", "_").casefold()
    return any(segment in _SENSITIVE_ATTRIBUTE_SEGMENTS for segment in normalized.split("."))


@contextmanager
def span(
    name: str,
    attributes: dict[str, object] | None = None,
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None | object = _INHERIT_PARENT,
):
    inherited_trace_id = obs_context.get_trace_id()
    inherited_span_id = obs_context.get_span_id()
    resolved_trace_id = str(trace_id or inherited_trace_id or obs_context.new_trace_id())
    resolved_span_id = str(span_id or obs_context.new_span_id())
    if parent_span_id is _INHERIT_PARENT:
        resolved_parent_span_id = inherited_span_id if not trace_id or trace_id == inherited_trace_id else None
    else:
        resolved_parent_span_id = str(parent_span_id) if parent_span_id else None
    trace_token = obs_context.set_trace_id(resolved_trace_id)
    span_token = obs_context.set_span_id(resolved_span_id)
    current = Span(name, resolved_trace_id, resolved_span_id, resolved_parent_span_id)
    if attributes:
        for key, value in attributes.items():
            current.set_attribute(key, value)
    start = time.perf_counter()
    status = "ok"
    try:
        yield current
    except Exception:  # noqa: BLE001 - broad-exception-boundary
        status = "error"
        current.set_status(status)
        raise
    finally:
        if current.status == "unset":
            current.set_status(status)
        duration = time.perf_counter() - start
        metrics.observe_histogram(
            "span_duration_seconds",
            duration,
            labels={"span": name, "status": current.status},
        )
        _logger.debug(
            "span.end",
            extra={
                "observability": {
                    "span": name,
                    "trace_id": current.trace_id,
                    "span_id": current.span_id,
                    "parent_span_id": current.parent_span_id or "",
                    "status": current.status,
                    "outcome_unknown": current.outcome_unknown,
                    "duration_seconds": duration,
                    "attributes": dict(current.attributes),
                }
            },
        )
        obs_context.reset_span_id(span_token)
        obs_context.reset_trace_id(trace_token)


def traced(name: str | None = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        span_name = name or getattr(func, "__qualname__", getattr(func, "__name__", "span"))

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with span(span_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator
