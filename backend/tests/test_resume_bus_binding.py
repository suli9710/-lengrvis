"""R4-H3 regression: resuming an OS run must register the orchestrator's bus.

A fresh OSExecutionEngine resolved its orchestrator lazily inside run_turn while
the run-service bridge had already fallen back to a throwaway AgentBus, so the
resumed run's live agent messages never reached the timeline. resume_run now
materializes and binds the orchestrator before the bridge subscribes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.orchestration.execution_engine import InMemoryRunStore
from app.orchestration.execution_models import RunPhase, RunState
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.os_execution_engine import OSExecutionEngine


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


@pytest.mark.asyncio
async def test_resume_run_binds_orchestrator_bus_to_registry() -> None:
    orchestrator_registry.release_task("task_resume")
    engine = OSExecutionEngine(store=InMemoryRunStore())
    run_id = "run_resume_bus"
    engine.store.put(
        RunState(
            run_id=run_id,
            engine="os",
            phase=RunPhase.PAUSED,
            goal="resume bus",
            task_id="task_resume",
            current_plan={"task_id": "task_resume", "steps": []},
        )
    )

    # Before resume, no orchestrator is bound -> bus_for_task would hand the
    # bridge a throwaway fallback bus.
    assert orchestrator_registry.get_for_task("task_resume") is None

    await engine.resume_run(run_id)

    bound = orchestrator_registry.get_for_task("task_resume")
    assert bound is not None
    # The registry now exposes the *same* bus the engine's orchestrator uses,
    # so a bridge subscribing via bus_for_task observes real messages.
    assert orchestrator_registry.bus_for_task("task_resume") is bound.bus
    assert engine._orchestrators_by_run[run_id] is bound

    orchestrator_registry.release_task("task_resume")
