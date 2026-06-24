from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import db
from app.core.schemas import Approval, Plan, PlanStep, StepStatus, Task, TaskStatus, Wakeup
from app.guardian import create_guardian_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import StepPhase
from app.orchestration.task_phase import TaskPhase
from app.security import mobile_jwt
from app.security.desktop_api import DESKTOP_API_TOKEN_HEADER
from app.security.mobile_jwt import (
    MOBILE_AUTH_WS_PROTOCOL_PREFIX,
    REMOTE_INPUT_SCOPE,
    decode_mobile_token,
    issue_mobile_token,
)
from app.security.sensitive_confirmation import create_settings_confirmation
from app.services import guardian_scheduler, mobile_pairing_service, scheduler_service, wakeup_service
from app.services.approval_event_service import publish_approval_created
from app.services.guardian_scheduler import GuardianScheduler
from app.services.scheduler_service import Scheduler, _utc_now
from app.services.settings_service import update_settings
from tls_test_material import write_lan_tls_material


async def _backend_unavailable() -> bool:
    return False


async def _backend_available() -> bool:
    return True


def _require_desktop_api_token(
    monkeypatch: pytest.MonkeyPatch,
    token: str = "guardian-desktop-secret",  # noqa: S107
) -> str:
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", token)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    return token


def test_guardian_health_is_lightweight(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    import app.guardian as guardian_module

    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("guardian health must not probe local LLMs")

    monkeypatch.setattr(guardian_module, "decode_mobile_token", fail_if_called)

    with TestClient(create_guardian_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "guardian"


def test_guardian_pairing_routes_are_single_sourced_from_routes_pair(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    from app.api import routes_pair

    pair_endpoints = {getattr(route, "endpoint", None) for route in routes_pair.router.routes}
    pair_endpoints.discard(None)
    app = create_guardian_app()
    # Match by endpoint identity rather than by route.path string. include_router
    # preserves the original endpoint function objects, while the exact stored
    # representation of a prefixed route.path in app.routes varies across the
    # Starlette/FastAPI versions resolved by the unpinned cross-platform job.
    # Endpoint identity is the version-stable single-sourcing guarantee.
    guardian_pair_routes = [route for route in app.routes if getattr(route, "endpoint", None) in pair_endpoints]

    assert guardian_pair_routes, "guardian must expose pairing endpoints"
    for route in guardian_pair_routes:
        assert route.endpoint in pair_endpoints, f"guardian route {route.path} is not the shared routes_pair handler"
    # The shared pairing routes must be mounted under the /api prefix. Assert the
    # prefix via the OpenAPI schema (FastAPI's stable public contract) instead of
    # raw route.path string inspection, which is what regressed on POSIX.
    pair_paths = [path for path in app.openapi()["paths"] if path.startswith("/api/pair")]
    assert pair_paths, "guardian must expose pairing endpoints under /api"


def test_guardian_rejects_remote_desktop_websocket_proxy(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    db.init_db()

    client = TestClient(create_guardian_app(), client=("192.168.1.44", 50100))
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/tasks/task_guardian_proxy"):
            raise AssertionError("Remote Guardian desktop websocket proxy should be blocked")

    assert exc_info.value.code == 1008


def test_import_backend_main_keeps_full_backend_lazy():
    import sys

    sys.modules.pop("app.main", None)
    import backend.main as backend_entry

    assert backend_entry.app.title == "Lengrvis Guardian Backend"
    assert "app.main" not in sys.modules


def test_guardian_full_backend_probe_uses_runtime_status(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    token = _require_desktop_api_token(monkeypatch)
    db.init_db()

    import app.services.guardian_runtime as guardian_runtime

    requested: list[tuple[str, dict[str, str]]] = []

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            requested.append((url, dict(kwargs.get("headers") or {})))
            return FakeResponse()

    monkeypatch.setattr(guardian_runtime.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(guardian_runtime.runtime._is_full_backend_healthy()) is True
    assert requested == [
        (
            f"{guardian_runtime.FULL_BACKEND_URL}/api/runtime/status",
            {DESKTOP_API_TOKEN_HEADER: token},
        )
    ]


def test_guardian_idle_recycle_treats_active_runs_as_busy(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    token = _require_desktop_api_token(monkeypatch)
    db.init_db()

    import app.services.guardian_runtime as guardian_runtime

    requested_headers: list[dict[str, str]] = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"activeRunIds": ["run_active"]}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

        async def get(self, url: str, **kwargs):  # noqa: ANN003, ARG002
            requested_headers.append(dict(kwargs.get("headers") or {}))
            return FakeResponse()

    monkeypatch.setattr(guardian_runtime.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(guardian_runtime.runtime._full_backend_has_active_runs()) is True
    assert requested_headers == [{DESKTOP_API_TOKEN_HEADER: token}]


def test_guardian_runtime_internal_http_calls_send_desktop_token(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    token = _require_desktop_api_token(monkeypatch)
    db.init_db()

    import app.services.guardian_runtime as guardian_runtime

    calls: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        status_code = 200
        content = b"{}"
        headers = {"content-type": "application/json"}

        def json(self):
            return {"activeRunIds": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

        async def get(self, url: str, **kwargs):  # noqa: ANN003
            calls.append(("get", url, dict(kwargs.get("headers") or {})))
            return FakeResponse()

        async def post(self, url: str, **kwargs):  # noqa: ANN003
            calls.append(("post", url, dict(kwargs.get("headers") or {})))
            return FakeResponse()

        async def request(self, method: str, url: httpx.URL, **kwargs):  # noqa: ANN003
            calls.append((method.lower(), str(url), dict(kwargs.get("headers") or {})))
            return FakeResponse()

    monkeypatch.setattr(guardian_runtime.httpx, "AsyncClient", FakeClient)

    guardian = guardian_runtime.GuardianRuntime()
    guardian.shell_mode = "foreground"

    async def runner() -> httpx.Response:
        await guardian._notify_full_foreground()
        await guardian._notify_full_background()
        return await guardian.proxy(
            "GET",
            "/api/tasks",
            headers={
                "Host": "127.0.0.1:8000",
                "Content-Length": "10",
                "X-Lengrvis-Desktop-Token": "stale-token",
            },
        )

    response = asyncio.run(runner())

    assert response.status_code == 200
    assert [call[0] for call in calls] == ["post", "post", "get", "get"]
    assert all(headers.get(DESKTOP_API_TOKEN_HEADER) == token for _method, _url, headers in calls)
    proxy_headers = calls[-1][2]
    assert all(key.lower() not in {"host", "content-length"} for key in proxy_headers)
    assert sum(1 for key in proxy_headers if key.lower() == DESKTOP_API_TOKEN_HEADER) == 1
    assert "stale-token" not in proxy_headers.values()


def test_guardian_scheduler_creates_wakeup_without_orchestrator(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    guardian_scheduler._scheduler = None

    def fail_if_orchestrator_imported(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("guardian scheduler must not execute orchestrator work")

    monkeypatch.setattr("app.agents.orchestrator_agent.OrchestratorAgent", fail_if_orchestrator_imported)

    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    guardian = GuardianScheduler(full_backend_available=_backend_unavailable)

    async def runner():
        return await guardian.tick(now=_utc_now() + timedelta(days=1))

    fired = asyncio.run(runner())

    assert schedule.id in fired
    refreshed = Scheduler().get(schedule.id)
    assert refreshed is not None
    assert refreshed.last_status == "waiting_user_confirmation"
    wakeups = db.fetch_many("wakeups", "source_id = ?", (schedule.id,), limit=10)
    assert len(wakeups) == 1
    assert wakeups[0]["goal"] == "scan downloads"
    assert wakeups[0]["status"] == "pending"


def test_guardian_scheduler_skips_when_full_backend_is_available(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    guardian_scheduler._scheduler = None
    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    guardian = GuardianScheduler(full_backend_available=_backend_available)

    async def runner():
        return await guardian.tick(now=_utc_now() + timedelta(days=1))

    fired = asyncio.run(runner())

    assert fired == []
    refreshed = Scheduler().get(schedule.id)
    assert refreshed is not None
    assert refreshed.last_status == ""
    wakeups = db.fetch_many("wakeups", "source_id = ?", (schedule.id,), limit=10)
    assert wakeups == []


def test_guardian_scheduler_concurrent_ticks_create_one_wakeup(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    guardian_scheduler._scheduler = None
    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    far_future = _utc_now() + timedelta(days=1)
    original_fetch_many = db.fetch_many
    barrier = threading.Barrier(2)

    def synchronized_fetch_many(table: str, where: str = "", args: tuple = (), limit: int = 200):
        rows = original_fetch_many(table, where, args, limit)
        if table == "scheduled_tasks" and where == "enabled = 1":
            barrier.wait(timeout=5)
        return rows

    monkeypatch.setattr(db, "fetch_many", synchronized_fetch_many)
    results: list[list[str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run_tick() -> None:
        async def runner() -> list[str]:
            return await GuardianScheduler(full_backend_available=_backend_unavailable).tick(now=far_future)

        try:
            fired = asyncio.run(runner())
            with lock:
                results.append(fired)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    thread_a = threading.Thread(target=run_tick)
    thread_b = threading.Thread(target=run_tick)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    fired_ids = [schedule_id for fired in results for schedule_id in fired]
    assert fired_ids == [schedule.id]
    wakeups = db.fetch_many("wakeups", "source_id = ?", (schedule.id,), limit=10)
    assert len(wakeups) == 1


def test_schedule_wakeup_creation_is_atomic_under_concurrent_callers(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    due_at = _utc_now().isoformat()
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []
    notifications: list[tuple[str, str]] = []
    lock = threading.Lock()

    def fake_notify(title: str, body: str, **kwargs) -> None:  # noqa: ANN003
        with lock:
            notifications.append((title, body))

    monkeypatch.setattr(wakeup_service.notification_service, "notify", fake_notify)

    def create() -> None:
        try:
            barrier.wait(timeout=5)
            wakeup = wakeup_service.create_schedule_wakeup(schedule, due_at=due_at)
            with lock:
                results.append(wakeup.id)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    thread_a = threading.Thread(target=create)
    thread_b = threading.Thread(target=create)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    assert len(results) == 2
    assert len(set(results)) == 1
    wakeups = db.fetch_many("wakeups", "source_id = ?", (schedule.id,), limit=10)
    assert len(wakeups) == 1
    assert notifications == [("Scheduled task ready", "scan downloads")]


def test_wakeup_decision_is_atomic_under_concurrent_submitters(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    wakeup = Wakeup(goal="scan downloads", mode="hybrid")
    db.upsert_model("wakeups", wakeup)
    results: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    def decide(status: str) -> None:
        barrier.wait(timeout=5)
        try:
            if status == "approved":
                wakeup_service.approve_wakeup(wakeup.id)
            else:
                wakeup_service.reject_wakeup(wakeup.id)
            results.append((status, "won"))
        except Exception as exc:  # noqa: BLE001
            results.append((status, f"lost:{getattr(exc, 'status_code', '')}"))

    approve = threading.Thread(target=decide, args=("approved",))
    reject = threading.Thread(target=decide, args=("rejected",))
    approve.start()
    reject.start()
    approve.join(timeout=5)
    reject.join(timeout=5)

    assert sorted(result.split(":", 1)[0] for _status, result in results) == ["lost", "won"]
    assert any(result == "lost:409" for _status, result in results)
    stored = db.fetch_one("wakeups", wakeup.id)
    assert stored is not None
    assert stored["status"] in {"approved", "rejected"}
    assert stored["decided_at"]


def test_mobile_wakeup_payload_redacts_sensitive_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_wakeup", device_name="Wakeup Phone")
    token = issue_mobile_token(device_id="mobile_wakeup", device_name="Wakeup Phone")
    wakeup = Wakeup(
        title="Scheduled task token=title-secret-1234567890",
        body="Run Authorization Bearer body-secret-1234567890",
        goal="Use api_key=goal-secret-1234567890",
        error="password=error-secret-1234567890",
    )
    db.upsert_model("wakeups", wakeup)

    with TestClient(create_guardian_app()) as client:
        desktop_pending = client.get("/api/wakeups/pending")
        mobile_pending = client.get(
            "/api/mobile/wakeups/pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        mobile_reject = client.post(
            f"/api/mobile/wakeups/{wakeup.id}/reject",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert desktop_pending.status_code == 200
    assert mobile_pending.status_code == 200
    assert mobile_reject.status_code == 200
    payload_text = json.dumps([desktop_pending.json(), mobile_pending.json(), mobile_reject.json()], ensure_ascii=False)
    assert "title-secret-1234567890" not in payload_text
    assert "body-secret-1234567890" not in payload_text
    assert "goal-secret-1234567890" not in payload_text
    assert "error-secret-1234567890" not in payload_text
    assert "[REDACTED]" in payload_text


def test_guardian_wakeup_approve_returns_refreshed_failed_payload(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("wakeups", wakeup)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_execute_wakeup(_wakeup):
        raise RuntimeError("backend execute failed")

    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(routes_guardian, "_execute_wakeup", fake_execute_wakeup)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/wakeups/{wakeup.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["wakeup"]["status"] == "failed"
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "failed"
    assert stored.error == "backend execute failed"


def test_guardian_wakeup_run_response_without_run_id_fails(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("wakeups", wakeup)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/wakeups/{wakeup.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["wakeup"]["status"] == "failed"
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "failed"
    assert stored.run_id == ""
    assert "run_id" in stored.error


def test_guardian_wakeup_run_failure_redacts_persisted_upstream_error(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("wakeups", wakeup)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 502
        text = "upstream token=upstream-secret-1234567890"

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/wakeups/{wakeup.id}/approve")

    assert response.status_code == 503
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert "upstream-secret-1234567890" not in payload_text
    assert "[REDACTED]" in payload_text
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "failed"
    assert "upstream-secret-1234567890" not in stored.error
    assert "[REDACTED]" in stored.error


def test_guardian_wakeup_approve_returns_completed_payload_with_run_id(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("wakeups", wakeup)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 200
        text = '{"run_id":"osrun_wakeup_created"}'

        def json(self):
            return {"run_id": "osrun_wakeup_created"}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/wakeups/{wakeup.id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["run_id"] == "osrun_wakeup_created"
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "completed"
    assert stored.run_id == "osrun_wakeup_created"
    assert stored.error == ""


def test_guardian_approval_and_wakeup_internal_posts_send_desktop_token(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    token = _require_desktop_api_token(monkeypatch)
    db.init_db()
    approval = Approval(task_id="task_guardian_internal_token", step_id="step_1", message="Approve")
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("approvals", approval, status=approval.status)
    db.upsert_model("wakeups", wakeup)

    import app.api.routes_guardian as routes_guardian

    posts: list[tuple[str, dict[str, str], dict[str, Any] | None]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload, ensure_ascii=False)

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, url: str, **kwargs):  # noqa: ANN003
            posts.append((url, dict(kwargs.get("headers") or {}), kwargs.get("json")))
            if url.endswith("/api/runs"):
                return FakeResponse(200, {"run_id": "osrun_internal_token"})
            return FakeResponse(200, {"ok": True})

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    asyncio.run(routes_guardian._wake_full_backend_for_approval(approval))
    asyncio.run(routes_guardian._execute_wakeup(wakeup))

    assert [url.rsplit("/", 1)[-1] for url, _headers, _json in posts] == ["continue", "runs"]
    assert all(headers == {DESKTOP_API_TOKEN_HEADER: token} for _url, headers, _json in posts)
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "completed"
    assert stored.run_id == "osrun_internal_token"


def test_guardian_mobile_wakeup_reject_returns_refreshed_payload(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_wakeup_guardian", device_name="Wakeup Guardian")
    token = issue_mobile_token(device_id="mobile_wakeup_guardian", device_name="Wakeup Guardian")
    wakeup = Wakeup(title="Wake up", body="Run", goal="Do something")
    db.upsert_model("wakeups", wakeup)

    with TestClient(create_guardian_app()) as client:
        response = client.post(
            f"/api/mobile/wakeups/{wakeup.id}/reject",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    stored = wakeup_service.get_wakeup(wakeup.id)
    assert stored.status == "rejected"


def test_wakeup_approval_requires_enabled_source_schedule(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    wakeup = wakeup_service.create_schedule_wakeup(schedule, due_at=_utc_now().isoformat())
    Scheduler().enable(schedule.id, False)

    with pytest.raises(HTTPException) as exc_info:
        wakeup_service.approve_wakeup(wakeup.id)

    assert exc_info.value.status_code == 409
    assert "disabled" in str(exc_info.value.detail)
    rejected = wakeup_service.reject_wakeup(wakeup.id)
    assert rejected.status == "rejected"


def test_wakeup_approval_requires_existing_source_schedule(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    wakeup = wakeup_service.create_schedule_wakeup(schedule, due_at=_utc_now().isoformat())
    with db.connect() as conn:
        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (schedule.id,))

    with pytest.raises(HTTPException) as exc_info:
        wakeup_service.approve_wakeup(wakeup.id)

    assert exc_info.value.status_code == 409
    assert "no longer available" in str(exc_info.value.detail)
    rejected = wakeup_service.reject_wakeup(wakeup.id)
    assert rejected.status == "rejected"


def test_guardian_approval_does_not_execute_step(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_approval",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_approval",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    async def fake_wake_full_backend(_approval):
        approved = Approval.model_validate(db.fetch_one("approvals", approval.id))
        approved.consumed_at = "2026-06-01T00:00:00+00:00"
        db.upsert_model("approvals", approved, status=approved.status)
        return approved

    monkeypatch.setattr(routes_guardian, "_wake_full_backend_for_approval", fake_wake_full_backend)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 200
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    refreshed_plan = Plan.model_validate(db.fetch_one("plans", plan.id))
    assert refreshed_plan.steps[0].status == "waiting_user_approval"


def test_guardian_approval_surfaces_full_backend_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_approval_failure",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_approval_failure",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        raise TimeoutError("Full backend did not become ready within 30 seconds.")

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["approval_id"] == approval.id
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["status"] == "approved"
    assert response.json()["detail"]["approval"]["consumed_at"] is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"


def test_guardian_approval_surfaces_full_backend_continue_transport_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_approval_continue_transport_failure",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_approval_continue_transport_failure",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            raise httpx.ConnectError("runtime continue disconnected token=transport-secret-1234567890")

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == "Full backend is not ready to continue the approval."
    assert response.json()["detail"]["approval_id"] == approval.id
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["status"] == "approved"
    assert response.json()["detail"]["approval"]["consumed_at"] is None
    assert "runtime continue disconnected" in response.json()["detail"]["error"]
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert "transport-secret-1234567890" not in payload_text
    assert "[REDACTED]" in response.json()["detail"]["error"]
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["consumed_at"] is None


def test_guardian_mobile_approval_surfaces_full_backend_continue_transport_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_continue_transport_failure", device_name="Mobile")
    token = issue_mobile_token(device_id="mobile_continue_transport_failure", device_name="Mobile")
    task = Task(
        id="task_guardian_mobile_continue_transport_failure",
        user_goal="approve later from mobile",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_mobile_continue_transport_failure",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve read",
        allowed_device_ids=["mobile_continue_transport_failure"],
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            raise httpx.ConnectError("mobile runtime continue disconnected token=mobile-transport-secret-1234567890")

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(
            f"/api/mobile/approvals/{approval.id}/approve",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == "Full backend is not ready to continue the approval."
    assert response.json()["detail"]["approval_id"] == approval.id
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["status"] == "approved"
    assert response.json()["detail"]["approval"]["consumed_at"] is None
    assert "mobile runtime continue disconnected" in response.json()["detail"]["error"]
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert "mobile-transport-secret-1234567890" not in payload_text
    assert "[REDACTED]" in response.json()["detail"]["error"]
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["consumed_at"] is None


def test_guardian_approval_wraps_non_json_full_backend_continue_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_non_json_continue_failure",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_non_json_continue_failure",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve token=approval-secret-1234567890",
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 502
        text = "upstream token=upstream-secret-1234567890"

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "Full backend did not continue the approval."
    assert response.json()["detail"]["approval_id"] == approval.id
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["status"] == "approved"
    assert response.json()["detail"]["approval"]["consumed_at"] is None
    assert "approval-secret-1234567890" not in payload_text
    assert "upstream-secret-1234567890" not in payload_text
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["consumed_at"] is None


def test_guardian_approval_redacts_json_full_backend_continue_failure_detail(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_json_continue_failure_redaction",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_json_continue_failure_redaction",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 503
        text = ""

        def json(self):
            return {
                "detail": {
                    "message": "Backend failed with token=backend-secret-1234567890",
                    "api_key": "sk-backend-secret-1234567890",
                    "nested": {"authorization": "Bearer backendbearersecret1234567890"},
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 503
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["backend_detail"]["api_key"] == "***"
    assert response.json()["detail"]["backend_detail"]["nested"]["authorization"] == "***"
    assert "backend-secret-1234567890" not in payload_text
    assert "backendbearersecret1234567890" not in payload_text


def test_guardian_approval_redacts_nested_backend_approval_preview(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_backend_approval_preview_redaction",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_backend_approval_preview_redaction",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 503
        text = ""

        def json(self):
            return {
                "detail": {
                    "message": "Backend failed during approved execution.",
                    "approval": {
                        "id": approval.id,
                        "task_id": approval.task_id,
                        "status": "approved",
                        "diff_preview": {
                            "diff_preview": [{"action": "write", "path": "a.txt"}],
                            "_resource_state": [{"path": "a.txt", "sha256": "internal-resource-sha"}],
                        },
                    },
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 503
    assert response.json()["detail"]["backend_detail"]["approval"]["id"] == approval.id
    assert "_resource_state" not in payload_text
    assert "internal-resource-sha" not in payload_text


def test_guardian_approval_can_retry_after_transient_execute_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_approval_retry",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_approval_retry",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    attempts = {"count": 0}

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload, ensure_ascii=False)

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            attempts["count"] += 1
            if attempts["count"] == 1:
                return FakeResponse(
                    409,
                    {"detail": {"message": "Approval is no longer executable.", "approval": {"status": "approved"}}},
                )
            stored = Approval.model_validate(db.fetch_one("approvals", approval.id))
            stored.consumed_at = "2026-06-01T00:00:00+00:00"
            db.upsert_model("approvals", stored, status=stored.status)
            return FakeResponse(200, {"ok": True})

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        first = client.post(f"/api/approvals/{approval.id}/approve")
        second = client.post(f"/api/approvals/{approval.id}/approve")

    assert first.status_code == 409
    assert second.status_code == 200
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"


def test_guardian_approval_requires_full_backend_to_consume_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_unconsumed_approval",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_unconsumed_approval",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN001, ANN002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201, ARG002
            return None

        async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002
            return FakeResponse()

    async def fake_wake_transient(*args, **kwargs):  # noqa: ANN001, ANN002
        return None

    monkeypatch.setattr(routes_guardian.runtime, "wake_transient", fake_wake_transient)
    monkeypatch.setattr(routes_guardian.httpx, "AsyncClient", FakeClient)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 503
    assert response.json()["detail"]["approval"]["id"] == approval.id
    assert response.json()["detail"]["approval"]["status"] == "approved"
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["consumed_at"] is None


def test_guardian_mobile_routes_enforce_device_bound_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_first", device_name="First")
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_second", device_name="Second")
    first_token = issue_mobile_token(device_id="mobile_first", device_name="First")
    second_approval = Approval(
        task_id="task_guardian_second_device",
        step_id="step_1",
        message="Approve second device test",
        allowed_device_ids=["mobile_second"],
    )
    db.upsert_model("approvals", second_approval)

    with TestClient(create_guardian_app()) as client:
        pending_response = client.get(
            "/api/mobile/approvals/pending",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        decision_response = client.post(
            f"/api/mobile/approvals/{second_approval.id}/decision",
            headers={"Authorization": f"Bearer {first_token}"},
            json={"decision": "denied"},
        )

    assert pending_response.status_code == 200
    assert pending_response.json() == []
    assert decision_response.status_code == 403


def test_guardian_mobile_device_list_only_returns_calling_device(monkeypatch, tmp_path: Path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()

    with TestClient(create_guardian_app()) as client:
        first_code = client.post("/api/pair/code").json()["code"]
        first_pair = client.post("/api/pair", json={"code": first_code, "device_name": "First"})
        second_code = client.post("/api/pair/code").json()["code"]
        second_pair = client.post("/api/pair", json={"code": second_code, "device_name": "Second"})
        first_token = first_pair.json()["token"]
        second_token = second_pair.json()["token"]
        first_device_id = decode_mobile_token(first_token)["device_id"]
        second_device_id = decode_mobile_token(second_token)["device_id"]

        response = client.get("/api/mobile/devices", headers={"Authorization": f"Bearer {first_token}"})

    assert response.status_code == 200
    assert [device["device_id"] for device in response.json()["devices"]] == [first_device_id]
    assert second_device_id not in [device["device_id"] for device in response.json()["devices"]]


def test_guardian_mobile_routes_enforce_remote_input_scope(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_approval", device_name="Approval")
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_remote", device_name="Remote")
    approval_token = issue_mobile_token(device_id="mobile_approval", device_name="Approval")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_remote")
    remote_token = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_remote", "device_name": "Remote"},
    )["token"]
    approval = Approval(
        task_id="task_guardian_remote_input",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input",
        source_device_id="mobile_remote",
        source_grant_id=grant["grant_id"],
        allowed_device_ids=["mobile_remote"],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    with TestClient(create_guardian_app()) as client:
        blocked_response = client.post(
            f"/api/mobile/approvals/{approval.id}/decision",
            headers={"Authorization": f"Bearer {approval_token}"},
            json={"decision": "denied"},
        )
        allowed_response = client.post(
            f"/api/mobile/approvals/{approval.id}/decision",
            headers={"Authorization": f"Bearer {remote_token}"},
            json={"decision": "denied"},
        )

    assert blocked_response.status_code == 403
    assert allowed_response.status_code == 200


def test_guardian_mobile_decision_rejects_invalid_decision_without_mutating_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_invalid_decision", device_name="Invalid Decision")
    token = issue_mobile_token(device_id="mobile_invalid_decision", device_name="Invalid Decision")
    approval = Approval(
        task_id="task_guardian_invalid_decision",
        step_id="step_1",
        message="Approve valid decision only",
        allowed_device_ids=["mobile_invalid_decision"],
    )
    db.upsert_model("approvals", approval)

    with TestClient(create_guardian_app()) as client:
        response = client.post(
            f"/api/mobile/approvals/{approval.id}/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "maybe"},
        )

    assert response.status_code == 422
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_guardian_mobile_reject_denies_step_and_cancels_task(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_reject_lifecycle", device_name="Reject Lifecycle")
    token = issue_mobile_token(device_id="mobile_reject_lifecycle", device_name="Reject Lifecycle")
    task = Task(user_goal="Reject lifecycle", status=TaskStatus.WAITING_USER_APPROVAL)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="test.reject",
        description="Reject me",
        status=StepStatus.WAITING_USER_APPROVAL,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Reject lifecycle approval",
        allowed_device_ids=["mobile_reject_lifecycle"],
    )
    db.upsert_model("approvals", approval)

    with TestClient(create_guardian_app()) as client:
        response = client.post(
            f"/api/mobile/approvals/{approval.id}/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": "denied"},
        )

    refreshed_task = Task.model_validate(db.fetch_one("tasks", task.id))
    refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert response.status_code == 200
    assert refreshed_approval.status == "rejected"
    assert refreshed_plan.steps[0].status == StepStatus.DENIED
    assert refreshed_task.status == TaskStatus.CANCELLED
    assert refreshed_task.final_summary == "Approval was rejected by the user."


def test_guardian_desktop_reject_denies_step_and_cancels_task(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    task = Task(user_goal="Desktop reject lifecycle", status=TaskStatus.WAITING_USER_APPROVAL)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="test.reject",
        description="Reject me from desktop",
        status=StepStatus.WAITING_USER_APPROVAL,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Desktop reject lifecycle approval",
    )
    db.upsert_model("approvals", approval)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/reject")

    refreshed_task = Task.model_validate(db.fetch_one("tasks", task.id))
    refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert response.status_code == 200
    assert refreshed_approval.status == "rejected"
    assert refreshed_plan.steps[0].status == StepStatus.DENIED
    assert refreshed_task.status == TaskStatus.CANCELLED
    assert refreshed_task.final_summary == "Approval was rejected by the user."


def test_guardian_mobile_approval_preserves_remote_input_denial_reason(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_remote_missing_grant", device_name="Remote")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_remote_missing_grant")
    remote_token = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_remote_missing_grant", "device_name": "Remote"},
    )["token"]
    approval = Approval(
        task_id="task_guardian_remote_input_missing_grant",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input without grant binding",
        source_device_id="mobile_remote_missing_grant",
        allowed_device_ids=["mobile_remote_missing_grant"],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    with TestClient(create_guardian_app()) as client:
        response = client.post(
            f"/api/mobile/approvals/{approval.id}/decision",
            headers={"Authorization": f"Bearer {remote_token}"},
            json={"decision": "approved"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Remote input approval is missing a grant binding."
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_guardian_mobile_websocket_streams_grant_events_and_closes_on_revoke(monkeypatch, tmp_path: Path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    _enable_remote_desktop()

    with TestClient(create_guardian_app()) as client:
        code = client.post("/api/pair/code").json()["code"]
        pair = client.post("/api/pair", json={"code": code, "device_name": "Guardian Phone"})
        token = pair.json()["token"]
        device_id = decode_mobile_token(token)["device_id"]

        with client.websocket_connect(
            "/ws/mobile/approvals",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ) as websocket:
            assert websocket.receive_json()["type"] == "connected"

            grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
            created = websocket.receive_json()
            assert created["type"] == "remote_input_grant_created"
            assert created["device_id"] == device_id
            assert created["grant"]["id"] == grant["grant_id"]

            client.delete(f"/api/pair/devices/{device_id}/remote-input-grants/{grant['grant_id']}")
            revoked_grant = websocket.receive_json()
            assert revoked_grant["type"] == "remote_input_grant_revoked"
            assert revoked_grant["device_id"] == device_id
            assert revoked_grant["grant"]["id"] == grant["grant_id"]
            assert revoked_grant["grant"]["status"] == "revoked"

            revoked_device = client.delete(f"/api/pair/devices/{device_id}").json()
            revoked_event = websocket.receive_json()
            assert revoked_event["type"] == "mobile_device_revoked"
            assert revoked_event["device_id"] == device_id
            assert revoked_event["device"]["status"] == "revoked"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()

    assert revoked_device["status"] == "revoked"
    assert exc_info.value.code == 1008


def test_guardian_mobile_websocket_closes_after_token_expires(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    guardian_scheduler._scheduler = None
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="guardian_expiring_ws", device_name="Guardian Phone")
    token = issue_mobile_token(device_id="guardian_expiring_ws", device_name="Guardian Phone", expires_in_seconds=60)

    class ExpiredTokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=120)

    with TestClient(create_guardian_app()) as client:
        with client.websocket_connect(
            "/ws/mobile/approvals",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ) as websocket:
            assert websocket.receive_json()["type"] == "connected"
            monkeypatch.setattr(mobile_jwt, "datetime", ExpiredTokenClock)
            approval = Approval(task_id="task_guardian_ws_expired", step_id="step_1", message="Expired token event")
            db.upsert_model("approvals", approval)
            publish_approval_created(approval)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_json()

    assert exc_info.value.code == 1008


def _enable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)


def _enable_lan_tls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cert, key = write_lan_tls_material(tmp_path)
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))
