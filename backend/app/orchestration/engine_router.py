from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.agents.delegation_rules import (
    goal_has_developer_read_intent,
    goal_has_developer_write_intent,
    goal_has_os_intent,
    goal_is_system_diagnostics,
)
from app.config import env_aliases
from app.orchestration.execution_engine import ExecutionEngine
from app.orchestration.execution_models import (
    NON_EXECUTABLE_RUN_PHASES,
    TERMINAL_RUN_PHASES,
    EngineName,
    EngineRouteDecision,
    EngineSelection,
    EngineTurnResult,
    RunPhase,
    RunState,
)

DEFAULT_ENGINE_ENV = "LENGRVIS_DEFAULT_ENGINE"

LEGACY_DEFAULT_ENGINE_ENVS = ("LENGRVIS_AGENT_LOOP_DEFAULT_ENGINE", "LENGRVIS_EXECUTION_DEFAULT_ENGINE")

EXECUTION_ENGINES_ENV = "LENGRVIS_EXECUTION_ENGINES"

MAX_TURNS_ENV = "LENGRVIS_AGENT_LOOP_MAX_TURNS"

DEFAULT_MAX_TURNS = 30


def configured_default_engine(environ: Mapping[str, str] | None = None) -> EngineSelection:
    source = environ or os.environ

    raw = _env(source, DEFAULT_ENGINE_ENV).strip().casefold()

    if not raw:
        for env_key in LEGACY_DEFAULT_ENGINE_ENVS:
            raw = _env(source, env_key).strip().casefold()

            if raw:
                break

    raw = raw or "auto"

    return raw if raw in {"auto", "os", "developer"} else "auto"  # type: ignore[return-value]


def configured_max_turns(environ: Mapping[str, str] | None = None) -> int:
    raw = _env(environ or os.environ, MAX_TURNS_ENV, str(DEFAULT_MAX_TURNS))

    try:
        return max(1, int(raw))

    except (TypeError, ValueError):
        return DEFAULT_MAX_TURNS


def _env(source: Mapping[str, str], key: str, default: str = "") -> str:
    for alias in env_aliases(key):
        raw = source.get(alias)

        if raw:
            return raw

    return default


def route_engine(
    goal: str,
    requested_engine: EngineSelection = "auto",
    *,
    fallback_engine: EngineSelection = "os",
    developer_writes_enabled: bool = False,
) -> EngineRouteDecision:
    if requested_engine in {"os", "developer"}:
        return EngineRouteDecision(
            requested_engine=requested_engine,
            selected_engine=requested_engine,
            reason="explicit engine override",
            rule="explicit_override",
        )

    normalized = goal.strip()

    developer_goal = goal_has_developer_read_intent(normalized)

    os_goal = goal_has_os_intent(normalized)

    if developer_goal and goal_has_developer_write_intent(normalized):
        if developer_writes_enabled:
            return EngineRouteDecision(
                requested_engine="auto",
                selected_engine="developer",
                reason="write-intent development goal with developer writes enabled",
                rule="developer_write_enabled",
            )

        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="write-intent development goal requires the OS approval/runtime path",
            rule="developer_write_os",
        )

    if goal_is_system_diagnostics(normalized):
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="goal matched read-only system diagnostics keywords",
            rule="system_diagnostics",
        )

    if developer_goal and not os_goal:
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="developer",
            reason="goal matched read-only developer/repository keywords",
            rule="developer_read_only",
        )

    if os_goal:
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="goal matched OS/browser/app/document keywords",
            rule="os_goal",
        )

    selected_fallback: EngineName = fallback_engine if fallback_engine in {"os", "developer"} else "os"

    return EngineRouteDecision(
        requested_engine="auto",
        selected_engine=selected_fallback,
        reason="default engine fallback for ambiguous goal",
        rule="ambiguous_fallback",
    )


class EngineRouter:
    def __init__(
        self,
        engines: Mapping[EngineName, ExecutionEngine],
        *,
        default_engine: EngineSelection | None = None,
        max_turns: int | None = None,
        developer_writes_enabled: bool = False,
    ) -> None:
        self.engines = dict(engines)

        self.default_engine = default_engine or configured_default_engine()

        self.max_turns = max_turns or configured_max_turns()

        self.developer_writes_enabled = developer_writes_enabled

        self._run_engines: dict[str, EngineName] = {}

    def route(self, goal: str, requested_engine: EngineSelection = "auto") -> EngineRouteDecision:
        decision = route_engine(
            goal,
            requested_engine,
            fallback_engine=self.default_engine,
            developer_writes_enabled=self.developer_writes_enabled,
        )

        if decision.selected_engine not in self.engines:
            available = ", ".join(sorted(self.engines)) or "none"

            raise KeyError(f"Execution engine is not registered: {decision.selected_engine} (available: {available})")

        return decision

    async def start_run(
        self,
        goal: str,
        mode: str = "efficiency",
        engine: EngineSelection = "auto",
        *,
        task_metadata: dict[str, Any] | None = None,
    ) -> RunState:
        decision = self.route(goal, engine)

        state = await self.engines[decision.selected_engine].start_run(
            goal,
            mode,
            decision.selected_engine,
            task_metadata=task_metadata,
        )

        routed = state.model_copy(
            update={"transition_reason": decision.reason, "route_rule": decision.rule},
            deep=True,
        )

        self._run_engines[routed.run_id] = routed.engine

        return routed

    async def resume_run(self, run_id: str) -> RunState:
        engine = self._engine_for_run(run_id)

        state = await self.engines[engine].resume_run(run_id)

        self._run_engines[state.run_id] = state.engine

        return state

    async def cancel_run(self, run_id: str) -> RunState:
        engine = self._engine_for_run(run_id)

        state = await self.engines[engine].cancel_run(run_id)

        self._run_engines[state.run_id] = state.engine

        return state

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in NON_EXECUTABLE_RUN_PHASES:
            result = await self.engines[state.engine].run_turn(state)

            self._run_engines[result.state.run_id] = result.state.engine

            return result

        if state.turn_count >= self.max_turns and state.phase not in TERMINAL_RUN_PHASES:
            stopped = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "transition_reason": f"max turns reached ({self.max_turns})",
                },
                deep=True,
            )

            return EngineTurnResult(state=stopped, finished=True, message=stopped.transition_reason)

        result = await self.engines[state.engine].run_turn(state)

        self._run_engines[result.state.run_id] = result.state.engine

        return result

    def _engine_for_run(self, run_id: str) -> EngineName:
        engine = self._run_engines.get(run_id)

        if engine is not None:
            return engine

        if len(self.engines) == 1:
            return next(iter(self.engines))

        raise KeyError(f"Run has no registered engine in this router: {run_id}")
