"""SQLite-backed rate and in-flight guards for guarded API POST entrypoints."""

from __future__ import annotations

import os
import sqlite3
import time

from fastapi import HTTPException, Request

from app.core import db
from app.services import run_service_background
from app.services.task_pool import get_pool

CHAT_RUN_RATE_LIMIT_MAX_ENV_VAR = "LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX"
CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR = "LENGRVIS_CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS"
CHAT_RUN_MAX_INFLIGHT_ENV_VAR = "LENGRVIS_CHAT_RUN_MAX_INFLIGHT"
CHAT_RUN_MAX_BODY_BYTES_ENV_VAR = "LENGRVIS_CHAT_RUN_MAX_BODY_BYTES"

_DEFAULT_RATE_LIMIT_MAX = 30
_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60
_DEFAULT_MAX_INFLIGHT = 4
_DEFAULT_MAX_BODY_BYTES = 65536

_SETTINGS_POST_RATE_LIMIT_EXEMPT = frozenset(
    {
        "/api/settings/test-llm-provider",
        "/api/settings/onnx/test-generate",
        "/api/settings/onnx/test-embedding",
        "/api/settings/onnx/test-ocr",
        "/api/settings/onnx/test-image-embedding",
    }
)


def guarded_api_endpoint_from_path(path: str) -> str | None:
    """Return the rate-limit scope family for guarded POST entrypoints."""
    normalized = str(path or "").rstrip("/") or "/"
    if normalized == "/api/chat":
        return "chat"
    if normalized == "/api/runs":
        return "runs"
    if normalized.startswith("/api/documents/"):
        return "documents"
    if normalized.startswith("/api/schedules"):
        return "schedules"
    if normalized.startswith("/api/approvals/"):
        return "approvals"
    if normalized.startswith("/api/tasks/") or normalized == "/api/mobile/tasks" or normalized.startswith(
        "/api/mobile/tasks/"
    ):
        return "tasks"
    if _is_state_changing_settings_post(normalized):
        return "settings"
    return None


def chat_run_endpoint_from_path(path: str) -> str | None:
    endpoint = guarded_api_endpoint_from_path(path)
    if endpoint in {"chat", "runs"}:
        return endpoint
    return None



def _is_execution_admission_request(path: str, endpoint: str) -> bool:
    """Return whether this POST can start or resume background execution.

    Pause/cancel operations remain available under saturation so callers can
    always relieve pressure; task creation, follow-up, and resume share the
    same global worker budget as chat and run submissions.
    """
    if endpoint in {"chat", "runs"}:
        return True
    if endpoint != "tasks":
        return False
    normalized = str(path or "").rstrip("/") or "/"
    return (
        normalized == "/api/mobile/tasks"
        or normalized.endswith("/resume")
        or normalized.endswith("/follow-up")
    )

def _is_state_changing_settings_post(path: str) -> bool:
    if path == "/api/settings" or path.startswith("/api/settings/"):
        return path not in _SETTINGS_POST_RATE_LIMIT_EXEMPT
    return False


def enforce_chat_run_request_guard(request: Request) -> None:
    """Rate/in-flight/body guards for guarded POST entrypoints (call before body parse)."""
    endpoint = guarded_api_endpoint_from_path(request.url.path)
    if request.method.upper() != "POST" or endpoint is None:
        return
    _raise_if_chat_run_body_too_large(request)
    if _is_execution_admission_request(request.url.path, endpoint):
        _raise_if_chat_run_concurrency_exceeded()
    scope = f"{endpoint}:{_client_scope(request)}"
    _enforce_sqlite_rate_limit(scope)


def _raise_if_chat_run_body_too_large(request: Request) -> None:
    maximum = _env_int(CHAT_RUN_MAX_BODY_BYTES_ENV_VAR, _DEFAULT_MAX_BODY_BYTES)
    if maximum <= 0:
        return
    raw_length = str(request.headers.get("content-length") or "").strip()
    if not raw_length:
        raise HTTPException(status_code=411, detail="Content-Length header is required.")
    try:
        content_length = int(raw_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from None
    if content_length > maximum:
        raise HTTPException(status_code=413, detail="Request body is too large.")


def enforce_chat_run_request_limit(request: Request, *, endpoint: str) -> None:
    """Reject chat/run submissions when rate or in-flight limits are exceeded."""
    _raise_if_chat_run_body_too_large(request)
    _raise_if_chat_run_concurrency_exceeded()
    scope = f"{endpoint}:{_client_scope(request)}"
    _enforce_sqlite_rate_limit(scope)


def _raise_if_chat_run_concurrency_exceeded() -> None:
    maximum = _env_int(CHAT_RUN_MAX_INFLIGHT_ENV_VAR, _DEFAULT_MAX_INFLIGHT)
    if maximum <= 0:
        return
    pool = get_pool()
    status = pool.status()
    inflight = int(status["running_count"]) + int(status["queued_count"]) + len(run_service_background.active_run_ids())
    if inflight >= maximum:
        raise HTTPException(status_code=429, detail="Too many in-flight chat/run requests. Try again later.")


def _enforce_sqlite_rate_limit(scope: str, *, now: float | None = None) -> None:
    current = time.time() if now is None else now
    window = _env_int(CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR, _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
    maximum = _env_int(CHAT_RUN_RATE_LIMIT_MAX_ENV_VAR, _DEFAULT_RATE_LIMIT_MAX)
    if maximum <= 0:
        return
    cutoff = current - max(1, window)
    path = db.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_rate_limit_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM api_request_rate_limits WHERE attempted_at <= ?", (cutoff,))
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM api_request_rate_limits
            WHERE scope = ? AND attempted_at > ?
            """,
            (scope, cutoff),
        ).fetchone()[0]
        if int(count) >= maximum:
            conn.rollback()
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
        conn.execute(
            "INSERT INTO api_request_rate_limits(scope, attempted_at) VALUES (?, ?)",
            (scope, current),
        )
        conn.commit()


def _ensure_rate_limit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_request_rate_limits (
            scope TEXT NOT NULL,
            attempted_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_api_request_rate_limits_scope_time
        ON api_request_rate_limits(scope, attempted_at)
        """
    )


def _client_scope(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return (host or "unknown").strip().lower() or "unknown"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default
