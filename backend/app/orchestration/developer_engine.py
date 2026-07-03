from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Any

from app.agents.delegation_metadata import developer_engine_capabilities
from app.config import PROJECT_ROOT, AppSettings
from app.core import db
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus
from app.integrations.lengrvis_code import (
    allowed_tools_for_developer,
    validate_allowed_tools,
)
from app.orchestration.developer_write_guard import run_write_verification
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import (
    NON_EXECUTABLE_RUN_PHASES,
    EngineSelection,
    EngineTurnResult,
    RunPhase,
    RunState,
)
from app.orchestration.lengrvis_code_config import LENGRVIS_CODE_DISPLAY_NAME, LengrvisCodeConfig
from app.orchestration.lengrvis_code_runner import (
    LengrvisCodeStreamSummary,
    cancel_lengrvis_code_run,
    lengrvis_code_summary_to_turn_result,
    run_lengrvis_code,
)
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

DEVELOPER_TOOL_NAME = "developer.lengrvis_code"
DEFAULT_DEVELOPER_RUN_TIMEOUT_SECONDS = 300.0


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
        self.store = store if store is not None else default_run_store
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
        db.init_db()
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
        task = _create_developer_task_shell(goal, mode, run_id="", metadata=task_metadata)
        step_status = StepStatus.FAILED if tool_safety_error else StepStatus.PENDING
        step = _developer_plan_step(
            task,
            allowed_tools=allowed_tools,
            writes_enabled=writes_enabled and not tool_safety_error,
            status=step_status,
        )
        plan = Plan(
            task_id=task.id,
            goal=goal,
            assumptions=[f"Executed through {LENGRVIS_CODE_DISPLAY_NAME} via ToolRuntime."],
            steps=[step],
            created_by_agent="DeveloperExecutionEngine",
        )
        db.upsert_model("plans", plan)
        phase = RunPhase.FAILED if tool_safety_error else RunPhase.PLANNING
        state = RunState(
            run_id=self.store.new_id("devrun"),
            engine="developer",
            phase=phase,
            goal=goal,
            mode=mode,
            task_id=task.id,
            transition_reason=tool_safety_error or f"developer {LENGRVIS_CODE_DISPLAY_NAME} run created",
            current_plan={
                "summary": (
                    f"Run {LENGRVIS_CODE_DISPLAY_NAME} headless with Lengrvis-controlled "
                    "OpenAI config and tool permissions."
                ),
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
                "task_id": task.id,
                "plan_id": plan.id,
                **({"safety_error": tool_safety_error} if tool_safety_error else {}),
                "steps": [
                    {
                        "id": step.id,
                        "logical_id": "lengrvis_code_run",
                        "tool": DEVELOPER_TOOL_NAME,
                        "status": "failed" if tool_safety_error else "pending",
                    },
                    *(
                        [{"id": "write_verification", "tool": "developer_write_guard", "status": "pending"}]
                        if writes_enabled and not tool_safety_error
                        else []
                    ),
                ],
            },
        )
        task.metadata["run_id"] = state.run_id
        db.upsert_model("tasks", task)
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        if state.phase == RunPhase.PAUSED:
            state = state.model_copy(
                update={
                    "phase": RunPhase.RUNNING,
                    "transition_reason": f"developer {LENGRVIS_CODE_DISPLAY_NAME} run resumed",
                },
                deep=True,
            )
            return self.store.put(state)
        return state

    async def cancel_run(self, run_id: str) -> RunState:
        db.init_db()
        await cancel_lengrvis_code_run(run_id)
        state = self.store.get(run_id)
        if state.task_id:
            task_data = db.fetch_one("tasks", state.task_id)
            if task_data:
                task = Task.model_validate(task_data)
                _DeveloperRuntimeAdapter(self.settings)._set_status(
                    task,
                    TaskStatus.CANCELLED,
                    final_summary=f"developer {LENGRVIS_CODE_DISPLAY_NAME} run cancelled",
                )
        updated = state.model_copy(
            update={
                "phase": RunPhase.CANCELLED,
                "transition_reason": f"developer {LENGRVIS_CODE_DISPLAY_NAME} run cancelled",
                "current_plan": _mark_plan_steps_status(state.current_plan, "cancelled"),
            },
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
        db.init_db()
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
                        tool for tool in plan_tools if tool.split("(", 1)[0] not in WRITE_CAPABLE_ALLOWED_TOOLS
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
            summary = await self._run_lengrvis_code_via_tool_runtime(
                state,
                launch_config=launch_config,
                writes_enabled=writes_enabled,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: external CLI failures become run failures.
            result = lengrvis_code_summary_to_turn_result(
                state,
                LengrvisCodeStreamSummary(
                    launch_error=f"Unexpected {LENGRVIS_CODE_DISPLAY_NAME} adapter failure: {exc}"
                ),
            )
            result.state.current_plan = _mark_plan_steps_status(result.state.current_plan, "failed")
            return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)

        result = lengrvis_code_summary_to_turn_result(state, summary)
        if writes_enabled and summary.permission_denials:
            result = _await_write_approval(result, summary)
        elif writes_enabled and result.state.phase == RunPhase.COMPLETED:
            result = _apply_write_verification(result, summary, settings=self.settings, writes_enabled=writes_enabled)
        step_status = _plan_step_status(result.state.phase)
        result.state.current_plan = _mark_plan_steps_status(
            result.state.current_plan, step_status, step_id="lengrvis_code_run"
        )
        if writes_enabled and result.outputs.get("write_verification"):
            verify_ok = bool(result.outputs["write_verification"].get("ok"))
            result.state.current_plan = _mark_plan_steps_status(
                result.state.current_plan,
                "succeeded" if verify_ok else "failed",
                step_id="write_verification",
            )
        return result.model_copy(update={"state": self.store.put(result.state)}, deep=True)

    async def _run_lengrvis_code_via_tool_runtime(
        self,
        state: RunState,
        *,
        launch_config: LengrvisCodeConfig,
        writes_enabled: bool,
    ) -> LengrvisCodeStreamSummary:
        adapter = _DeveloperRuntimeAdapter(self.settings)
        task, plan, step = _developer_runtime_models(
            state,
            settings=self.settings,
            allowed_tools=launch_config.allowed_tools,
            writes_enabled=writes_enabled,
        )
        runtime = TaskRuntimeContext.from_task(task, self.settings, adapter.bus)
        runtime.allowed_directories = list(self.settings.allowed_directories or [_default_workspace(self.settings)])
        runtime.extra_context.update(
            {
                "run_id": state.run_id,
                "task_id": task.id,
                "step_id": step.id,
                "explicit_path_scope": state.goal,
                "_developer_lengrvis_code_config": launch_config,
            }
        )
        tool = _developer_lengrvis_code_tool()
        review = await ToolRuntime(adapter).review_and_maybe_prepare_approval(task, step, tool, runtime)
        if review.kind != "allowed":
            db.upsert_model("plans", plan)
            if review.kind in {"step_denied", "fatal_denied"}:
                return LengrvisCodeStreamSummary(
                    result={"is_error": True, "errors": [task.final_summary or f"{DEVELOPER_TOOL_NAME} denied."]},
                )
            return LengrvisCodeStreamSummary(
                result={"is_error": True, "errors": [task.final_summary or f"{DEVELOPER_TOOL_NAME} unavailable."]},
            )

        execution = await ToolRuntime(adapter).execute_allowed(
            task,
            step,
            tool,
            runtime,
            approved_args={
                "prompt": _prompt_from_goal(state.goal, writes_enabled=writes_enabled),
                "cwd": _default_workspace(self.settings),
                "run_id": state.run_id,
                "allowed_tools": list(launch_config.allowed_tools),
                "max_turns": launch_config.max_turns,
                "permission_mode": launch_config.permission_mode,
            },
        )
        db.upsert_model("plans", plan)
        output = execution.result.output if execution.result is not None else {}
        summary_payload = output.get("summary")
        if isinstance(summary_payload, LengrvisCodeStreamSummary):
            return summary_payload
        if isinstance(summary_payload, dict):
            try:
                return LengrvisCodeStreamSummary(**summary_payload)
            except TypeError:
                pass
        if execution.result is not None and not execution.result.ok:
            return LengrvisCodeStreamSummary(result={"is_error": True, "errors": [execution.result.error]})
        return LengrvisCodeStreamSummary(result={"is_error": False, "result": "Developer run completed."})


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


class _DeveloperRuntimeAdapter:
    name = "DeveloperExecutionEngine"

    def __init__(self, settings: AppSettings) -> None:
        from app.agents.safety_review_agent import SafetyReviewAgent
        from app.orchestration.agent_bus import AgentBus

        self.settings = settings
        self.bus = AgentBus()
        self.safety = SafetyReviewAgent(self.bus, settings=settings)
        self.browser_activity_review = None

    def _set_status(self, task: Task, status: TaskStatus, *, final_summary: str | None = None) -> Task:
        from app.orchestration.state_machine import safe_transition

        task = safe_transition(task, status, actor=self.name)
        if final_summary is not None:
            task.final_summary = final_summary
        db.upsert_model("tasks", task)
        return task

    def _supervise_new_agent_messages(self, task_id: str, stage: str) -> bool:
        messages = [message for message in self.bus.get_messages(task_id) if message.from_agent != self.safety.name]
        if not messages:
            return True
        return self.safety.review_agent_messages_batch(messages, stage).verdict.value != "deny"

    async def _capture_step_frame(self, task: Task, step: PlanStep, phase: str) -> dict[str, Any]:  # noqa: ARG002
        return {"enabled": False, "ok": True, "phase": phase, "reason": "developer_engine_no_screen_capture"}

    def _publish_step_recording(
        self,
        task: Task,
        step: PlanStep,
        frames: list[dict[str, Any]],
        *,
        tool_name: str,
        agent: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    def _friendly_tool_error(self, error: str) -> str:
        return f"Developer engine failed: {error}" if error else "Developer engine failed."

    async def _reflect_on_step(self, task: Task, step: PlanStep, result) -> None:  # noqa: ANN001, ARG002
        return None


def _create_developer_task_shell(
    goal: str,
    mode: str,
    *,
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> Task:
    task = Task(
        user_goal=goal,
        mode=mode,
        status=TaskStatus.PLANNING,
        metadata={"engine": "developer", "run_id": run_id, **dict(metadata or {})},
    )
    db.upsert_model("tasks", task)
    return task


def _developer_plan_step(
    task: Task,
    *,
    allowed_tools: tuple[str, ...],
    writes_enabled: bool,
    status: StepStatus = StepStatus.PENDING,
) -> PlanStep:
    return PlanStep(
        task_id=task.id,
        order=1,
        agent_name="DeveloperExecutionEngine",
        tool_name=DEVELOPER_TOOL_NAME,
        description=f"Run {LENGRVIS_CODE_DISPLAY_NAME} through ToolRuntime.",
        args={
            "workspace_path": _default_workspace(AppSettings()),
            "allowed_tools": list(allowed_tools),
            "writes_enabled": writes_enabled,
        },
        expected_observation=f"{LENGRVIS_CODE_DISPLAY_NAME} stream-json result captured.",
        risk_level=RiskLevel.R1_OPEN_ONLY,
        status=status,
        tool_effects=["read", "execute_subprocess"],
        resource_kinds=["workspace", "developer_runtime"],
        trust_tier="builtin",
    )


def _developer_runtime_models(
    state: RunState,
    *,
    settings: AppSettings,
    allowed_tools: tuple[str, ...],
    writes_enabled: bool,
) -> tuple[Task, Plan, PlanStep]:
    task = _task_for_developer_state(state)
    step_id = _developer_step_id(state.current_plan)
    plan = _plan_for_developer_task(task, state)
    step = next((item for item in plan.steps if item.id == step_id), None)
    if step is None:
        step = _developer_plan_step(
            task,
            allowed_tools=allowed_tools,
            writes_enabled=writes_enabled,
        )
        if step_id:
            step.id = step_id
        plan.steps.insert(0, step)
    step.args = {
        "workspace_path": _default_workspace(settings),
        "allowed_tools": list(allowed_tools),
        "writes_enabled": writes_enabled,
    }
    step.tool_name = DEVELOPER_TOOL_NAME
    step.agent_name = "DeveloperExecutionEngine"
    step.risk_level = RiskLevel.R1_OPEN_ONLY
    step.tool_effects = ["read", "execute_subprocess"]
    step.resource_kinds = ["workspace", "developer_runtime"]
    step.trust_tier = "builtin"
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    return task, plan, step


def _task_for_developer_state(state: RunState) -> Task:
    if state.task_id:
        row = db.fetch_one("tasks", state.task_id)
        if row:
            return Task.model_validate(row)
    return _create_developer_task_shell(state.goal, state.mode, run_id=state.run_id)


def _plan_for_developer_task(task: Task, state: RunState) -> Plan:
    plan_id = str(state.current_plan.get("plan_id") or "")
    if plan_id:
        row = db.fetch_one("plans", plan_id)
        if row:
            return Plan.model_validate(row)
    rows = db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)
    if rows:
        return Plan.model_validate(rows[0])
    return Plan(
        task_id=task.id,
        goal=task.user_goal,
        assumptions=[f"Executed through {LENGRVIS_CODE_DISPLAY_NAME} via ToolRuntime."],
        created_by_agent="DeveloperExecutionEngine",
    )


def _developer_step_id(plan: dict[str, Any]) -> str:
    for raw_step in plan.get("steps") or []:
        if isinstance(raw_step, dict) and raw_step.get("logical_id") == "lengrvis_code_run":
            return str(raw_step.get("id") or "")
    return ""


def _developer_lengrvis_code_tool() -> ToolDefinition:
    return ToolDefinition(
        name=DEVELOPER_TOOL_NAME,
        description=f"Run {LENGRVIS_CODE_DISPLAY_NAME} headless inside the authorized workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string"},
                "run_id": {"type": "string"},
            },
            "required": ["prompt", "cwd", "run_id"],
            "additionalProperties": True,
        },
        output_schema={},
        risk_level=RiskLevel.R1_OPEN_ONLY,
        agent_owner="DeveloperExecutionEngine",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_execute_lengrvis_code_tool,
        read_only=False,
        concurrency_safe=False,
        concurrency_key="developer.lengrvis_code",
        destructive=False,
        max_result_size=80000,
        result_summary=_developer_tool_summary,
        capabilities=["developer_runtime", "subprocess"],
        effects=["read", "execute_subprocess"],
        resource_kinds=["workspace", "developer_runtime"],
        trust_tier="builtin",
        sensitive_arg_keys=["prompt"],
        tool_version="1",
    )


def _execute_lengrvis_code_tool(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = context.get("settings")
    if not isinstance(settings, AppSettings):
        settings = AppSettings()
    config = context.get("_developer_lengrvis_code_config")
    if not isinstance(config, LengrvisCodeConfig):
        config = LengrvisCodeConfig()
    run_id = str(args.get("run_id") or context.get("run_id") or "")
    abort_event = context.get("_tool_abort_event")
    summary = _run_lengrvis_code_sync(
        str(args.get("prompt") or ""),
        cwd=str(args.get("cwd") or _default_workspace(settings)),
        settings=settings,
        config=config,
        run_id=run_id,
        abort_event=abort_event if isinstance(abort_event, threading.Event) else None,
    )
    output = _developer_summary_output(summary)
    if summary.is_error:
        output["error"] = output.get("error") or summary.error_classification or "developer_runtime_failed"
    return output


def _run_lengrvis_code_sync(
    prompt: str,
    *,
    cwd: str,
    settings: AppSettings,
    config: LengrvisCodeConfig,
    run_id: str,
    abort_event: threading.Event | None,
) -> LengrvisCodeStreamSummary:
    timeout_seconds = _developer_run_timeout(settings)

    async def _runner() -> LengrvisCodeStreamSummary:
        cancel_task: asyncio.Task[None] | None = None
        run_task: asyncio.Task[LengrvisCodeStreamSummary] | None = None

        async def _poll_abort() -> None:
            if abort_event is None:
                return
            while not abort_event.is_set():
                await asyncio.sleep(0.05)
            await cancel_lengrvis_code_run(run_id)

        if abort_event is not None:
            cancel_task = asyncio.create_task(_poll_abort())
        try:
            run_task = asyncio.create_task(
                run_lengrvis_code(
                    prompt,
                    cwd=cwd,
                    settings=settings,
                    config=config,
                    run_id=run_id,
                )
            )
            if timeout_seconds is None or timeout_seconds <= 0:
                return await run_task
            try:
                return await asyncio.wait_for(asyncio.shield(run_task), timeout=timeout_seconds)
            except TimeoutError:
                cancelled_external = False
                if run_id:
                    cancelled_external = await cancel_lengrvis_code_run(run_id)
                if not cancelled_external:
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
                    return _developer_timeout_summary(timeout_seconds)
                try:
                    await asyncio.wait_for(asyncio.shield(run_task), timeout=2.0)
                except TimeoutError:
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
                else:
                    await asyncio.gather(run_task, return_exceptions=True)
                return _developer_timeout_summary(timeout_seconds)
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
                await asyncio.gather(cancel_task, return_exceptions=True)

    return _run_developer_coro_sync(_runner(), timeout_seconds=timeout_seconds)


def _run_developer_coro_sync(
    coro,
    *,
    timeout_seconds: float | None = DEFAULT_DEVELOPER_RUN_TIMEOUT_SECONDS,
) -> LengrvisCodeStreamSummary:
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(asyncio.run, coro)
        try:
            guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 3
            return future.result(timeout=guard_timeout)
        finally:
            if not future.done():
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        return _developer_timeout_summary(timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: tool body failures should be reported inline.
        return LengrvisCodeStreamSummary(
            launch_error=f"{LENGRVIS_CODE_DISPLAY_NAME} run failed: {exc}",
            result={"is_error": True, "result": str(exc)},
        )


def _developer_run_timeout(settings: AppSettings) -> float:
    configured = getattr(settings, "tool_timeout_seconds", None)
    try:
        timeout = float(configured) if configured is not None else DEFAULT_DEVELOPER_RUN_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = DEFAULT_DEVELOPER_RUN_TIMEOUT_SECONDS
    return timeout


def _developer_timeout_summary(timeout_seconds: float | None) -> LengrvisCodeStreamSummary:
    if timeout_seconds is None or timeout_seconds <= 0:
        message = f"{LENGRVIS_CODE_DISPLAY_NAME} run timed out."
    else:
        message = f"{LENGRVIS_CODE_DISPLAY_NAME} run timed out after {timeout_seconds:.0f}s."
    return LengrvisCodeStreamSummary(
        launch_error=message,
        result={"is_error": True, "result": message},
    )


def _developer_summary_output(summary: LengrvisCodeStreamSummary) -> dict[str, Any]:
    payload = {
        "summary": summary,
        "ok": not summary.is_error,
        "cancelled": summary.cancelled,
        "assistant_text": summary.final_text,
        "tool_events": list(summary.tool_events or []),
        "system_events": list(summary.system_events or []),
        "result": summary.result,
        "permission_denials": summary.permission_denials,
        "error_classification": summary.error_classification,
        "returncode": summary.returncode,
        "runtime_health": summary.runtime_health,
    }
    if summary.cancelled:
        payload["error"] = "cancelled"
    elif summary.is_error:
        payload["error"] = summary.error_classification or "developer_runtime_failed"
    return payload


def _developer_tool_summary(output: dict[str, Any]) -> str:
    text = str(output.get("assistant_text") or "").strip()
    if text:
        return text[:500]
    if output.get("cancelled"):
        return f"{LENGRVIS_CODE_DISPLAY_NAME} run was cancelled."
    if output.get("error"):
        return f"{LENGRVIS_CODE_DISPLAY_NAME} run failed: {output.get('error')}"
    return f"{LENGRVIS_CODE_DISPLAY_NAME} run completed."


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
        "Write/Edit tools are enabled inside the workspace only; every mutation must pass "
        "Lengrvis Code permission prompts "
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
