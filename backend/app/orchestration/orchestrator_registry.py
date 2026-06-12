from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.orchestration.agent_bus import AgentBus

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent


class OrchestratorRegistry:
    """Caches orchestrators per task/run so buses and engines stay isolated."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_task: dict[str, OrchestratorAgent] = {}
        self._run_to_task: dict[str, str] = {}

    def bind(self, *, task_id: str, orchestrator: OrchestratorAgent, run_id: str | None = None) -> None:
        with self._lock:
            self._by_task[task_id] = orchestrator
            if run_id:
                self._run_to_task[run_id] = task_id

    def get_for_task(self, task_id: str) -> OrchestratorAgent | None:
        with self._lock:
            return self._by_task.get(task_id)

    def get_for_run(self, run_id: str) -> OrchestratorAgent | None:
        with self._lock:
            task_id = self._run_to_task.get(run_id)
            if not task_id:
                return None
            return self._by_task.get(task_id)

    def bus_for_task(self, task_id: str, *, fallback: AgentBus | None = None) -> AgentBus:
        orchestrator = self.get_for_task(task_id)
        if orchestrator is not None:
            return orchestrator.bus
        return fallback or AgentBus()

    def get_or_create_for_task(
        self,
        task_id: str,
        factory: Callable[[], OrchestratorAgent],
    ) -> OrchestratorAgent:
        with self._lock:
            existing = self._by_task.get(task_id)
            if existing is not None:
                return existing
            orchestrator = factory()
            self._by_task[task_id] = orchestrator
            return orchestrator

    def release_run(self, run_id: str) -> None:
        with self._lock:
            self._run_to_task.pop(run_id, None)

    def release_task(self, task_id: str) -> None:
        with self._lock:
            self._by_task.pop(task_id, None)
            for run_id, bound_task_id in list(self._run_to_task.items()):
                if bound_task_id == task_id:
                    self._run_to_task.pop(run_id, None)


orchestrator_registry = OrchestratorRegistry()
