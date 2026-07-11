from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_chat import router as chat_router
from app.api.routes_runs import router as runs_router
from app.commerce.activation import ActivationError, enforce_activation_rate_limit, initialize_activation_db
from app.core import db
from app.core.errors import register_error_handlers
from app.core.schemas import MAX_USER_MESSAGE_CHARS
from app.security.api_request_limits import (
    enforce_chat_run_request_guard,
    enforce_chat_run_request_limit,
    guarded_api_endpoint_from_path,
)
from app.security.middleware import register_security_middleware
from app.services import run_service_background, task_pool


class _StubRequest:
    def __init__(self, host: str = "127.0.0.1", *, path: str = "/api/chat", method: str = "POST") -> None:
        self.client = type("Client", (), {"host": host})()
        self.method = method
        self.url = type("URL", (), {"path": path})()
        self.headers: dict[str, str] = {}


def test_activation_rate_limit_is_shared_across_connections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    initialize_activation_db(db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_WINDOW_SECONDS", "300")
    scope = "client-a"
    now = 1_700_000_000.0

    for index in range(3):
        enforce_activation_rate_limit(scope, now=now + index, db_path=db_path)

    with pytest.raises(ActivationError) as excinfo:
        enforce_activation_rate_limit(scope, now=now + 3, db_path=db_path)

    assert excinfo.value.code == "activation_rate_limited"
    assert excinfo.value.status_code == 429


def test_activation_rate_limit_serializes_concurrent_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "activation.sqlite"
    initialize_activation_db(db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_WINDOW_SECONDS", "300")
    scope = "concurrent-client"
    now = 1_700_000_100.0

    def _attempt(offset: float) -> str | None:
        try:
            enforce_activation_rate_limit(scope, now=now + offset, db_path=db_path)
        except ActivationError as exc:
            return exc.code
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(_attempt, [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]))

    assert results.count(None) == 3
    assert results.count("activation_rate_limited") == 3


def test_chat_request_rejects_oversized_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(chat_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "x" * (MAX_USER_MESSAGE_CHARS + 1), "mode": "efficiency"},
    )

    assert response.status_code == 422


def test_run_request_rejects_oversized_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(runs_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/runs",
        json={"message": "x" * (MAX_USER_MESSAGE_CHARS + 1), "mode": "efficiency"},
    )

    assert response.status_code == 422


def test_chat_run_rate_limit_rejects_burst(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    request = _StubRequest()
    request.headers = {"content-length": "32"}

    enforce_chat_run_request_limit(request, endpoint="chat")
    enforce_chat_run_request_limit(request, endpoint="chat")

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_limit(request, endpoint="chat")

    assert getattr(excinfo.value, "status_code", None) == 429


def test_chat_run_concurrency_limit_rejects_when_pool_is_full(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "2")
    db.init_db()
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    request = _StubRequest(path="/api/runs")
    request.headers = {"content-length": "32"}

    pending = concurrent.futures.Future()
    pool._running["task_busy"] = pending  # noqa: SLF001 - test-only slot fill
    run_service_background.track_active_run("run_busy", pending)

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_limit(request, endpoint="runs")

    assert getattr(excinfo.value, "status_code", None) == 429
    assert "in-flight" in str(getattr(excinfo.value, "detail", "")).lower()

    pending.set_result(None)
    run_service_background.untrack_active_run("run_busy")


def test_chat_run_guard_rejects_oversized_content_length(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_BODY_BYTES", "32")
    db.init_db()
    request = _StubRequest()
    request.headers = {"content-length": "64"}

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_guard(request)

    assert getattr(excinfo.value, "status_code", None) == 413


def test_chat_run_guard_rejects_missing_content_length(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    request = _StubRequest()

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_guard(request)

    assert getattr(excinfo.value, "status_code", None) == 411


def test_chat_run_middleware_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_BODY_BYTES", "32")
    db.init_db()
    app = FastAPI()
    register_error_handlers(app)
    register_security_middleware(app)
    app.include_router(chat_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        content=b"x" * 64,
        headers={"content-type": "application/json", "content-length": "64"},
    )

    assert response.status_code == 413


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/chat", "chat"),
        ("/api/runs", "runs"),
        ("/api/documents/parse", "documents"),
        ("/api/settings", "settings"),
        ("/api/settings/confirm-sensitive-change", "settings"),
        ("/api/settings/permission-policy/rules", "settings"),
        ("/api/settings/onnx/warmup", "settings"),
        ("/api/schedules", "schedules"),
        ("/api/schedules/job-1/enable", "schedules"),
        ("/api/approvals/appr-1/approve", "approvals"),
        ("/api/approvals/appr-1/reject", "approvals"),
        ("/api/tasks/task-1/resume", "tasks"),
        ("/api/tasks/task-1/pause", "tasks"),
        ("/api/mobile/tasks", "tasks"),
        ("/api/mobile/tasks/task-1/follow-up", "tasks"),
        ("/api/settings/onnx/test-generate", None),
        ("/api/documents", None),
        ("/api/mobile/approvals/appr-1/approve", None),
    ],
)
def test_guarded_api_endpoint_from_path(path: str, expected: str | None) -> None:
    assert guarded_api_endpoint_from_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/parse",
        "/api/settings",
        "/api/schedules",
        "/api/approvals/appr-1/approve",
        "/api/tasks/task-1/resume",
        "/api/mobile/tasks",
    ],
)
def test_guarded_post_endpoints_require_content_length(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    request = _StubRequest(path=path)

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_guard(request)

    assert getattr(excinfo.value, "status_code", None) == 411


def test_settings_test_post_skips_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "1")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    request = _StubRequest(path="/api/settings/onnx/test-generate")

    enforce_chat_run_request_guard(request)


