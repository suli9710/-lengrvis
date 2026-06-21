"""Lightweight in-process tracing primitives.

``span`` is a context manager that establishes trace/span correlation IDs and
records a ``span_duration_seconds`` histogram labelled by span name and status.
``traced`` is a convenience decorator around ``span``.
"""

from __future__ import annotations

import functools
import logging
import time
from contextlib import contextmanager
from typing import Callable, Dict, Optional

import app.observability.context as obs_context
import app.observability.metrics as metrics

_logger = logging.getLogger("lengrvis.observability.tracing")


class Span:
    """A minimal span carrying a name, correlation IDs, and attributes."""

    def __init__(self, name: str, trace_id: str, span_id: str):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.attributes: Dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[str(key)] = value


@contextmanager
def span(name: str, attributes: Optional[Dict[str, object]] = None):
    trace_id = obs_context.get_trace_id() or obs_context.new_trace_id()
    span_id = obs_context.new_span_id()
    trace_token = obs_context.set_trace_id(trace_id)
    span_token = obs_context.set_span_id(span_id)
    current = Span(name, trace_id, span_id)
    if attributes:
        for key, value in attributes.items():
            current.set_attribute(key, value)
    start = time.perf_counter()
    status = "ok"
    try:
        yield current
    except Exception:
        status = "error"
        raise
    finally:
        duration = time.perf_counter() - start
        metrics.observe_histogram(
            "span_duration_seconds",
            duration,
            labels={"span": name, "status": status},
        )
        _logger.debug(
            "span.end",
            extra={
                "observability": {
                    "span": name,
                    "status": status,
                    "duration_seconds": duration,
                }
            },
        )
        obs_context.reset_span_id(span_token)
        obs_context.reset_trace_id(trace_token)


def traced(name: Optional[str] = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        span_name = name or getattr(func, "__qualname__", getattr(func, "__name__", "span"))

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with span(span_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator
