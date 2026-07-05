from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from app.core import db
from app.core.audit import record
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.llm.registry import get_effective_settings
from app.orchestration import os_execution_state as os_state
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import (
    NON_EXECUTABLE_RUN_PHASES,
    TERMINAL_RUN_PHASES,
    EngineSelection,
    EngineTurnResult,
    LargeResultRef,
    RunObservation,
    RunPhase,
    RunState,
)
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.os_reflection import (
    OSReflectionDecider,
    OSReflectionInput,
    apply_reflection_decision,
    reflection_count_updates,
)
from app.orchestration.plan_snapshot import snapshot_step, write_back_step
from app.orchestration.resource_state import clear_task_read_states
from app.orchestration.step_phase import set_step_status

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent


OSEventHook = Callable[[str, dict[str, Any]], Awaitable[None] | None]

# Per-run orchestrator binding (R4-H2). run_plan_turn binds the run's
# orchestrator here for the duration of the turn; asyncio.create_task copies
# the context, so parallel step tasks inherit the same binding.
_CURRENT_RUN_ORCHESTRATOR: ContextVar[OrchestratorAgent | None] = ContextVar(
    "os_engine_current_run_orchestrator",
    default=None,
)

class OSExecutionEngine(ExecutionEngine):
    """Turn-based OS/app/browser execution engine.

    This engine coordinates RunState, ready-step selection, progress hooks, and
    turn outputs. It intentionally delegates validation, path authorization,
    browser privacy checks, R4 denial, dry-run approval creation, write locks,
    approval bindings, post-tool review, and result budgeting to the existing
    StepExecutionHandler/ToolRuntime path.
    """

    name = "os"

    def __init__(
        self,
        orchestrator: OrchestratorAgent | None = None,
        *,
        orchestrator_factory: Callable[[], OrchestratorAgent] | None = None,
        store: InMemoryRunStore | None = None,
        event_hook: OSEventHook | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.orchestrator_factory = orchestrator_factory
        self.store = store if store is not None else default_run_store
        self.event_hook = event_hook
        self._orchestrators_by_run: dict[str, OrchestratorAgent] = {}
        self._run_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        settings = get_effective_settings()
        self.reflection_decider = OSReflectionDecider(
            max_per_run=settings.os_reflection_max_per_run,
            max_per_step=settings.os_reflection_max_per_step,
        )

    async def start_run(
        self,
        goal: str,
        mode: str,
        engine: EngineSelection = "auto",
        *,
        task_metadata: dict[str, Any] | None = None,
    ) -> RunState:  # noqa: ARG002
        orchestrator = self._new_orchestrator()
        task = orchestrator.create_task_shell(goal, mode, metadata=dict(task_metadata or {}))
        state = RunState(
            run_id=self.store.new_id("osrun"),
            engine="os",
            phase=RunPhase.PLANNING,
            goal=goal,
            mode=mode,
            task_id=task.id,
            transition_reason="os run created",
            current_plan={"task_id": task.id, "steps": []},
        )
        self._orchestrators_by_run[state.run_id] = orchestrator
        orchestrator_registry.bind(task_id=task.id, orchestrator=orchestrator, run_id=state.run_id)
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        # Materialize + register the orchestrator now so the run-service bridge
        # subscribes to the SAME bus the engine will publish on. Without this a
        # fresh engine resolves its orchestrator lazily inside run_turn, while
        # the bridge already fell back to a throwaway AgentBus -> the resumed
        # run's live timeline/approval events are silently dropped.
        self._orchestrator_for_state(state)
        if state.phase == RunPhase.PAUSED:
            resumed = state.model_copy(
                update={"phase": RunPhase.RUNNING, "transition_reason": "os run resumed"}, deep=True
            )
            return self.store.put(resumed)
        if state.phase == RunPhase.AWAITING_APPROVAL:
            return state.model_copy(
                update={"transition_reason": "os run is waiting for external approval"},
                deep=True,
            )
        return state

    async def cancel_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        orchestrator = self._orchestrator_for_state(state)
        task_id = state.task_id or str(state.current_plan.get("task_id") or "")
        run_tasks = list(self._run_tasks.pop(run_id, set()))
        for work in run_tasks:
            work.cancel()
        if run_tasks:
            await asyncio.gather(*run_tasks, return_exceptions=True)
        if task_id:
            clear_task_read_states(task_id)
            task_data = db.fetch_one("tasks", task_id)
            if task_data:
                orchestrator._set_status(
                    Task.model_validate(task_data), TaskStatus.CANCELLED, final_summary="Run cancelled."
                )
        cancelled = state.model_copy(
            update={"phase": RunPhase.CANCELLED, "transition_reason": "os run cancelled"},
            deep=True,
        )
        orchestrator_registry.release_run(run_id)
        return self.store.put(cancelled)

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in NON_EXECUTABLE_RUN_PHASES:
            return EngineTurnResult(state=state, finished=True, message=f"Run is already {state.phase.value}.")

        orchestrator = self._orchestrator_for_state(state)
        task = await self._task_for_state(orchestrator, state)
        plan = await self._plan_for_state(orchestrator, task, state)
        if task.status in {TaskStatus.CANCELLED, TaskStatus.DENIED, TaskStatus.FAILED}:
            updated = self._state_from_task_plan(
                state, task, plan, phase=self._phase_for_task_plan(task, plan), reason=task.final_summary
            )
            return EngineTurnResult(state=self.store.put(updated), finished=True, message=task.final_summary)
        return await self.run_plan_turn(task, plan, state=state)

    async def process_plan(
        self,
        task: Task,
        plan: Plan,
        *,
        state: RunState | None = None,
        event_hook: OSEventHook | None = None,
        max_turns: int | None = None,
    ) -> EngineTurnResult:
        """Run an already-reviewed plan until it completes, pauses, or waits."""

        current = state or self._initial_state_for_plan(task, plan)
        orchestrator = self._orchestrator_for_state(current)
        self._orchestrators_by_run[current.run_id] = orchestrator
        orchestrator_registry.bind(task_id=task.id, orchestrator=orchestrator, run_id=current.run_id)
        turns_remaining = max_turns if max_turns is not None else max(1, (len(plan.steps) + 1) * 4 + 32)
        last_result: EngineTurnResult | None = None
        while turns_remaining > 0:
            turns_remaining -= 1
            last_result = await self.run_plan_turn(task, plan, state=current, event_hook=event_hook)
            current = last_result.state
            if last_result.finished:
                return last_result
            if not last_result.outputs.get("selected_step_ids") and last_result.outputs.get("outcome") != "reflected":
                return last_result

        message = "OS execution engine reached its per-plan turn limit."
        orchestrator._set_status(task, TaskStatus.FAILED, final_summary=message)
        failed_state = self._state_from_task_plan(current, task, plan, phase=RunPhase.FAILED, reason=message)
        stored = self.store.put(failed_state)
        return EngineTurnResult(state=stored, finished=True, message=message)

    async def run_plan_turn(
        self,
        task: Task,
        plan: Plan,
        *,
        state: RunState | None = None,
        event_hook: OSEventHook | None = None,
    ) -> EngineTurnResult:
        current = state or self._initial_state_for_plan(task, plan)
        # Resolve the run-bound orchestrator and pin it for the whole turn so
        # every helper in the chain (selection, parallel execution, recovery,
        # reflection, finish) resolves the same instance (R4-H2).
        orchestrator = self._orchestrator_for_state(current)
        token = _CURRENT_RUN_ORCHESTRATOR.set(orchestrator)
        try:
            return await self._run_plan_turn_bound(orchestrator, task, plan, current, event_hook)
        finally:
            _CURRENT_RUN_ORCHESTRATOR.reset(token)

    async def _run_plan_turn_bound(
        self,
        orchestrator: OrchestratorAgent,
        task: Task,
        plan: Plan,
        current: RunState,
        event_hook: OSEventHook | None,
    ) -> EngineTurnResult:
        turn = current.turn_count + 1
        outputs: dict[str, Any] = {"events": [], "turn": turn, "task_id": task.id, "plan_id": plan.id}
        hook = event_hook or self.event_hook
        context = self._build_turn_context(current, task, plan)
        observations_by_step = self._observations_by_step(current)

        await self._emit(outputs, hook, "turn.started", {"turn": turn, "task_id": task.id, "plan_id": plan.id})

        try:
            by_id, _dependents = orchestrator._build_step_graph(plan)
        except ValueError as exc:
            return await self._handle_step_graph_error(
                current,
                task,
                plan,
                outputs,
                hook,
                turn=turn,
                context=context,
                error=str(exc),
            )

        selection = await self._select_turn_steps(
            current,
            task,
            plan,
            outputs,
            hook,
            turn=turn,
            context=context,
            by_id=by_id,
        )
        if isinstance(selection, EngineTurnResult):
            return selection
        selected, threaded_tools = selection

        step_outcomes = await self._execute_selected_steps(
            task,
            plan,
            selected,
            context,
            observations_by_step,
            threaded_tools=threaded_tools,
        )
        outputs["step_outcomes"] = [self._step_outcome_payload(step, outcome) for step, outcome in step_outcomes]

        current = await self._record_step_results(
            current,
            task,
            outputs,
            hook,
            turn=turn,
            step_outcomes=step_outcomes,
            observations_by_step=observations_by_step,
        )

        return await self._resolve_turn_outcome(
            current,
            task,
            plan,
            outputs,
            hook,
            turn=turn,
            context=context,
            step_outcomes=step_outcomes,
        )

    async def _handle_step_graph_error(
        self,
        current: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        turn: int,
        context: dict[str, Any],
        error: str,
    ) -> EngineTurnResult:
        orchestrator = self._orchestrator()
        record("task.step_graph_invalid", orchestrator.name, {"error": error}, task_id=task.id)
        reflection_state = await self._maybe_reflect(
            current,
            task,
            plan,
            outputs,
            hook,
            turn=turn,
            context=context,
            graph_error=error,
        )
        if reflection_state is not None:
            return reflection_state
        for step in plan.steps:
            if step.status == StepStatus.PENDING:
                set_step_status(step, StepStatus.FAILED, actor="OSExecutionEngine")
        orchestrator._set_status(task, TaskStatus.FAILED, final_summary=error)
        return await self._finish_turn(
            current,
            task,
            plan,
            outputs,
            hook,
            phase=RunPhase.FAILED,
            outcome="failed",
            message=error,
            finished=True,
        )

    async def _select_turn_steps(
        self,
        current: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        turn: int,
        context: dict[str, Any],
        by_id: dict[str, PlanStep],
    ) -> EngineTurnResult | tuple[list[PlanStep], bool]:
        orchestrator = self._orchestrator()
        pending = self._pending_step_ids(plan)
        ready = orchestrator._ready_steps(pending, by_id)
        if not ready:
            reflection_state = await self._maybe_reflect(
                current,
                task,
                plan,
                outputs,
                hook,
                turn=turn,
                context=context,
                no_ready=bool(pending),
            )
            if reflection_state is not None:
                return reflection_state
            orchestrator._mark_blocked_steps(pending, by_id)
            return await self._finish_from_plan(current, task, plan, outputs, hook, turn)

        threaded_tools = self._parallel_batch_allowed(task, ready)
        selected = ready if threaded_tools else ready[:1]
        selected_ids = [step.id for step in selected]
        outputs["selected_step_ids"] = selected_ids
        await self._emit(
            outputs,
            hook,
            "step.selected",
            {
                "turn": turn,
                "task_id": task.id,
                "plan_id": plan.id,
                "step_ids": selected_ids,
                "parallel": threaded_tools,
            },
        )
        return selected, threaded_tools

    async def _record_step_results(
        self,
        current: RunState,
        task: Task,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        turn: int,
        step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]],
        observations_by_step: dict[str, ToolResult],
    ) -> RunState:
        observations = list(current.observations)
        large_refs = list(current.large_result_refs)
        for step, outcome in step_outcomes:
            if outcome.result is None:
                continue
            observations_by_step[step.id] = outcome.result
            observations.append(self._run_observation(turn, step, outcome))
            large_ref = self._large_result_ref(outcome.result)
            if large_ref is not None:
                large_refs.append(large_ref)
            await self._emit(
                outputs,
                hook,
                "tool.result",
                {
                    "turn": turn,
                    "task_id": task.id,
                    "step_id": step.id,
                    "outcome": outcome.kind,
                    "tool_result": outcome.result.model_dump(mode="json"),
                },
            )

        updated = current.model_copy(update={"observations": observations, "large_result_refs": large_refs}, deep=True)
        return self.store.trim_state_history(updated)

    async def _resolve_turn_outcome(
        self,
        current: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        turn: int,
        context: dict[str, Any],
        step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]],
    ) -> EngineTurnResult:
        stop_outcome = self._stop_outcome(step_outcomes)
        if stop_outcome == "waiting_approval":
            return await self._finish_turn(
                current,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.AWAITING_APPROVAL,
                outcome=stop_outcome,
                message="Plan generated and waiting for approval on modifying steps.",
                finished=True,
            )
        if stop_outcome == "paused":
            return await self._finish_turn(
                current,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.PAUSED,
                outcome=stop_outcome,
                message="A subagent requested plan revision; automatic replanning was not repeated for this step.",
                finished=True,
            )
        if stop_outcome in {"denied", "failed"}:
            if stop_outcome == "failed":
                reflection_state = await self._maybe_reflect(
                    current,
                    task,
                    plan,
                    outputs,
                    hook,
                    turn=turn,
                    context=context,
                    step_outcomes=step_outcomes,
                )
                if reflection_state is not None:
                    return reflection_state
            self._mark_blocked_pending_steps(plan)
            phase = RunPhase.DENIED if stop_outcome == "denied" else RunPhase.FAILED
            return await self._finish_turn(
                current,
                task,
                plan,
                outputs,
                hook,
                phase=phase,
                outcome=stop_outcome,
                message=task.final_summary or f"Plan {stop_outcome}.",
                finished=True,
            )
        if any(outcome.kind == "failed" for _step, outcome in step_outcomes):
            reflection_state = await self._maybe_reflect(
                current,
                task,
                plan,
                outputs,
                hook,
                turn=turn,
                context=context,
                step_outcomes=step_outcomes,
            )
            if reflection_state is not None:
                return reflection_state

        return await self._finish_from_plan(current, task, plan, outputs, hook, turn)

    async def _maybe_reflect(
        self,
        state: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        turn: int,
        context: dict[str, Any],
        step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]] | None = None,
        no_ready: bool = False,
        graph_error: str = "",
    ) -> EngineTurnResult | None:
        run_reflection_count = int(state.recovery_count_by_step.get("__os_reflection_run__", 0))
        reflection_input = OSReflectionInput(
            task=task,
            plan=plan,
            turn=turn,
            run_reflection_count=run_reflection_count,
            step_reflection_counts=dict(state.recovery_count_by_step),
            step_outcomes=step_outcomes or [],
            no_ready=no_ready,
            graph_error=graph_error,
        )
        if not self.reflection_decider.should_reflect(reflection_input):
            return None
        await self._emit(
            outputs,
            hook,
            "os.reflection.started",
            {
                "turn": turn,
                "task_id": task.id,
                "plan_id": plan.id,
                "reason": graph_error or "observed failed or ambiguous execution state",
                "run_reflection_count": run_reflection_count,
            },
        )
        decision = await self.reflection_decider.decide(reflection_input, self._orchestrator(), context)
        outputs.setdefault("os_reflections", []).append(
            {
                "action": decision.action,
                "reason": decision.reason,
                "target_step_ids": decision.target_step_ids,
                "step_count": len(decision.steps),
            }
        )
        await self._emit(
            outputs,
            hook,
            "os.reflection.decided",
            {
                "turn": turn,
                "task_id": task.id,
                "plan_id": plan.id,
                "action": decision.action,
                "reason": decision.reason,
                "target_step_ids": decision.target_step_ids,
            },
        )
        if decision.action in {"continue", "finish"}:
            return None
        updates = apply_reflection_decision(task, plan, decision, self._orchestrator())
        next_counts = reflection_count_updates(state.recovery_count_by_step, decision)
        if decision.action in {"add_steps", "replace_pending"} and updates.get("added_step_ids"):
            next_state = self._state_from_task_plan(
                state.model_copy(update={"recovery_count_by_step": next_counts}, deep=True),
                task,
                plan,
                phase=RunPhase.RUNNING,
                reason=decision.reason or "OS reflection updated pending plan.",
                turn_count=turn,
            )
            db.upsert_model("plans", plan)
            stored = self.store.put(next_state)
            await self._emit(
                outputs,
                hook,
                "turn.completed",
                {
                    "turn": turn,
                    "task_id": task.id,
                    "plan_id": plan.id,
                    "outcome": "reflected",
                    "added_step_ids": updates.get("added_step_ids", []),
                },
            )
            outputs["outcome"] = "reflected"
            outputs["current_plan"] = stored.current_plan
            return EngineTurnResult(
                state=stored,
                finished=False,
                message=decision.reason or "OS reflection updated the plan.",
                outputs=outputs,
            )
        if decision.action == "ask_user":
            paused_state = state.model_copy(update={"recovery_count_by_step": next_counts}, deep=True)
            return await self._finish_turn(
                paused_state,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.PAUSED,
                outcome="paused",
                message=decision.reason or "OS reflection needs user input before continuing.",
                finished=True,
            )
        if decision.action == "fail":
            failed_state = state.model_copy(update={"recovery_count_by_step": next_counts}, deep=True)
            return await self._finish_turn(
                failed_state,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.FAILED,
                outcome="failed",
                message=decision.reason or "OS reflection could not repair the plan.",
                finished=True,
            )
        return None

    async def _execute_selected_steps(
        self,
        task: Task,
        plan: Plan,
        selected: list[PlanStep],
        context: dict[str, Any],
        observations_by_step: dict[str, ToolResult],
        *,
        threaded_tools: bool,
    ) -> list[tuple[PlanStep, StepExecutionOutcome]]:
        if not threaded_tools or len(selected) <= 1:
            step = selected[0]
            outcome = await self._execute_one_step(
                task, plan, step, context, observations_by_step, threaded_tools=False
            )
            return [(step, outcome)]

        work: dict[asyncio.Task[StepExecutionOutcome], tuple[PlanStep, PlanStep, ToolResult | None]] = {}
        original_observations: dict[str, ToolResult | None] = {}
        run_id = str(context.get("run_id") or "")
        for step in selected:
            observation = self._dependency_observation(step, observations_by_step)
            original_observations[step.id] = observation
            step_context = copy.deepcopy(context)
            # Parallel executors mutate step fields across await points; hand each
            # one an isolated snapshot and write it back serially on completion
            # so siblings never observe (or persist) a half-updated step.
            isolated = snapshot_step(step)
            task_work = asyncio.create_task(
                self._orchestrator()._execute_step(
                    task, plan, isolated, step_context, observation, threaded_tools=True
                ),
                name=f"os-step-{step.id}",
            )
            if run_id:
                self._register_run_task(run_id, task_work)
            work[task_work] = (step, isolated, observation)

        results: list[tuple[PlanStep, StepExecutionOutcome]] = []
        stop_requested = False
        try:
            while work and not stop_requested:
                done_set, _ = await asyncio.wait(work.keys(), return_when=asyncio.FIRST_COMPLETED)
                for task_work in list(done_set):
                    step, isolated, observation = work.pop(task_work)
                    try:
                        raw_outcome = task_work.result()
                    except asyncio.CancelledError as exc:
                        raw_outcome = exc
                    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: step failures are normalized below.
                        raw_outcome = exc
                    if not isinstance(raw_outcome, BaseException):
                        write_back_step(step, isolated)
                    outcome = self._normalize_step_outcome(task, step, raw_outcome)
                    if outcome.result is not None:
                        observations_by_step[step.id] = outcome.result
                    results.append((step, outcome))
                    if outcome.kind in {
                        "step_denied",
                        "fatal_denied",
                        "fatal_failed",
                        "waiting_user_approval",
                        "revision_requested",
                    }:
                        stop_requested = True
            if stop_requested and work:
                # Drain like StepSchedulerHandler._drain_running_after_stop:
                # siblings that already finished must be written back and kept
                # in results, otherwise a resume re-runs their side effects.
                # cancel() is a no-op for done tasks; gather hands back their
                # real outcome instead of CancelledError.
                remaining = list(work.keys())
                for pending_work in remaining:
                    pending_work.cancel()
                raw_outcomes = await asyncio.gather(*remaining, return_exceptions=True)
                for task_work, raw_outcome in zip(remaining, raw_outcomes, strict=False):
                    step, isolated, observation = work.pop(task_work)
                    if isinstance(raw_outcome, asyncio.CancelledError):
                        continue
                    if not isinstance(raw_outcome, BaseException):
                        write_back_step(step, isolated)
                    outcome = self._normalize_step_outcome(task, step, raw_outcome)
                    if outcome.result is not None:
                        observations_by_step[step.id] = outcome.result
                    results.append((step, outcome))
                work.clear()
        except asyncio.CancelledError:
            for task_work in work:
                task_work.cancel()
            if work:
                await asyncio.gather(*work.keys(), return_exceptions=True)
            raise
        if not stop_requested:
            results = await self._recover_parallel_failures_serially(
                task,
                plan,
                results,
                context,
                original_observations,
            )
        return results

    async def _recover_parallel_failures_serially(
        self,
        task: Task,
        plan: Plan,
        results: list[tuple[PlanStep, StepExecutionOutcome]],
        context: dict[str, Any],
        original_observations: dict[str, ToolResult | None],
    ) -> list[tuple[PlanStep, StepExecutionOutcome]]:
        recovered: list[tuple[PlanStep, StepExecutionOutcome]] = []
        for step, outcome in results:
            if outcome.kind == "failed" and not self._defer_recovery_to_reflection(outcome):
                outcome = await self._orchestrator().recovery_handler.recover_failed_step(
                    task,
                    plan,
                    step,
                    outcome.result,
                    context,
                    original_observations.get(step.id),
                    threaded_tools=False,
                )
            recovered.append((step, outcome))
        return recovered

    async def _execute_one_step(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observations_by_step: dict[str, ToolResult],
        *,
        threaded_tools: bool,
    ) -> StepExecutionOutcome:
        observation = self._dependency_observation(step, observations_by_step)
        try:
            outcome = await self._orchestrator()._execute_step(
                task,
                plan,
                step,
                context,
                observation,
                threaded_tools=threaded_tools,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            return self._normalize_step_outcome(task, step, exc)
        if outcome.result is not None:
            observations_by_step[step.id] = outcome.result
        if outcome.kind == "failed" and not self._defer_recovery_to_reflection(outcome):
            outcome = await self._orchestrator().recovery_handler.recover_failed_step(
                task,
                plan,
                step,
                outcome.result,
                context,
                observation,
                threaded_tools=threaded_tools,
            )
        return outcome

    def _defer_recovery_to_reflection(self, outcome: StepExecutionOutcome) -> bool:
        result = outcome.result
        if result is None:
            return False
        output = result.output or {}
        return bool(output.get("replan_recommended") or output.get("resource_state_error"))

    async def _finish_from_plan(
        self,
        state: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        turn: int,
    ) -> EngineTurnResult:
        pending = self._mark_blocked_pending_steps(plan)

        if task.status in {TaskStatus.CANCELLED, TaskStatus.DENIED, TaskStatus.FAILED}:
            phase = self._phase_for_task_plan(task, plan)
            outcome = "cancelled" if phase == RunPhase.CANCELLED else "denied" if phase == RunPhase.DENIED else "failed"
            return await self._finish_turn(
                state,
                task,
                plan,
                outputs,
                hook,
                phase=phase,
                outcome=outcome,
                message=task.final_summary or f"Task {outcome}.",
                finished=True,
            )
        if any(step.status == StepStatus.WAITING_USER_APPROVAL for step in plan.steps):
            return await self._finish_turn(
                state,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.AWAITING_APPROVAL,
                outcome="waiting_approval",
                message="Plan generated and waiting for approval on modifying steps.",
                finished=True,
            )
        if any(step.status == StepStatus.DENIED for step in plan.steps):
            return await self._finish_turn(
                state,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.DENIED,
                outcome="denied",
                message="Task denied by safety review before tool execution.",
                finished=True,
            )
        if any(step.status == StepStatus.FAILED for step in plan.steps):
            return await self._finish_turn(
                state,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.FAILED,
                outcome="failed",
                message="Task failed while processing one or more steps.",
                finished=True,
            )
        if pending:
            next_state = self._state_from_task_plan(
                state,
                task,
                plan,
                phase=RunPhase.RUNNING,
                reason=f"turn {turn} completed; ready for next step",
                turn_count=turn,
            )
            db.upsert_model("plans", plan)
            stored = self.store.put(next_state)
            await self._emit(
                outputs,
                hook,
                "turn.completed",
                {"turn": turn, "task_id": task.id, "outcome": "continue", "pending_steps": sorted(pending)},
            )
            outputs["outcome"] = "continue"
            outputs["current_plan"] = stored.current_plan
            return EngineTurnResult(
                state=stored, finished=False, message="Continue to next OS execution turn.", outputs=outputs
            )

        return await self._finish_turn(
            state,
            task,
            plan,
            outputs,
            hook,
            phase=RunPhase.COMPLETED,
            outcome="completed",
            message="Task completed with read-only/open-only MVP tools.",
            finished=True,
        )

    async def _finish_turn(
        self,
        state: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        *,
        phase: RunPhase,
        outcome: str,
        message: str,
        finished: bool,
    ) -> EngineTurnResult:
        self._sync_task_status(task, phase, message)
        db.upsert_model("plans", plan)
        next_state = self._state_from_task_plan(
            state,
            task,
            plan,
            phase=phase,
            reason=message,
            turn_count=state.turn_count + 1,
        )
        stored = self.store.put(next_state)
        await self._emit(
            outputs,
            hook,
            "turn.completed",
            {
                "turn": stored.turn_count,
                "task_id": task.id,
                "plan_id": plan.id,
                "outcome": outcome,
                "phase": stored.phase.value,
            },
        )
        if outcome == "waiting_approval":
            await self._emit(
                outputs,
                hook,
                "approval.needed",
                {
                    "task_id": task.id,
                    "plan_id": plan.id,
                    "waiting_step_ids": [
                        step.id for step in plan.steps if step.status == StepStatus.WAITING_USER_APPROVAL
                    ],
                },
            )
        event_name = self._event_name_for_outcome(outcome)
        if event_name:
            await self._emit(
                outputs,
                hook,
                event_name,
                {
                    "task_id": task.id,
                    "plan_id": plan.id,
                    "phase": stored.phase.value,
                    "final_summary": task.final_summary,
                },
            )
        outputs["outcome"] = outcome
        outputs["phase"] = stored.phase.value
        outputs["current_plan"] = stored.current_plan
        orchestrator_name = self._orchestrator().name
        if finished and task.id:
            clear_task_read_states(task.id)
        if finished and stored.phase in TERMINAL_RUN_PHASES:
            self._release_run_runtime(stored.run_id)
        record("task.finished_or_waiting", orchestrator_name, {"status": task.status}, task_id=task.id)
        return EngineTurnResult(state=stored, finished=finished, message=message, outputs=outputs)

    def _register_run_task(self, run_id: str, work: asyncio.Task[Any]) -> None:
        bucket = self._run_tasks.setdefault(run_id, set())
        bucket.add(work)

        def _on_done(done: asyncio.Task[Any]) -> None:
            tasks = self._run_tasks.get(run_id)
            if tasks is None:
                return
            tasks.discard(done)
            if not tasks:
                self._run_tasks.pop(run_id, None)

        work.add_done_callback(_on_done)

    def _release_run_runtime(self, run_id: str) -> None:
        self._orchestrators_by_run.pop(run_id, None)
        self._run_tasks.pop(run_id, None)
        orchestrator_registry.release_run(run_id)

    def _mark_blocked_pending_steps(self, plan: Plan) -> set[str]:
        orchestrator = self._orchestrator()
        pending = self._pending_step_ids(plan)
        if pending:
            by_id, _dependents = orchestrator._build_step_graph(plan)
            orchestrator._mark_blocked_steps(pending, by_id)
            pending = self._pending_step_ids(plan)
        return pending

    def _sync_task_status(self, task: Task, phase: RunPhase, message: str) -> None:
        orchestrator = self._orchestrator()
        if phase == RunPhase.AWAITING_APPROVAL:
            orchestrator._set_status(task, TaskStatus.WAITING_USER_APPROVAL, final_summary=message)
        elif phase == RunPhase.PAUSED:
            orchestrator._set_status(task, TaskStatus.PAUSED, final_summary=message)
        elif phase == RunPhase.COMPLETED:
            orchestrator._set_status(task, TaskStatus.COMPLETED, final_summary=message)
        elif phase == RunPhase.FAILED:
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=message)
        elif phase == RunPhase.CANCELLED:
            orchestrator._set_status(task, TaskStatus.CANCELLED, final_summary=message)
        elif phase == RunPhase.DENIED:
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=message)

    def _stop_outcome(self, step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]]) -> str:
        return os_state.stop_outcome(step_outcomes)

    def _normalize_step_outcome(
        self,
        task: Task,
        step: PlanStep,
        raw_outcome: StepExecutionOutcome | BaseException,
    ) -> StepExecutionOutcome:
        if isinstance(raw_outcome, StepExecutionOutcome):
            return raw_outcome
        set_step_status(step, StepStatus.FAILED, actor="OSExecutionEngine")
        error = str(raw_outcome)
        self._orchestrator()._set_status(
            task, TaskStatus.FAILED, final_summary=self._orchestrator()._friendly_tool_error(error)
        )
        record(
            "task.step_failed_unhandled", self._orchestrator().name, {"step": step.id, "error": error}, task_id=task.id
        )
        return StepExecutionOutcome(
            "fatal_failed",
            ToolResult(
                tool_call_id=f"{step.id}_exception", ok=False, error=error, observation=f"{step.tool_name} failed."
            ),
        )

    async def _task_for_state(self, orchestrator: OrchestratorAgent, state: RunState) -> Task:
        task_id = state.task_id or str(state.current_plan.get("task_id") or "")
        if task_id:
            task_data = db.fetch_one("tasks", task_id)
            if task_data:
                return Task.model_validate(task_data)
        if state.goal:
            from app.agents.delegation_metadata import merge_run_task_metadata

            metadata = merge_run_task_metadata(goal=state.goal)
            return orchestrator.create_task_shell(state.goal, state.mode, metadata=metadata or None)
        raise KeyError(f"OS run has no task binding: {state.run_id}")

    async def _plan_for_state(self, orchestrator: OrchestratorAgent, task: Task, state: RunState) -> Plan:
        plan_id = str(state.current_plan.get("plan_id") or "")
        if plan_id:
            plan_data = db.fetch_one("plans", plan_id)
            if plan_data:
                return Plan.model_validate(plan_data)
        try:
            return orchestrator._latest_plan_for_task(task.id)
        except KeyError:
            return await self._create_reviewed_plan(orchestrator, task)

    async def _create_reviewed_plan(self, orchestrator: OrchestratorAgent, task: Task) -> Plan:
        if not orchestrator._supervise_new_agent_messages(task.id, "user_goal"):
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task during initial runtime supervision.",
            )
            return Plan(task_id=task.id, goal=task.user_goal, steps=[])

        goal_review = orchestrator.safety.review_goal(task.id, task.user_goal)
        if goal_review.verdict.value == "deny":
            denial_summary = (
                "; ".join(part for part in [*goal_review.reasons, goal_review.safe_alternative] if part)
                or "Forbidden intent detected."
            )
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=denial_summary)
            return Plan(task_id=task.id, goal=task.user_goal, steps=[])

        memory_context = await orchestrator._recall_memory(task.user_goal)
        goal_context = orchestrator.planning_handler._goal_context_for_planning(task, task.user_goal)
        session_context = orchestrator.planning_handler._session_context_for_planning(task)
        from app.agents.worker_agents import normalize_supervisor_agent_hint

        agent_hint = normalize_supervisor_agent_hint((task.metadata or {}).get("supervisor_agent_hint")) or None
        plan = await orchestrator.planning_handler._create_plan(
            task,
            task.user_goal,
            task.mode,
            memory_context,
            goal_context,
            session_context,
            agent_hint=agent_hint,
        )
        db.upsert_model("plans", plan)
        if not orchestrator._supervise_new_agent_messages(task.id, "planner_output"):
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after PlannerAgent output.",
            )
            return plan
        plan_review = orchestrator.consultation_handler.consult_and_review(task, plan)
        if plan_review.verdict.value == "deny":
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=plan_review.safe_alternative)
        return plan

    def _build_turn_context(self, state: RunState, task: Task, plan: Plan) -> dict[str, Any]:
        context = self._orchestrator()._tool_context()
        context.update(
            {
                "run_id": state.run_id,
                "turn_count": state.turn_count,
                "plan_id": plan.id,
                "task_id": task.id,
                "recent_failure_count": self._recent_failure_count(plan),
                "observations": [observation.model_dump(mode="json") for observation in state.observations[-20:]],
            }
        )
        return context

    def _pending_step_ids(self, plan: Plan) -> set[str]:
        return os_state.pending_step_ids(plan)

    def _parallel_batch_allowed(self, task: Task, ready: list[PlanStep]) -> bool:
        return self._orchestrator().step_scheduler_handler._parallel_batch_allowed(task, ready)

    def _dependency_observation(self, step: PlanStep, observations_by_step: dict[str, ToolResult]) -> ToolResult | None:
        return self._orchestrator()._dependency_observation(step, observations_by_step)

    def _observations_by_step(self, state: RunState) -> dict[str, ToolResult]:
        return os_state.observations_by_step(state, orchestrator_name=self._orchestrator().name)

    def _run_observation(self, turn: int, step: PlanStep, outcome: StepExecutionOutcome) -> RunObservation:
        return os_state.run_observation(turn, step, outcome)

    def _large_result_ref(self, result: ToolResult) -> LargeResultRef | None:
        return os_state.large_result_ref(result)

    def _initial_state_for_plan(self, task: Task, plan: Plan) -> RunState:
        state = RunState(
            run_id=f"os_{task.id}",
            engine="os",
            phase=RunPhase.RUNNING,
            goal=task.user_goal,
            mode=task.mode,
            task_id=task.id,
            transition_reason="os plan execution started",
            current_plan=self._plan_snapshot(task, plan),
        )
        return self.store.put(state)

    def _state_from_task_plan(
        self,
        state: RunState,
        task: Task,
        plan: Plan,
        *,
        phase: RunPhase,
        reason: str,
        turn_count: int | None = None,
    ) -> RunState:
        updated = os_state.state_from_task_plan(
            state,
            task,
            plan,
            phase=phase,
            reason=reason,
            turn_count=turn_count,
        )
        return self.store.trim_state_history(updated)

    def _plan_snapshot(self, task: Task, plan: Plan) -> dict[str, Any]:
        return os_state.plan_snapshot(task, plan)

    def _step_status_counts(self, plan: Plan) -> dict[str, int]:
        return os_state.step_status_counts(plan)

    def _recent_failure_count(self, plan: Plan) -> int:
        return os_state.recent_failure_count(plan)

    def _step_outcome_payload(self, step: PlanStep, outcome: StepExecutionOutcome) -> dict[str, Any]:
        return os_state.step_outcome_payload(step, outcome)

    def _phase_for_task(self, task: Task) -> RunPhase:
        return os_state.phase_for_task(task)

    def _phase_for_task_plan(self, task: Task, plan: Plan) -> RunPhase:
        return os_state.phase_for_task_plan(task, plan)

    def _event_name_for_outcome(self, outcome: str) -> str:
        return os_state.event_name_for_outcome(outcome)

    def _orchestrator(self) -> OrchestratorAgent:
        # Prefer the orchestrator bound to the currently executing run turn
        # (R4-H2): chain helpers then stay run-isolated even when two runs
        # share one engine instance, instead of trusting the mutable
        # self.orchestrator field that the last caller happened to set.
        bound = _CURRENT_RUN_ORCHESTRATOR.get()
        if bound is not None:
            return bound
        if self.orchestrator is None:
            self.orchestrator = self._new_orchestrator()
        return self.orchestrator

    def _orchestrator_for_state(self, state: RunState) -> OrchestratorAgent:
        orchestrator = self._orchestrators_by_run.get(state.run_id)
        if orchestrator is not None:
            self.orchestrator = orchestrator
            return orchestrator
        orchestrator = self._orchestrator()
        self._orchestrators_by_run[state.run_id] = orchestrator
        # Publish the bus into the registry so the run-service bridge and WS
        # subscribers resolve this orchestrator's bus instead of a fallback.
        task_id = state.task_id or str(state.current_plan.get("task_id") or "")
        if task_id:
            orchestrator_registry.bind(task_id=task_id, orchestrator=orchestrator, run_id=state.run_id)
        return orchestrator

    def _new_orchestrator(self) -> OrchestratorAgent:
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory()
        from app.agents.orchestrator_agent import OrchestratorAgent

        return OrchestratorAgent()

    async def _emit(
        self,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        event_payload = {"event": event_name, **payload}
        outputs.setdefault("events", []).append(event_payload)
        if hook is None:
            return
        maybe_awaitable = hook(event_name, payload)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
