from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import AppSettings, PROJECT_ROOT
from app.agents.delegation_metadata import developer_engine_capabilities
from app.integrations.lengrvis_code import (
    allowed_tools_for_developer,
    validate_allowed_tools,
)
from app.orchestration.lengrvis_code_config import LENGRVIS_CODE_DISPLAY_NAME, LengrvisCodeConfig
from app.orchestration.lengrvis_code_runner import (
    LengrvisCodeStreamSummary,
    cancel_lengrvis_code_run,
    lengrvis_code_summary_to_turn_result,
    run_lengrvis_code,
)
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.developer_write_guard import run_write_verification
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

    async def start_run(
        self,
        goal: str,
        mode: str,
        engine: EngineSelection = "auto",
        *,
        task_metadata: dict[str, Any] | None = None,
    ) -> RunState:  # noqa: ARG002
        writes_enabled = bool(getattr(self.settings, "developer_writes_enabled", False))
        require_verification = bool(getattr(self.settings, "developer_writes_require_verification", True))
        tool_safety_error = ""
        try:
            allowed_tools = lengrvis_code_developer_tool_names(
                self.lengrvis_code_config,
                writes_enabled=writes_enabled,
            )
        except ValueError as exc:
            allowed_tools = ()
            tool_safety_error = f"Developer engine tool allowlist rejected: {exc}"
        caps = developer_engine_capabilities(writes_enabled=writes_enabled and not tool_safety_error)
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
                "writes_enabled": writes_enabled and not tool_safety_error,
                "writes_require_verification": require_verification and writes_enabled and not tool_safety_error,
                "capability_mode": caps["mode"],
                "capability_disclosure": caps["disclosure"],
                **({"safety_error": tool_safety_error} if tool_safety_error else {}),
                "steps": [
                    {"id": "lengrvis_code_run", "tool": "lengrvis_code", "status": "failed" if tool_safety_error else "pending"},
                    *(
                        [{"id": "write_verification", "tool": "developer_write_guard", "status": "pending"}]
                        if writes_enabled and not tool_safety_error
                        else []
                    ),
                ],
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
        # Re-check live settings on every turn/resume; do not trust a stale plan snapshot.
        writes_enabled = bool(getattr(self.settings, "developer_writes_enabled", False))
        base_config = _config_for_settings(self.settings, self.lengrvis_code_config)
        plan_tools = tuple(str(tool) for tool in (state.current_plan.get("allowed_tools") or []))
        try:
            if plan_tools:
                if writes_enabled:
                    candidate_tools = plan_tools
                else:
                    from app.integrations.lengrvis_code import WRITE_CAPABLE_ALLOWED_TOOLS

                    candidate_tools = tuple(
                        tool
                        for tool in plan_tools
                        if tool.split("(", 1)[0] not in WRITE_CAPABLE_ALLOWED_TOOLS
                    )
                if candidate_tools:
                    allowed_tools = validate_allowed_tools(candidate_tools, allow_write_tools=writes_enabled)
                else:
                    allowed_tools = lengrvis_code_developer_tool_names(
                        self.lengrvis_code_config,
                        writes_enabled=writes_enabled,
                    )
            else:
                allowed_tools = lengrvis_code_developer_tool_names(
                    self.lengrvis_code_config,
                    writes_enabled=writes_enabled,
                )
        except ValueError as exc:
            failed = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": f"Developer engine tool allowlist rejected: {exc}",
                    "current_plan": _mark_plan_steps_status(state.current_plan, "failed"),
                },
                deep=True,
            )
            return EngineTurnResult(
                state=self.store.put(failed),
                finished=True,
                message=failed.transition_reason,
            )
        launch_config = LengrvisCodeConfig(
            command=base_config.command,
            executable=base_config.executable,
            executable_args=base_config.executable_args,
            allowed_tools=allowed_tools or base_config.allowed_tools,
            max_turns=base_config.max_turns,
            permission_mode=base_config.permission_mode,
            extra_args=base_config.extra_args,
            env=base_config.env,
        )
        try:
            summary = await run_lengrvis_code(
                _prompt_from_goal(state.goal, writes_enabled=writes_enabled),
                cwd=_default_workspace(self.settings),
                settings=self.settings,
                config=launch_config,
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
        if writes_enabled and summary.permission_denials:
            result = _await_write_approval(result, summary)
        elif writes_enabled and result.state.phase == RunPhase.COMPLETED:
            result = _apply_write_verification(result, summary, settings=self.settings, writes_enabled=writes_enabled)
        step_status = _plan_step_status(result.state.phase)
        result.state.current_plan = _mark_plan_steps_status(result.state.current_plan, step_status, step_id="lengrvis_code_run")
        if writes_enabled and result.outputs.get("write_verification"):
            verify_ok = bool(result.outputs["write_verification"].get("ok"))
            result.state.current_plan = _mark_plan_steps_status(
                result.state.current_plan,
                "succeeded" if verify_ok else "failed",
                step_id="write_verification",
            )
        return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)


def readonly_developer_tool_names() -> tuple[str, ...]:
    """Compatibility alias; developer runs now use Lengrvis Code's controlled tool allowlist."""

    return lengrvis_code_developer_tool_names()