def test_get_requests_skip_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "1")
    db.init_db()
    request = _StubRequest(path="/api/settings/llm/health", method="GET")

    enforce_chat_run_request_guard(request)


@pytest.mark.parametrize(
    ("path", "endpoint"),
    [
        ("/api/documents/ask", "documents"),
        ("/api/settings/permission-policy/confirm-relaxation", "settings"),
        ("/api/schedules/job-1/enable", "schedules"),
        ("/api/approvals/appr-1/reject", "approvals"),
        ("/api/tasks/task-1/resume", "tasks"),
        ("/api/mobile/tasks/task-1/follow-up", "tasks"),
    ],
)
def test_guarded_post_rate_limit_rejects_burst(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    endpoint: str,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    request = _StubRequest(path=path)
    request.headers = {"content-length": "32"}

    enforce_chat_run_request_limit(request, endpoint=endpoint)
    enforce_chat_run_request_limit(request, endpoint=endpoint)

    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_limit(request, endpoint=endpoint)

    assert getattr(excinfo.value, "status_code", None) == 429


def test_guarded_post_rate_limits_use_distinct_scopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "1")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "0")
    db.init_db()
    headers = {"content-length": "32"}

    documents = _StubRequest(path="/api/documents/parse")
    documents.headers = headers
    settings = _StubRequest(path="/api/settings")
    settings.headers = headers

    enforce_chat_run_request_limit(documents, endpoint="documents")
    enforce_chat_run_request_limit(settings, endpoint="settings")


def test_task_execution_admission_shares_the_global_inflight_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_CHAT_RUN_MAX_INFLIGHT", "1")
    db.init_db()
    pool = task_pool.reset_pool_for_tests(max_concurrent=1)
    pending = concurrent.futures.Future()
    pool._running["task_busy"] = pending  # noqa: SLF001 - test-only slot fill

    resume = _StubRequest(path="/api/tasks/task-1/resume")
    resume.headers = {"content-length": "32"}
    with pytest.raises(Exception) as excinfo:
        enforce_chat_run_request_guard(resume)
    assert getattr(excinfo.value, "status_code", None) == 429

    # Pressure-relieving controls must remain available while admissions are
    # closed, otherwise a saturated service cannot be paused or cancelled.
    pause = _StubRequest(path="/api/tasks/task-1/pause")
    pause.headers = {"content-length": "32"}
    enforce_chat_run_request_guard(pause)
    pending.set_result(None)
    task_pool.reset_pool_for_tests()
