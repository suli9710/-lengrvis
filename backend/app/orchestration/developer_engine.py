from __future__ import annotations

from pathlib import Path

from app.config import AppSettings, PROJECT_ROOT
from app.orchestration.claude_code_config import ClaudeCodeConfig, default_allowed_tools
from app.orchestration.claude_code_runner import (
    cancel_claude_code_run,
    claude_code_summary_to_turn_result,
    run_claude_code,
)
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import EngineSelection, EngineTurnResult, RunPhase, RunState


class DeveloperExecutionEngine(ExecutionEngine):
    """Developer engine backed by Claude Code's headless tool-use loop."""

    name = "developer"

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        store: InMemoryRunStore | None = None,
        claude_code_config: ClaudeCodeConfig | None = None,
        use_claude_code: bool = True,
    ) -> None:
        self.settings = settings or AppSettings()
        self.store = store or default_run_store
        self.claude_code_config = claude_code_config
        self.use_claude_code = use_claude_code

    async def start_run(self, goal: str, mode: str, engine: EngineSelection = "auto") -> RunState:
        state = RunState(
            run_id=self.store.new_id("devrun"),
            engine="developer",
            phase=RunPhase.PLANNING,
            goal=goal,
            mode=mode,
            transition_reason="developer Claude Code run created",
            current_plan={
                "summary": "Run Claude Code headless with Mavris-controlled OpenAI config and tool permissions.",
                "adapter": "claude_code_headless_stream_json",
                "workspace": _default_workspace(self.settings),
                "model": self.settings.model,
                "allowed_tools": list(claude_code_developer_tool_names(self.claude_code_config)),
                "permission_mode": "acceptEdits",
                "claude_code_enabled": self.use_claude_code,
                "dangerously_skip_permissions": False,
                "writes_enabled": self.use_claude_code,
                "steps": [{"id": "claude_code_run", "tool": "claude_code", "status": "pending"}],
            },
        )
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        if state.phase == RunPhase.PAUSED:
            state = state.model_copy(
                update={"phase": RunPhase.RUNNING, "transition_reason": "developer Claude Code run resumed"},
                deep=True,
            )
            return self.store.put(state)
        return state

    async def cancel_run(self, run_id: str) -> RunState:
        await cancel_claude_code_run(run_id)
        state = self.store.get(run_id)
        updated = state.model_copy(
            update={"phase": RunPhase.CANCELLED, "transition_reason": "developer Claude Code run cancelled"},
            deep=True,
        )
        return self.store.put(updated)

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED}:
            return EngineTurnResult(state=state, finished=True, message=f"Run is already {state.phase.value}.")
        if not self.use_claude_code:
            disabled = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": "Claude Code developer engine is disabled.",
                    "current_plan": _mark_plan_steps_status(state.current_plan, "failed"),
                },
                deep=True,
            )
            return EngineTurnResult(state=self.store.put(disabled), finished=True, message=disabled.transition_reason)
        return await self._run_claude_code_turn(state)

    async def _run_claude_code_turn(self, state: RunState) -> EngineTurnResult:
        try:
            summary = await run_claude_code(
                _prompt_from_goal(state.goal),
                cwd=_default_workspace(self.settings),
                settings=self.settings,
                config=_config_for_settings(self.settings, self.claude_code_config),
                run_id=state.run_id,
            )
        except Exception as exc:  # noqa: BLE001 - external CLI failures become run failures.
            failed = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": f"claude code run failed: {exc}",
                    "current_plan": _mark_plan_steps_status(state.current_plan, "failed"),
                },
                deep=True,
            )
            return EngineTurnResult(state=self.store.put(failed), finished=True, message=failed.transition_reason)

        result = claude_code_summary_to_turn_result(state, summary)
        step_status = "succeeded" if result.state.phase == RunPhase.COMPLETED else result.state.phase.value
        result.state.current_plan = _mark_plan_steps_status(result.state.current_plan, step_status)
        return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)


def readonly_developer_tool_names() -> tuple[str, ...]:
    """Compatibility alias; developer runs now use Claude Code's controlled tool allowlist."""

    return claude_code_developer_tool_names()


def claude_code_developer_tool_names(config: ClaudeCodeConfig | None = None) -> tuple[str, ...]:
    configured = getattr(config, "allowed_tools", None)
    return tuple(str(tool) for tool in configured) if configured else default_allowed_tools()


def _config_for_settings(settings: AppSettings, config: ClaudeCodeConfig | None) -> ClaudeCodeConfig:
    if config is not None:
        return config
    return ClaudeCodeConfig(max_turns=max(1, int(settings.agent_loop_max_turns or 1)))


def _default_workspace(settings: AppSettings) -> str:
    if settings.allowed_directories:
        return str(Path(settings.allowed_directories[0]).expanduser().resolve(strict=False))
    return str(PROJECT_ROOT.resolve(strict=False))


def _prompt_from_goal(goal: str) -> str:
    return (
        "You are the Mavris Developer Engine running through Claude Code headless mode. "
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
