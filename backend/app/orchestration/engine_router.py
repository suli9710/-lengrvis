from __future__ import annotations

import os
import re
from collections.abc import Mapping

from app.orchestration.execution_engine import ExecutionEngine
from app.orchestration.execution_models import (
    EngineName,
    EngineRouteDecision,
    EngineSelection,
    EngineTurnResult,
    RunPhase,
    RunState,
)


DEFAULT_ENGINE_ENV = "MARVIS_DEFAULT_ENGINE"
LEGACY_DEFAULT_ENGINE_ENVS = ("MARVIS_AGENT_LOOP_DEFAULT_ENGINE", "MARVIS_EXECUTION_DEFAULT_ENGINE")
EXECUTION_ENGINES_ENV = "MARVIS_EXECUTION_ENGINES"
MAX_TURNS_ENV = "MARVIS_AGENT_LOOP_MAX_TURNS"
DEFAULT_MAX_TURNS = 30

_DEVELOPER_GOAL_RE = re.compile(
    r"\b("
    r"code|repo|repository|git|diff|patch|bug|debug|test|tests|pytest|lint|typecheck|"
    r"refactor|implement|fix|build|compile|api|backend|frontend|database|migration|"
    r"function|class|module|package|dependency|import|stacktrace|traceback|pr|pull request"
    r")\b",
    re.IGNORECASE,
)
_OS_GOAL_RE = re.compile(
    r"\b("
    r"open|click|browser|website|web page|app|window|desktop|screen|screenshot|folder|"
    r"file manager|finder|explorer|document|spreadsheet|presentation|word|excel|powerpoint|"
    r"calendar|email|remote|ui|mouse|keyboard"
    r")\b",
    re.IGNORECASE,
)


def configured_default_engine(environ: Mapping[str, str] | None = None) -> EngineSelection:
    source = environ or os.environ
    raw = source.get(DEFAULT_ENGINE_ENV, "").strip().casefold()
    if not raw:
        for env_key in LEGACY_DEFAULT_ENGINE_ENVS:
            raw = source.get(env_key, "").strip().casefold()
            if raw:
                break
    raw = raw or "auto"
    return raw if raw in {"auto", "os", "developer"} else "auto"  # type: ignore[return-value]


def configured_max_turns(environ: Mapping[str, str] | None = None) -> int:
    raw = (environ or os.environ).get(MAX_TURNS_ENV, str(DEFAULT_MAX_TURNS))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TURNS


def route_engine(
    goal: str,
    requested_engine: EngineSelection = "auto",
    *,
    fallback_engine: EngineSelection = "os",
) -> EngineRouteDecision:
    if requested_engine in {"os", "developer"}:
        return EngineRouteDecision(
            requested_engine=requested_engine,
            selected_engine=requested_engine,
            reason="explicit engine override",
        )

    normalized = goal.strip()
    if _DEVELOPER_GOAL_RE.search(normalized) and not _OS_GOAL_RE.search(normalized):
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="developer",
            reason="goal matched developer/repository keywords",
        )
    if _OS_GOAL_RE.search(normalized):
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="goal matched OS/browser/app/document keywords",
        )
    selected_fallback: EngineName = fallback_engine if fallback_engine in {"os", "developer"} else "os"
    return EngineRouteDecision(
        requested_engine="auto",
        selected_engine=selected_fallback,
        reason="default engine fallback for ambiguous goal",
    )


class EngineRouter:
    def __init__(
        self,
        engines: Mapping[EngineName, ExecutionEngine],
        *,
        default_engine: EngineSelection | None = None,
        max_turns: int | None = None,
    ) -> None:
        self.engines = dict(engines)
        self.default_engine = default_engine or configured_default_engine()
        self.max_turns = max_turns or configured_max_turns()
        self._run_engines: dict[str, EngineName] = {}

    def route(self, goal: str, requested_engine: EngineSelection = "auto") -> EngineRouteDecision:
        decision = route_engine(goal, requested_engine, fallback_engine=self.default_engine)
        if decision.selected_engine not in self.engines:
            available = ", ".join(sorted(self.engines)) or "none"
            raise KeyError(f"Execution engine is not registered: {decision.selected_engine} (available: {available})")
        return decision

    async def start_run(self, goal: str, mode: str = "efficiency", engine: EngineSelection = "auto") -> RunState:
        decision = self.route(goal, engine)
        state = await self.engines[decision.selected_engine].start_run(goal, mode, decision.selected_engine)
        routed = state.model_copy(update={"transition_reason": decision.reason}, deep=True)
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
        if state.turn_count >= self.max_turns and state.phase not in {
            RunPhase.COMPLETED,
            RunPhase.FAILED,
            RunPhase.DENIED,
            RunPhase.CANCELLED,
        }:
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
