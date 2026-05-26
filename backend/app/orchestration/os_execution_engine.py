from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.core import db
from app.core.audit import record
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import (
    EngineSelection,
    EngineTurnResult,
    LargeResultRef,
    RunObservation,
    RunPhase,
    RunState,
)
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.observations import summarize_result
from app.orchestration.step_phase import set_step_status
from app.policy.risk import RiskLevel

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent


OSEventHook = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_TERMINAL_STEP_STATUSES = {
    StepStatus.SUCCEEDED,
    StepStatus.SKIPPED,
    StepStatus.FAILED,
    StepStatus.DENIED,
    StepStatus.WAITING_USER_APPROVAL,
}


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
        self.store = store or default_run_store
        self.event_hook = event_hook
        self._orchestrators_by_run: dict[str, OrchestratorAgent] = {}

    async def start_run(self, goal: str, mode: str, engine: EngineSelection = "auto") -> RunState:  # noqa: ARG002
        orchestrator = self._new_orchestrator()
        task = orchestrator.create_task_shell(goal, mode)
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
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        if state.phase == RunPhase.PAUSED:
            resumed = state.model_copy(update={"phase": RunPhase.RUNNING, "transition_reason": "os run resumed"}, deep=True)
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
        if task_id:
            task_data = db.fetch_one("tasks", task_id)
            if task_data:
                orchestrator._set_status(Task.model_validate(task_data), TaskStatus.CANCELLED, final_summary="Run cancelled.")
        cancelled = state.model_copy(
            update={"phase": RunPhase.CANCELLED, "transition_reason": "os run cancelled"},
            deep=True,
        )
        return self.store.put(cancelled)

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in {RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED, RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.DENIED, RunPhase.CANCELLED}:
            return EngineTurnResult(state=state, finished=True, message=f"Run is already {state.phase.value}.")

        orchestrator = self._orchestrator_for_state(state)
        task = await self._task_for_state(orchestrator, state)
        plan = await self._plan_for_state(orchestrator, task, state)
        if task.status in {TaskStatus.CANCELLED, TaskStatus.DENIED, TaskStatus.FAILED}:
            updated = self._state_from_task_plan(state, task, plan, phase=self._phase_for_task_plan(task, plan), reason=task.final_summary)
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
        self._orchestrators_by_run[current.run_id] = self._orchestrator()
        turns_remaining = max_turns if max_turns is not None else max(1, (len(plan.steps) + 1) * 4 + 32)
        last_result: EngineTurnResult | None = None
        while turns_remaining > 0:
            turns_remaining -= 1
            last_result = await self.run_plan_turn(task, plan, state=current, event_hook=event_hook)
            current = last_result.state
            if last_result.finished:
                return last_result
            if not last_result.outputs.get("selected_step_ids"):
                return last_result

        message = "OS execution engine reached its per-plan turn limit."
        self._orchestrator()._set_status(task, TaskStatus.FAILED, final_summary=message)
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
        orchestrator = self._orchestrator()
        current = state or self._initial_state_for_plan(task, plan)
        turn = current.turn_count + 1
        outputs: dict[str, Any] = {"events": [], "turn": turn, "task_id": task.id, "plan_id": plan.id}
        hook = event_hook or self.event_hook
        context = self._build_turn_context(current, task, plan)
        observations_by_step = self._observations_by_step(current)

        await self._emit(outputs, hook, "turn.started", {"turn": turn, "task_id": task.id, "plan_id": plan.id})

        try:
            by_id, _dependents = orchestrator._build_step_graph(plan)
        except ValueError as exc:
            for step in plan.steps:
                if step.status == StepStatus.PENDING:
                    set_step_status(step, StepStatus.FAILED, actor="OSExecutionEngine")
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=str(exc))
            record("task.step_graph_invalid", orchestrator.name, {"error": str(exc)}, task_id=task.id)
            return await self._finish_turn(
                current,
                task,
                plan,
                outputs,
                hook,
                phase=RunPhase.FAILED,
                outcome="failed",
                message=str(exc),
                finished=True,
            )

        pending = self._pending_step_ids(plan)
        ready = orchestrator._ready_steps(pending, by_id)
        if not ready:
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

        step_outcomes = await self._execute_selected_steps(
            task,
            plan,
            selected,
            context,
            observations_by_step,
            threaded_tools=threaded_tools,
        )
        outputs["step_outcomes"] = [self._step_outcome_payload(step, outcome) for step, outcome in step_outcomes]

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

        current = current.model_copy(update={"observations": observations, "large_result_refs": large_refs}, deep=True)
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

        return await self._finish_from_plan(current, task, plan, outputs, hook, turn)

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
            outcome = await self._execute_one_step(task, plan, step, context, observations_by_step, threaded_tools=False)
            return [(step, outcome)]

        work: dict[asyncio.Task[StepExecutionOutcome], tuple[PlanStep, ToolResult | None]] = {}
        for step in selected:
            observation = self._dependency_observation(step, observations_by_step)
            task_work = asyncio.create_task(
                self._orchestrator()._execute_step(task, plan, step, context, observation, threaded_tools=True),
                name=f"os-step-{step.id}",
            )
            work[task_work] = (step, observation)

        raw_outcomes = await asyncio.gather(*work.keys(), return_exceptions=True)
        results: list[tuple[PlanStep, StepExecutionOutcome]] = []
        for task_work, raw_outcome in zip(work.keys(), raw_outcomes, strict=True):
            step, observation = work[task_work]
            outcome = self._normalize_step_outcome(task, step, raw_outcome)
            if outcome.result is not None:
                observations_by_step[step.id] = outcome.result
            if outcome.kind == "failed":
                outcome = await self._orchestrator().recovery_handler.recover_failed_step(
                    task,
                    plan,
                    step,
                    outcome.result,
                    context,
                    observation,
                    threaded_tools=True,
                )
            results.append((step, outcome))
        return results

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
        except Exception as exc:  # noqa: BLE001
            return self._normalize_step_outcome(task, step, exc)
        if outcome.result is not None:
            observations_by_step[step.id] = outcome.result
        if outcome.kind == "failed":
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

    async def _finish_from_plan(
        self,
        state: RunState,
        task: Task,
        plan: Plan,
        outputs: dict[str, Any],
        hook: OSEventHook | None,
        turn: int,
    ) -> EngineTurnResult:
        orchestrator = self._orchestrator()
        pending = self._pending_step_ids(plan)
        if pending:
            by_id, _dependents = orchestrator._build_step_graph(plan)
            if not orchestrator._ready_steps(pending, by_id):
                orchestrator._mark_blocked_steps(pending, by_id)
                pending = self._pending_step_ids(plan)

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
            return EngineTurnResult(state=stored, finished=False, message="Continue to next OS execution turn.", outputs=outputs)

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
        record("task.finished_or_waiting", self._orchestrator().name, {"status": task.status}, task_id=task.id)
        return EngineTurnResult(state=stored, finished=finished, message=message, outputs=outputs)

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
        kinds = {outcome.kind for _step, outcome in step_outcomes}
        if "waiting_user_approval" in kinds:
            return "waiting_approval"
        if "revision_requested" in kinds:
            return "paused"
        if kinds & {"step_denied", "fatal_denied"}:
            return "denied"
        if kinds & {"fatal_failed"}:
            return "failed"
        return "continue"

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
        self._orchestrator()._set_status(task, TaskStatus.FAILED, final_summary=self._orchestrator()._friendly_tool_error(error))
        record("task.step_failed_unhandled", self._orchestrator().name, {"step": step.id, "error": error}, task_id=task.id)
        return StepExecutionOutcome(
            "fatal_failed",
            ToolResult(tool_call_id=f"{step.id}_exception", ok=False, error=error, observation=f"{step.tool_name} failed."),
        )

    async def _task_for_state(self, orchestrator: OrchestratorAgent, state: RunState) -> Task:
        task_id = state.task_id or str(state.current_plan.get("task_id") or "")
        if task_id:
            task_data = db.fetch_one("tasks", task_id)
            if task_data:
                return Task.model_validate(task_data)
        if state.goal:
            return orchestrator.create_task_shell(state.goal, state.mode)
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
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=goal_review.safe_alternative)
            return Plan(task_id=task.id, goal=task.user_goal, steps=[])

        memory_context = await orchestrator._recall_memory(task.user_goal)
        goal_context = orchestrator.planning_handler._goal_context_for_planning(task, task.user_goal)
        session_context = orchestrator.planning_handler._session_context_for_planning(task)
        plan = await orchestrator.planning_handler._create_plan(
            task,
            task.user_goal,
            task.mode,
            memory_context,
            goal_context,
            session_context,
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
        return {step.id for step in plan.steps if step.status not in _TERMINAL_STEP_STATUSES}

    def _parallel_batch_allowed(self, task: Task, ready: list[PlanStep]) -> bool:
        return self._orchestrator().step_scheduler_handler._parallel_batch_allowed(task, ready)

    def _dependency_observation(self, step: PlanStep, observations_by_step: dict[str, ToolResult]) -> ToolResult | None:
        return self._orchestrator()._dependency_observation(step, observations_by_step)

    def _observations_by_step(self, state: RunState) -> dict[str, ToolResult]:
        observations: dict[str, ToolResult] = {}
        for observation in state.observations:
            step_id = str(observation.payload.get("step_id") or "")
            result_payload = observation.payload.get("tool_result")
            if not step_id or not isinstance(result_payload, dict):
                continue
            try:
                observations[step_id] = ToolResult.model_validate(result_payload)
            except Exception:  # noqa: BLE001
                continue
        return observations

    def _run_observation(self, turn: int, step: PlanStep, outcome: StepExecutionOutcome) -> RunObservation:
        result = outcome.result
        assert result is not None
        return RunObservation(
            turn=turn,
            source=step.agent_name or "ToolRuntime",
            message=summarize_result(result),
            payload={
                "step_id": step.id,
                "tool_name": step.tool_name,
                "outcome": outcome.kind,
                "tool_result": result.model_dump(mode="json"),
            },
        )

    def _large_result_ref(self, result: ToolResult) -> LargeResultRef | None:
        output = result.output or {}
        if not output.get("persisted_result"):
            return None
        return LargeResultRef(
            ref_id=result.id,
            path=str(output.get("path") or ""),
            original_size=int(output.get("original_size") or 0),
            preview=str(output.get("preview") or ""),
            has_more=bool(output.get("has_more")),
        )

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
        return state.model_copy(
            update={
                "phase": phase,
                "turn_count": state.turn_count if turn_count is None else turn_count,
                "transition_reason": reason,
                "current_plan": self._plan_snapshot(task, plan),
                "goal": task.user_goal or state.goal,
                "mode": task.mode or state.mode,
                "task_id": task.id,
                "paused": phase == RunPhase.PAUSED,
            },
            deep=True,
        )

    def _plan_snapshot(self, task: Task, plan: Plan) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "task_status": str(task.status.value if hasattr(task.status, "value") else task.status),
            "execution_stage": str(task.execution_stage.value if hasattr(task.execution_stage, "value") else task.execution_stage),
            "plan_id": plan.id,
            "goal": plan.goal,
            "step_status_counts": self._step_status_counts(plan),
            "steps": [step.model_dump(mode="json") for step in plan.steps],
        }

    def _step_status_counts(self, plan: Plan) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in plan.steps:
            key = step.status.value if hasattr(step.status, "value") else str(step.status)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _recent_failure_count(self, plan: Plan) -> int:
        return sum(1 for step in plan.steps if step.status == StepStatus.FAILED)

    def _step_outcome_payload(self, step: PlanStep, outcome: StepExecutionOutcome) -> dict[str, Any]:
        return {
            "step_id": step.id,
            "tool_name": step.tool_name,
            "kind": outcome.kind,
            "status": step.status.value if hasattr(step.status, "value") else str(step.status),
            "result_id": outcome.result.id if outcome.result is not None else "",
        }

    def _phase_for_task(self, task: Task) -> RunPhase:
        if task.execution_stage == ExecutionStage.AWAITING_APPROVAL:
            return RunPhase.AWAITING_APPROVAL
        if task.execution_stage == ExecutionStage.PAUSED:
            return RunPhase.PAUSED
        if task.status == TaskStatus.FAILED:
            return RunPhase.FAILED
        if task.status == TaskStatus.CANCELLED:
            return RunPhase.CANCELLED
        if task.status == TaskStatus.DENIED:
            return RunPhase.DENIED
        if task.status == TaskStatus.COMPLETED:
            return RunPhase.COMPLETED
        return RunPhase.RUNNING

    def _phase_for_task_plan(self, task: Task, plan: Plan) -> RunPhase:
        phase = self._phase_for_task(task)
        if phase != RunPhase.CANCELLED:
            return phase
        summary = (task.final_summary or "").casefold()
        if "cancel" in summary or "rejected" in summary:
            return RunPhase.CANCELLED
        if "deny" in summary or "denied" in summary or "forbidden" in summary or "safety" in summary:
            return RunPhase.DENIED
        if plan.global_risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            return RunPhase.DENIED
        if any(step.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF for step in plan.steps):
            return RunPhase.DENIED
        if any(step.status == StepStatus.DENIED for step in plan.steps):
            return RunPhase.DENIED
        return RunPhase.CANCELLED

    def _event_name_for_outcome(self, outcome: str) -> str:
        return {
            "cancelled": "run.cancelled",
            "waiting_approval": "run.waiting_approval",
            "completed": "run.completed",
            "failed": "run.failed",
            "denied": "run.denied",
        }.get(outcome, "")

    def _orchestrator(self) -> OrchestratorAgent:
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
