from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Any
from uuid import uuid4

from app.config import get_env
from app.orchestration.execution_models import (
    TERMINAL_RUN_PHASES,
    EngineName,
    EngineSelection,
    EngineTurnResult,
    RunState,
)

DEFAULT_RUN_STORE_MAX_RUNS = 256
DEFAULT_RUN_STORE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_RUN_STORE_TERMINAL_TTL_SECONDS = 60 * 60
DEFAULT_RUN_STATE_MAX_OBSERVATIONS = 200
DEFAULT_RUN_STATE_MAX_LARGE_RESULT_REFS = 200


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


@dataclass(slots=True)
class _RunStoreEntry:
    state: RunState
    created_at: float
    updated_at: float


class InMemoryRunStore:
    """Small shared store for v1 engine skeletons.

    API persistence can replace this at the boundary without changing the
    execution engine contract.
    """

    def __init__(
        self,
        *,
        max_runs: int | None = None,
        ttl_seconds: float | None = None,
        terminal_ttl_seconds: float | None = None,
        max_observations: int | None = None,
        max_large_result_refs: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = RLock()
        self._runs: OrderedDict[str, _RunStoreEntry] = OrderedDict()
        self._max_runs = _bounded_store_int(
            max_runs,
            "LENGRVIS_RUN_STORE_MAX_RUNS",
            DEFAULT_RUN_STORE_MAX_RUNS,
        )
        self._ttl_seconds = _bounded_store_float(
            ttl_seconds,
            "LENGRVIS_RUN_STORE_TTL_SECONDS",
            DEFAULT_RUN_STORE_TTL_SECONDS,
        )
        self._terminal_ttl_seconds = _bounded_store_float(
            terminal_ttl_seconds,
            "LENGRVIS_RUN_STORE_TERMINAL_TTL_SECONDS",
            DEFAULT_RUN_STORE_TERMINAL_TTL_SECONDS,
        )
        self._max_observations = _bounded_store_int(
            max_observations,
            "LENGRVIS_RUN_STATE_MAX_OBSERVATIONS",
            DEFAULT_RUN_STATE_MAX_OBSERVATIONS,
        )
        self._max_large_result_refs = _bounded_store_int(
            max_large_result_refs,
            "LENGRVIS_RUN_STATE_MAX_LARGE_RESULT_REFS",
            DEFAULT_RUN_STATE_MAX_LARGE_RESULT_REFS,
        )
        self._clock = clock or time.monotonic

    def new_id(self, prefix: str = "run") -> str:
        return f"{prefix}_{uuid4().hex}"

    def put(self, state: RunState) -> RunState:
        with self._lock:
            now = self._clock()
            bounded = self.trim_state_history(state)
            existing = self._runs.get(state.run_id)
            self._runs[state.run_id] = _RunStoreEntry(
                state=bounded.model_copy(deep=True),
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            self._runs.move_to_end(state.run_id)
            self._prune_locked(now)
            return bounded

    def get(self, run_id: str) -> RunState:
        with self._lock:
            now = self._clock()
            self._prune_locked(now)
            entry = self._runs.get(run_id)
            if entry is None:
                raise RunNotFoundError(f"Run not found: {run_id}")
            self._runs.move_to_end(run_id)
            if entry.state.phase not in TERMINAL_RUN_PHASES:
                entry.updated_at = now
            return entry.state.model_copy(deep=True)

    def update(self, state: RunState, **changes: Any) -> RunState:
        updated = state.model_copy(update=changes, deep=True)
        return self.put(updated)

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def prune(self) -> None:
        with self._lock:
            self._prune_locked(self._clock())

    def trim_state_history(self, state: RunState) -> RunState:
        updates: dict[str, Any] = {}
        if len(state.observations) > self._max_observations:
            updates["observations"] = state.observations[-self._max_observations :]
        if len(state.large_result_refs) > self._max_large_result_refs:
            updates["large_result_refs"] = state.large_result_refs[-self._max_large_result_refs :]
        if not updates:
            return state
        return state.model_copy(update=updates, deep=False)

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._runs)

    def _prune_locked(self, now: float) -> None:
        expired = [run_id for run_id, entry in self._runs.items() if self._entry_expired(entry, now)]
        for run_id in expired:
            self._runs.pop(run_id, None)
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)

    def _entry_expired(self, entry: _RunStoreEntry, now: float) -> bool:
        age = max(0.0, now - entry.updated_at)
        if entry.state.phase in TERMINAL_RUN_PHASES and age > self._terminal_ttl_seconds:
            return True
        return age > self._ttl_seconds


def _bounded_store_int(value: int | None, env_key: str, default: int) -> int:
    if value is None:
        value = _env_int(env_key, default)
    return max(1, int(value))


def _bounded_store_float(value: float | None, env_key: str, default: float) -> float:
    if value is None:
        value = _env_float(env_key, default)
    return max(1.0, float(value))


def _env_int(env_key: str, default: int) -> int:
    raw = str(get_env(env_key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(env_key: str, default: float) -> float:
    raw = str(get_env(env_key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


default_run_store = InMemoryRunStore()
