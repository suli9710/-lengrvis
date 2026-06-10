from __future__ import annotations

from pathlib import Path

from app.config import AppSettings, PROJECT_ROOT
from app.orchestration.lengrvis_code_config import (
    LENGRVIS_CODE_DISPLAY_NAME,
    LengrvisCodeConfig,
    default_allowed_tools,
    validate_allowed_tools,
)
from app.orchestration.lengrvis_code_runner import (
    LengrvisCodeStreamSummary,
    cancel_lengrvis_code_run,
    lengrvis_code_summary_to_turn_result,
    run_lengrvis_code,
)
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import (
    NON_EXECUTABLE_RUN_PHASES,
    EngineSelection,
    EngineTurnResult,
    RunPhase,
    RunState,
)


class DeveloperExecutionEngine(ExecutionEngine):
    """Developer engine backed by Lengrvis Code's headless tool-use loop."""

    name = "developer"

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        store: InMemoryRunStore | None = None,
        lengrvis_code_config: LengrvisCodeConfig | None = None,
        use_lengrvis_code: bool = True,
    ) -> None:
        self.settings = settings or AppSettings()
        self.store = store or default_run_store
        self.lengrvis_code_config = lengrvis_code_config
        self.use_lengrvis_code = use_lengrvis_code

    async def start_run(self, goal: str, mode: str, engine: EngineSelection = "auto") -> RunState:
        tool_safety_error = ""
        try:
            allowed_tools = lengrvis_code_developer_tool_names(self.lengrvis_code_config)
        except ValueError as exc:
            allowed_tools = ()
            tool_safety_error = f"Developer engine tool allowlist rejected: {exc}"
        phase = RunPhase.FAILED if tool_safety_error else RunPhase.PLANNING
        state = RunState(
            run_id=self.store.new_id("devrun"),
            engine="developer",
            phase=phase,
            goal=goal,
            mode=mode,
            transition_reason=tool_safety_error or f"developer {LENGRVIS_CODE_DISPLAY_NAME} run created",
            current_plan={
                "summary": f"Run {LENGRVIS_CODE_DISPLAY_NAME} headless with Lengrvis-controlled OpenAI config and tool permissions.",
                "adapter": "lengrvis_code_headless_stream_json",
                "adapter_display_name": LENGRVIS_CODE_DISPLAY_NAME,
                "workspace": _default_workspace(self.settings),
                "model": self.settings.model,
                "allowed_tools": list(allowed_tools),
                "permission_mode": _config_for_settings(self.settings, self.lengrvis_code_config).permission_mode,
                "lengrvis_code_enabled": self.use_lengrvis_code and not tool_safety_error,
                "dangerously_skip_permissions": False,
                "writes_enabled": False,
                **({"safety_error": tool_safety_error} if tool_safety_error else {}),
                "steps": [{"id": "lengrvis_code_run", "tool": "lengrvis_code", "status": "failed" if tool_safety_error else "pending"}],
            },
        )
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        if state.phase == RunPhase.PAUSED:
            state = state.model_copy(
                update={"phase": RunPhase.RUNNING, "transition_reason": f"developer {LENGRVIS_CODE_DISPLAY_NAME} run resumed"},
                deep=True,
            )
            return self.store.put(state)
        return state

    async def cancel_run(self, run_id: str) -> RunState:
        await cancel_lengrvis_code_run(run_id)
        state = self.store.get(run_id)
        updated = state.model_copy(
            update={"phase": RunPhase.CANCELLED, "transition_reason": f"developer {LENGRVIS_CODE_DISPLAY_NAME} run cancelled"},
            deep=True,
        )
        return self.store.put(updated)

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in NON_EXECUTABLE_RUN_PHASES:
            return EngineTurnResult(state=state, finished=True, message=f"Run is already {state.phase.value}.")
        if not self.use_lengrvis_code:
            disabled = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": f"{LENGRVIS_CODE_DISPLAY_NAME} developer engine is disabled.",
                    "current_plan": _mark_plan_steps_status(state.current_plan, "failed"),
                },
                deep=True,
            )
            return EngineTurnResult(state=self.store.put(disabled), finished=True, message=disabled.transition_reason)
        return await self._run_lengrvis_code_turn(state)

    async def _run_lengrvis_code_turn(self, state: RunState) -> EngineTurnResult:
        try:
            summary = await run_lengrvis_code(
                _prompt_from_goal(state.goal),
                cwd=_default_workspace(self.settings),
                settings=self.settings,
                config=_config_for_settings(self.settings, self.lengrvis_code_config),
                run_id=state.run_id,
            )
        except Exception as exc:  # noqa: BLE001 - external CLI failures become run failures.
            result = lengrvis_code_summary_to_turn_result(
                state,
                LengrvisCodeStreamSummary(launch_error=f"Unexpected {LENGRVIS_CODE_DISPLAY_NAME} adapter failure: {exc}"),
            )
            result.state.current_plan = _mark_plan_steps_status(result.state.current_plan, "failed")
            return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)

        result = lengrvis_code_summary_to_turn_result(state, summary)
        step_status = "succeeded" if result.state.phase == RunPhase.COMPLETED else result.state.phase.value
        result.state.current_plan = _mark_plan_steps_status(result.state.current_plan, step_status)
        return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)


def readonly_developer_tool_names() -> tuple[str, ...]:
    """Compatibility alias; developer runs now use Lengrvis Code's controlled tool allowlist."""

    return lengrvis_code_developer_tool_names()


def lengrvis_code_developer_tool_names(config: LengrvisCodeConfig | None = None) -> tuple[str, ...]:
    configured = getattr(config, "allowed_tools", None)
    return validate_allowed_tools(tuple(str(tool) for tool in configured)) if configured else default_allowed_tools()


def _config_for_settings(settings: AppSettings, config: LengrvisCodeConfig | None) -> LengrvisCodeConfig:
    if config is not None:
        return config
    return LengrvisCodeConfig(max_turns=max(1, int(settings.agent_loop_max_turns or 1)))


def _default_workspace(settings: AppSettings) -> str:
    if settings.allowed_directories:
        return str(Path(settings.allowed_directories[0]).expanduser().resolve(strict=False))
    return str(PROJECT_ROOT.resolve(strict=False))


def _prompt_from_goal(goal: str) -> str:
    return (
        f"You are the Lengrvis Developer Engine running through {LENGRVIS_CODE_DISPLAY_NAME} headless mode. "
        "Work only inside the allowed workspace provided by --add-dir. "
        "Use the controlled tool allowlist and do not request bypass permissions. "
        "Complete the user's development task, verify when practical, and summarize changed files and checks.\n\n"
        f"User task:\n{goal.strip()}"
    )


def _mark_plan_steps_status(plan: dict, status: str) -> dict:
    updated = {**plan}
    updated["steps"] = [
        {**step, "status": status}
        for step in list(plan.get("steps") or [])
        if isinstance(step, dict)
    ]
    return updated
