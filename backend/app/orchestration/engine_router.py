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
    NON_EXECUTABLE_RUN_PHASES,
    RunPhase,
    RunState,
    TERMINAL_RUN_PHASES,
)
from app.config import env_aliases


DEFAULT_ENGINE_ENV = "LENGRVIS_DEFAULT_ENGINE"
LEGACY_DEFAULT_ENGINE_ENVS = ("LENGRVIS_AGENT_LOOP_DEFAULT_ENGINE", "LENGRVIS_EXECUTION_DEFAULT_ENGINE")
EXECUTION_ENGINES_ENV = "LENGRVIS_EXECUTION_ENGINES"
MAX_TURNS_ENV = "LENGRVIS_AGENT_LOOP_MAX_TURNS"
DEFAULT_MAX_TURNS = 30

_DEVELOPER_GOAL_RE = re.compile(
    r"\b("
    r"inspect|analyze|analyse|review|explain|summarize|summarise|code|repo|repository|"
    r"git|diff|bug|debug|test|tests|pytest|lint|typecheck|build|compile|api|backend|"
    r"frontend|database|migration|function|class|module|package|dependency|import|"
    r"stacktrace|traceback|pr|pull request"
    r")\b",
    re.IGNORECASE,
)
_DEVELOPER_WRITE_INTENT_RE = re.compile(
    r"\b("
    r"fix|patch|repair|resolve|refactor|implement|change|modify|edit|write|add|remove|"
    r"delete|create|generate|scaffold|update|upgrade|migrate|replace|improve|rename|"
    r"address|failing|failed|broken|pass"
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
_SYSTEM_DIAGNOSTICS_RE = re.compile(
    r"("
    r"\b(?:system|computer|machine|pc|device)\s+(?:diagnostics?|checkup|health|status|inspection)\b|"
    r"\b(?:diagnose|check|inspect)\s+(?:this\s+)?(?:system|computer|machine|pc|device)\b|"
    r"(?:\u5e2e\u6211)?(?:\u68c0\u67e5|\u67e5\u770b|\u770b\u4e00\u4e0b|\u68c0\u6d4b|\u8bca\u65ad|\u4f53\u68c0)"
    r".*(?:\u8fd9\u53f0\u7535\u8111|\u7535\u8111|\u7cfb\u7edf|\u672c\u673a|CPU|\u5185\u5b58|\u78c1\u76d8)|"
    r"(?:\u8fd9\u53f0\u7535\u8111|\u7535\u8111|\u7cfb\u7edf|\u672c\u673a|CPU|\u5185\u5b58|\u78c1\u76d8)"
    r".*(?:\u68c0\u67e5|\u67e5\u770b|\u770b\u4e00\u4e0b|\u68c0\u6d4b|\u8bca\u65ad|\u4f53\u68c0)"
    r")",
    re.IGNORECASE,
)
_CHINESE_SYSTEM_DIAGNOSTICS_RE = re.compile(
    r"(?=.*(?:检查|查看|看一下|查|检测|诊断|体检|状态))"
    r"(?=.*(?:这台电脑|电脑|磁盘|内存|进程|本地\s*AI|CPU|系统(?:状态|诊断|体检|信息|配置|运行)))",
    re.IGNORECASE,
)


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
) -> EngineRouteDecision:
    if requested_engine in {"os", "developer"}:
        return EngineRouteDecision(
            requested_engine=requested_engine,
            selected_engine=requested_engine,
            reason="explicit engine override",
        )

    normalized = goal.strip()
    developer_goal = _DEVELOPER_GOAL_RE.search(normalized)
    os_goal = _OS_GOAL_RE.search(normalized)
    if developer_goal and _DEVELOPER_WRITE_INTENT_RE.search(normalized):
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="write-intent development goal requires the OS approval/runtime path",
        )
    if _SYSTEM_DIAGNOSTICS_RE.search(normalized) or _CHINESE_SYSTEM_DIAGNOSTICS_RE.search(normalized):
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="os",
            reason="goal matched read-only system diagnostics keywords",
        )
    if developer_goal and not os_goal:
        return EngineRouteDecision(
            requested_engine="auto",
            selected_engine="developer",
            reason="goal matched read-only developer/repository keywords",
        )
    if os_goal:
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