def lengrvis_code_developer_tool_names(
    config: LengrvisCodeConfig | None = None,
    *,
    writes_enabled: bool = False,
) -> tuple[str, ...]:
    from app.integrations.lengrvis_code import WRITE_CAPABLE_ALLOWED_TOOLS

    configured = getattr(config, "allowed_tools", None)
    if configured:
        tools = tuple(str(tool) for tool in configured)
        if writes_enabled:
            tools = tools + tuple(tool for tool in WRITE_CAPABLE_ALLOWED_TOOLS if tool not in tools)
        return validate_allowed_tools(tools, allow_write_tools=writes_enabled)
    return allowed_tools_for_developer(writes_enabled=writes_enabled)


def _config_for_settings(settings: AppSettings, config: LengrvisCodeConfig | None) -> LengrvisCodeConfig:
    if config is not None:
        return config
    return LengrvisCodeConfig(max_turns=max(1, int(settings.agent_loop_max_turns or 1)))


def _default_workspace(settings: AppSettings) -> str:
    if settings.allowed_directories:
        return str(Path(settings.allowed_directories[0]).expanduser().resolve(strict=False))
    return str(PROJECT_ROOT.resolve(strict=False))


def _prompt_from_goal(goal: str, *, writes_enabled: bool = False) -> str:
    write_clause = (
        "Write/Edit tools are enabled inside the workspace only; every mutation must pass Lengrvis Code permission prompts "
        "(dry-run/approval) before applying. After edits, run the smallest relevant pytest command to verify the fix. "
        if writes_enabled
        else "Do not request Write/Edit/Bash/Agent tools or bypass permissions. "
    )
    return (
        f"You are the Lengrvis Developer Engine running through {LENGRVIS_CODE_DISPLAY_NAME} headless mode. "
        "Work only inside the allowed workspace provided by --add-dir. "
        f"{write_clause}"
        "Use the controlled tool allowlist. "
        "Complete the user's development task, verify when practical, and summarize changed files and checks.\n\n"
        f"User task:\n{goal.strip()}"
    )


def _await_write_approval(result: EngineTurnResult, summary: Any) -> EngineTurnResult:
    from app.integrations.lengrvis_code import LENGRVIS_CODE_DISPLAY_NAME

    payload = dict(result.outputs.get("lengrvis_code") or {})
    payload["awaiting_write_approval"] = True
    updated = result.state.model_copy(
        update={
            "phase": RunPhase.AWAITING_APPROVAL,
            "transition_reason": (
                f"{LENGRVIS_CODE_DISPLAY_NAME} write/edit blocked pending user approval "
                f"({len(summary.permission_denials)} denial(s))."
            ),
            "current_plan": {
                **result.state.current_plan,
                "pending_write_approvals": summary.permission_denials,
            },
        },
        deep=True,
    )
    return result.model_copy(
        update={
            "state": updated,
            "finished": True,
            "message": updated.transition_reason,
            "outputs": {**result.outputs, "lengrvis_code": payload},
        },
        deep=True,
    )


def _apply_write_verification(
    result: EngineTurnResult,
    summary: Any,
    *,
    settings: AppSettings,
    writes_enabled: bool,
) -> EngineTurnResult:
    if not writes_enabled:
        return result
    require_verification = bool(getattr(settings, "developer_writes_require_verification", True))
    workspace = _default_workspace(settings)
    verification = run_write_verification(
        workspace=workspace,
        allowed_directories=list(settings.allowed_directories or [workspace]),
        goal=result.state.goal,
        tool_events=list(summary.tool_events or []),
        require_verification=require_verification,
        data_dir=getattr(settings, "data_dir", None),
    )
    outputs = dict(result.outputs)
    outputs["write_verification"] = verification
    if verification.get("writes_detected") and not verification.get("ok"):
        updated = result.state.model_copy(
            update={
                "phase": RunPhase.FAILED,
                "transition_reason": str(verification.get("summary") or "Developer write verification failed."),
                "current_plan": {
                    **result.state.current_plan,
                    "write_verification": verification,
                },
            },
            deep=True,
        )
        return result.model_copy(
            update={
                "state": updated,
                "finished": True,
                "message": updated.transition_reason,
                "outputs": outputs,
            },
            deep=True,
        )
    updated = result.state.model_copy(
        update={
            "current_plan": {
                **result.state.current_plan,
                "write_verification": verification,
                "diff_preview": verification.get("diff_preview"),
            },
        },
        deep=True,
    )
    return result.model_copy(update={"state": updated, "outputs": outputs}, deep=True)


def _plan_step_status(phase: RunPhase) -> str:
    if phase == RunPhase.COMPLETED:
        return "succeeded"
    if phase == RunPhase.AWAITING_APPROVAL:
        return "awaiting_approval"
    if phase in {RunPhase.FAILED, RunPhase.DENIED, RunPhase.CANCELLED}:
        return phase.value
    return "running"


def _mark_plan_steps_status(plan: dict, status: str, *, step_id: str | None = None) -> dict:
    updated = {**plan}
    steps: list[dict] = []
    for step in list(plan.get("steps") or []):
        if not isinstance(step, dict):
            continue
        if step_id is None or step.get("id") == step_id:
            steps.append({**step, "status": status})
        else:
            steps.append(step)
    updated["steps"] = steps
    return updated
