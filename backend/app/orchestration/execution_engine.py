from __future__ import annotations

from abc import ABC, abstractmethod
from threading import RLock
from typing import Any
from uuid import uuid4

from app.orchestration.execution_models import EngineName, EngineSelection, EngineTurnResult, RunState


class RunNotFoundError(KeyError):
    """Raised when an execution run cannot be found."""


class ExecutionEngine(ABC):
    name: EngineName

    @abstractmethod
    async def start_run(self, goal: str, mode: str, engine: EngineSelection = "auto") -> RunState:
        raise NotImplementedError

    @abstractmethod
    async def resume_run(self, run_id: str) -> RunState:
        raise NotImplementedError

    @abstractmethod
    async def cancel_run(self, run_id: str) -> RunState:
        raise NotImplementedError

    @abstractmethod
    async def run_turn(self, state: RunState) -> EngineTurnResult:
        raise NotImplementedError


class InMemoryRunStore:
    """Small shared store for v1 engine skeletons.

    API persistence can replace this at the boundary without changing the
    execution engine contract.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[str, RunState] = {}

    def new_id(self, prefix: str = "run") -> str:
        return f"{prefix}_{uuid4().hex}"

    def put(self, state: RunState) -> RunState:
        with self._lock:
            self._runs[state.run_id] = state.model_copy(deep=True)
            return state

    def get(self, run_id: str) -> RunState:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                raise RunNotFoundError(f"Run not found: {run_id}")
            return state.model_copy(deep=True)

    def update(self, state: RunState, **changes: Any) -> RunState:
        updated = state.model_copy(update=changes, deep=True)
        return self.put(updated)


default_run_store = InMemoryRunStore()
