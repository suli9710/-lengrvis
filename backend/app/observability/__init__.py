"""Dependency-free observability foundation for the Lengrvis backend.

This package provides structured logging, in-process metrics, lightweight
tracing, and crash reporting without introducing any third-party dependency.
All user-facing output is routed through ``app.policy.redaction`` so secrets and
PII never leak into logs, metrics labels, or crash reports.
"""

from __future__ import annotations

from app.observability import context
from app.observability.best_effort import log_best_effort_failure
from app.observability.context import (
    correlation_snapshot,
    get_request_id,
    get_span_id,
    get_trace_id,
    new_span_id,
    new_trace_id,
    set_request_id,
    set_span_id,
    set_trace_id,
)
from app.observability.crash import (
    install_crash_handlers,
    report_exception,
    reset_crash_handlers_for_tests,
)
from app.observability.logging_config import (
    JsonLogFormatter,
    RedactingTextFormatter,
    configure_logging,
)
from app.observability.metrics import (
    DEFAULT_BUCKETS,
    MetricsRegistry,
    adjust_gauge,
    increment_counter,
    observe_histogram,
    registry,
    render_prometheus,
    reset,
    set_gauge,
    snapshot,
    timer,
)
from app.observability.middleware import register_observability_middleware
from app.observability.tracing import Span, span, traced

__all__ = [
    "context",
    "correlation_snapshot",
    "get_request_id",
    "get_span_id",
    "get_trace_id",
    "new_span_id",
    "new_trace_id",
    "set_request_id",
    "set_span_id",
    "set_trace_id",
    "configure_logging",
    "log_best_effort_failure",
    "JsonLogFormatter",
    "RedactingTextFormatter",
    "DEFAULT_BUCKETS",
    "MetricsRegistry",
    "adjust_gauge",
    "increment_counter",
    "observe_histogram",
    "registry",
    "render_prometheus",
    "reset",
    "set_gauge",
    "snapshot",
    "timer",
    "Span",
    "span",
    "traced",
    "install_crash_handlers",
    "report_exception",
    "reset_crash_handlers_for_tests",
    "register_observability_middleware",
]
