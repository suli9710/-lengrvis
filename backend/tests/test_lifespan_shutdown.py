"""Verify lifespan teardown drains the background TaskPool."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    yield
    # Lifespan teardown runs shutdown_runs(), which flips the module-level
    # "accepting new runs" gate off. In production the process exits next, but
    # in this test session later suites reuse the module, so restore it.
    from app.services.run_service import enter_foreground_runtime

    enter_foreground_runtime()


def test_startup_recovers_crash_orphaned_running_runs():
    import concurrent.futures

    from app.core import db
    from app.core.schemas import Run, RunPhase
    from app.services import run_service

    db.init_db()
    orphan = Run(message="crashed mid-flight", mode="efficiency", phase=RunPhase.RUNNING)
    db.upsert_model("runs", orphan)
    alive = Run(message="still driven by this process", mode="efficiency", phase=RunPhase.RUNNING)
    db.upsert_model("runs", alive)
    live_future: concurrent.futures.Future = concurrent.futures.Future()
    run_service.track_active_run(alive.id, live_future)
    try:
        recovered = run_service.recover_interrupted_runs()
    finally:
        run_service.untrack_active_run(alive.id)
        live_future.cancel()

    assert orphan.id in recovered
    assert alive.id not in recovered
    assert run_service.get_run(orphan.id).phase == RunPhase.PAUSED
    assert run_service.get_run(alive.id).phase == RunPhase.RUNNING


def test_startup_recovery_logs_scan_failures(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    from app.core import db
    from app.services import run_service

    db.init_db()

    def fail_fetch_many(*args, **kwargs):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(db, "fetch_many", fail_fetch_many)

    with caplog.at_level(logging.WARNING, logger=run_service.logger.name):
        recovered = run_service.recover_interrupted_runs()

    assert recovered == []
    assert "recover_interrupted_runs.scan" in caplog.text
    assert "scan exploded" in caplog.text


def test_lifespan_shutdown_calls_task_pool_shutdown(monkeypatch: pytest.MonkeyPatch):
    shutdown_calls: list[bool] = []

    class MockPool:
        async def shutdown(self) -> None:
            shutdown_calls.append(True)

    monkeypatch.setattr("app.services.task_pool.get_pool", lambda: MockPool())

    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200

    assert shutdown_calls == [True]


def test_lifespan_mcp_load_degrades_for_definition_shape_errors(monkeypatch: pytest.MonkeyPatch):
    from app import lifespan
    from app.config import AppSettings

    recorded: list[tuple[str, str, dict[str, str]]] = []
    registered: list[list[object]] = []

    class FailingMcpRegistry:
        def load_from_settings(self, settings):  # noqa: ARG002
            return None

        async def adapt_to_tool_definitions(self):
            raise ValueError("bad mcp definition")

    monkeypatch.setattr(lifespan, "get_mcp_registry", lambda: FailingMcpRegistry())
    monkeypatch.setattr(lifespan, "record", lambda event, actor, payload: recorded.append((event, actor, payload)))
    monkeypatch.setattr(
        lifespan,
        "register_all_tools",
        lambda *, extra_definitions, settings: registered.append(list(extra_definitions)),
    )

    asyncio.run(lifespan._load_mcp_tools(AppSettings(provider_name="mock")))

    assert recorded == [("mcp.startup_load_failed", "lifespan", {"error": "bad mcp definition"})]
    assert registered == [[]]


def test_lifespan_mcp_load_does_not_swallow_unexpected_adapter_bugs(monkeypatch: pytest.MonkeyPatch):
    from app import lifespan
    from app.config import AppSettings

    class BuggyMcpRegistry:
        def load_from_settings(self, settings):  # noqa: ARG002
            return None

        async def adapt_to_tool_definitions(self):
            raise RuntimeError("mcp adapter bug")

    monkeypatch.setattr(lifespan, "get_mcp_registry", lambda: BuggyMcpRegistry())

    with pytest.raises(RuntimeError, match="mcp adapter bug"):
        asyncio.run(lifespan._load_mcp_tools(AppSettings(provider_name="mock")))


def test_file_environment_startup_failure_unwinds_started_components(monkeypatch: pytest.MonkeyPatch):
    from app import lifespan
    from app.config import AppSettings

    calls: list[str] = []
    sink = object()

    class Watcher:
        def subscribe_changes(self, value):
            assert value is sink
            calls.append("watcher.subscribe")

        async def start(self, allowed_directories):
            assert allowed_directories
            calls.append("watcher.start")

        async def stop(self):
            calls.append("watcher.stop")

        def unsubscribe_changes(self, value):
            assert value is sink
            calls.append("watcher.unsubscribe")

    class EnvironmentStream:
        def file_change_sink(self):
            return sink

        async def start(self):
            calls.append("environment.start")

        async def stop(self):
            calls.append("environment.stop")

    class AutomationTriggers:
        def __init__(self, *, allowed_directories):
            assert allowed_directories

        async def start(self, watcher):  # noqa: ARG002
            calls.append("automation.start")
            raise RuntimeError("automation startup failed")

        async def stop(self):
            calls.append("automation.stop")

    watcher = Watcher()
    environment_stream = EnvironmentStream()
    monkeypatch.setattr(lifespan, "get_file_watcher", lambda: watcher)
    monkeypatch.setattr(lifespan, "get_environment_stream", lambda **kwargs: environment_stream)
    monkeypatch.setattr("app.automation.file_trigger.AutomationFileTriggerService", AutomationTriggers)

    async def run_startup() -> None:
        async with AsyncExitStack() as stack:
            await lifespan._start_file_environment(
                stack,
                AppSettings(provider_name="mock", allowed_directories=[str(Path.cwd())]),
            )

    with pytest.raises(RuntimeError, match="automation startup failed"):
        asyncio.run(run_startup())

    assert calls == [
        "watcher.subscribe",
        "environment.start",
        "watcher.start",
        "automation.start",
        "automation.stop",
        "watcher.stop",
        "watcher.unsubscribe",
        "environment.stop",
    ]
