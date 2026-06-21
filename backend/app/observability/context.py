"""Correlation context for observability.

Request, trace, and span identifiers are stored in :class:`contextvars.ContextVar`
so they propagate across ``await`` boundaries within a single logical request
without leaking between concurrent requests.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Dict, Optional

_request_id: ContextVar[Optional[str]] = ContextVar("lengrvis_request_id", default=None)
_trace_id: ContextVar[Optional[str]] = ContextVar("lengrvis_trace_id", default=None)
_span_id: ContextVar[Optional[str]] = ContextVar("lengrvis_span_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def get_request_id() -> Optional[str]:
    return _request_id.get()


def set_request_id(value: Optional[str]) -> Token:
    return _request_id.set(value)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def set_trace_id(value: Optional[str]) -> Token:
    return _trace_id.set(value)


def reset_trace_id(token: Token) -> None:
    _trace_id.reset(token)


def get_span_id() -> Optional[str]:
    return _span_id.get()


def set_span_id(value: Optional[str]) -> Token:
    return _span_id.set(value)


def reset_span_id(token: Token) -> None:
    _span_id.reset(token)


def correlation_snapshot() -> Dict[str, str]:
    """Return the currently-set correlation IDs as a plain dict."""

    snap: Dict[str, str] = {}
    request_id = _request_id.get()
    trace_id = _trace_id.get()
    span_id = _span_id.get()
    if request_id:
        snap["request_id"] = request_id
    if trace_id:
        snap["trace_id"] = trace_id
    if span_id:
        snap["span_id"] = span_id
    return snap
