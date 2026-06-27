from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.config import AppSettings
from app.orchestration.engine_router import EngineRouter
from app.orchestration.execution_engine import ExecutionEngine
from app.orchestration.execution_models import EngineTurnResult, RunPhase, RunState
from app.services import run_service


class StubEngine(ExecutionEngine):
    name = "os"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def start_run(self, goal: str, mode: str, engine: str = "auto") -> RunState:
        return RunState(run_id="run_stub", engine="os", phase=RunPhase.RUNNING, goal=goal, mode=mode)

    async def resume_run(self, run_id: str) -> RunState:
        return RunState(run_id=run_id, engine="os", phase=RunPhase.RUNNING, goal="resume")

    async def cancel_run(self, run_id: str) -> RunState:
        self.cancelled.append(run_id)
        return RunState(run_id=run_id, engine="os", phase=RunPhase.CANCELLED, goal="cancel")

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        return EngineTurnResult(state=state, finished=True)


def test_cancel_run_reuses_tracked_router(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = StubEngine()
    router = EngineRouter({"os": engine}, default_engine="os", max_turns=1)
    run_service._track_run_router("run_stub", router)

    monkeypatch.setattr(
        run_service,
        "get_effective_settings",
        lambda: AppSettings(data_dir="."),
    )
    monkeypatch.setattr(
        run_service,
        "get_run",
        lambda run_id: SimpleNamespace(
            id=run_id,
            phase=run_service.RunPhase.RUNNING,
            engine=run_service.RunEngine.OS,
            task_id=None,
            state={},
        ),
    )
    monkeypatch.setattr(run_service, "_cancel_persisted_state", lambda run: None)
    monkeypatch.setattr(run_service, "_update_run", lambda run, **kwargs: run)
    monkeypatch.setattr(run_service, "_cancel_active_run_task", lambda run_id, **kwargs: None)
    monkeypatch.setattr(run_service, "run_event_bus", SimpleNamespace(publish=lambda *args, **kwargs: None))

    scheduled: list = []

    def fake_schedule(coro, *, data_dir):
        scheduled.append(coro)
        return SimpleNamespace()

    monkeypatch.setattr(run_service, "_schedule_background", fake_schedule)

    run_service.cancel_run("run_stub")

    assert len(scheduled) == 1
    asyncio.run(scheduled[0])
    assert engine.cancelled == ["run_stub"]


def test_cancel_run_logs_schedule_failure_with_redacted_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = SimpleNamespace(
        id="run_stub_failure",
        phase=run_service.RunPhase.RUNNING,
        engine=run_service.RunEngine.OS,
        task_id=None,
        state={},
    )
    router = EngineRouter({"os": StubEngine()}, default_engine="os", max_turns=1)

    monkeypatch.setattr(
        run_service,
        "get_effective_settings",
        lambda: AppSettings(data_dir="."),
    )
    monkeypatch.setattr(run_service, "get_run", lambda run_id: run)
    monkeypatch.setattr(run_service, "_cancel_persisted_state", lambda run: None)
    monkeypatch.setattr(run_service, "_router_for_run", lambda run_id, settings: router)
    monkeypatch.setattr(run_service, "_cancel_active_run_task", lambda run_id, **kwargs: None)
    monkeypatch.setattr(run_service, "run_event_bus", SimpleNamespace(publish=lambda *args, **kwargs: None))

    def update_run(target, **kwargs):
        for key, value in kwargs.items():
            if value is not None:
                setattr(target, key, value)
        return target

    def fake_schedule(coro, *, data_dir):
        coro.close()
        raise RuntimeError("schedule failed token=supersecrettokenvalue1234567890")

    monkeypatch.setattr(run_service, "_update_run", update_run)
    monkeypatch.setattr(run_service, "_schedule_background", fake_schedule)
    caplog.set_level(logging.WARNING, logger="app.services.run_service")

    cancelled = run_service.cancel_run("run_stub_failure")

    assert cancelled.phase == run_service.RunPhase.CANCELLED
    assert "cancel_run.schedule_engine_cancellation" in caplog.text
    assert "supersecrettokenvalue" not in caplog.text
