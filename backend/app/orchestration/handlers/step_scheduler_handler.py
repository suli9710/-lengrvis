from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.audit import record
from app.core.schemas import MessageType, Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.plan_snapshot import snapshot_step, write_back_step
from app.orchestration.resource_state import clear_task_read_states
from app.orchestration.step_phase import set_step_status
from app.policy.risk import SafetyVerdict

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher
    from app.orchestration.handlers.context import StepExecutionOutcome


@dataclass
class _RunningStep:
    """A parallel in-flight step: the real plan step plus its isolated snapshot."""

    step: PlanStep
    snapshot: PlanStep


@dataclass
class _ScheduleState:
    """Mutable bookkeeping shared by the scheduling loop helpers."""

    pending: set[str]
    by_id: dict[str, PlanStep]
    running: dict[asyncio.Task, _RunningStep] = field(default_factory=dict)
    observations: dict[str, ToolResult] = field(default_factory=dict)
    any_waiting: bool = False
    revision_requested: bool = False
    stop_requested: bool = False


class StepSchedulerHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, _dispatcher: EventDispatcher) -> None:
        """Compatibility no-op; scheduling runs through ``process_steps``."""
        return None

    async def process_steps(self, task: Task, plan: Plan) -> None:
        try:
            by_id, _dependents = self._build_step_graph(plan)
        except ValueError as exc:
            self._fail_plan_for_graph_error(task, plan, exc)
            return

        context = self.orchestrator._tool_context()
        state = _ScheduleState(pending=self._initial_pending_step_ids(plan), by_id=by_id)

        try:
            await self._run_schedule_loop(task, plan, context, state)
        except asyncio.CancelledError:
            await self._cancel_running_steps(task, state)
            raise

        self._finalize_plan_status(task, plan, state)

    def _fail_plan_for_graph_error(self, task: Task, plan: Plan, exc: ValueError) -> None:
        orchestrator = self.orchestrator
        for step in plan.steps:
            if step.status == StepStatus.PENDING:
                set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
        orchestrator._set_status(task, TaskStatus.FAILED, final_summary=str(exc))
        record("task.step_graph_invalid", orchestrator.name, {"error": str(exc)}, task_id=task.id)

    def _initial_pending_step_ids(self, plan: Plan) -> set[str]:
        return {
            step.id
            for step in plan.steps
            if step.status
            not in {
                StepStatus.SUCCEEDED,
                StepStatus.SKIPPED,
                StepStatus.FAILED,
                StepStatus.DENIED,
                StepStatus.WAITING_USER_APPROVAL,
            }
        }

    async def _run_schedule_loop(self, task: Task, plan: Plan, context: dict[str, Any], state: _ScheduleState) -> None:
        while state.pending or state.running:
            if not state.stop_requested:
                ready, threaded_tools = self._select_ready_batch(task, state)
                if len(ready) == 1 and not state.running:
                    await self._run_sequential_step(task, plan, ready[0], context, state)
                    if state.stop_requested:
                        break
                    continue
                self._launch_ready_steps(task, plan, ready, context, state, threaded_tools=threaded_tools)

            if not state.running:
                self._mark_blocked_steps(state.pending, state.by_id)
                break

            await self._collect_finished_steps(task, plan, context, state)

            if state.stop_requested and state.running:
                await self._drain_running_after_stop(task, state)
                break

            if not state.running and state.pending and not self._ready_steps(state.pending, state.by_id):
                self._mark_blocked_steps(state.pending, state.by_id)
                break

    def _select_ready_batch(self, task: Task, state: _ScheduleState) -> tuple[list[PlanStep], bool]:
        ready = self._ready_steps(state.pending, state.by_id)
        threaded_tools = self._parallel_batch_allowed(task, ready)
        if state.running and not threaded_tools:
            ready = []
        elif len(ready) > 1 and not threaded_tools:
            ready = ready[:1]
        return ready, threaded_tools

    async def _run_sequential_step(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        state: _ScheduleState,
    ) -> None:
        orchestrator = self.orchestrator
        state.pending.remove(step.id)
        observation = self._dependency_observation(step, state.observations)
        outcome = await orchestrator._execute_step(task, plan, step, context, observation, threaded_tools=False)
        if outcome.result is not None:
            state.observations[step.id] = outcome.result
        if outcome.kind == "failed":
            outcome = await orchestrator.recovery_handler.recover_failed_step(
                task,
                plan,
                step,
                outcome.result,
                context,
                observation,
                threaded_tools=False,
            )
            if outcome.result is not None:
                state.observations[step.id] = outcome.result
        self._apply_outcome_flags(outcome, state)

    def _launch_ready_steps(
        self,
        task: Task,
        plan: Plan,
        ready: list[PlanStep],
        context: dict[str, Any],
        state: _ScheduleState,
        *,
        threaded_tools: bool,
    ) -> None:
        orchestrator = self.orchestrator
        for step in ready:
            state.pending.remove(step.id)
            observation = self._dependency_observation(step, state.observations)
            step_context = copy.deepcopy(context)
            # Parallel executors mutate step fields across await points; hand each
            # one an isolated snapshot and write it back serially on completion
            # so siblings never observe (or persist) a half-updated step.
            isolated = snapshot_step(step)
            work = asyncio.create_task(
                orchestrator._execute_step(task, plan, isolated, step_context, observation, threaded_tools=threaded_tools),
                name=f"step-{step.id}",
            )
            state.running[work] = _RunningStep(step=step, snapshot=isolated)

    async def _collect_finished_steps(self, task: Task, plan: Plan, context: dict[str, Any], state: _ScheduleState) -> None:
        orchestrator = self.orchestrator
        done_set, _ = await asyncio.wait(state.running.keys(), return_when=asyncio.FIRST_COMPLETED)
        done = list(done_set)  # freeze iteration order before zip
        outcomes = await asyncio.gather(*done, return_exceptions=True)
        for work, outcome in zip(done, outcomes):
            running = state.running.pop(work)
            step = running.step
            # BaseException also covers asyncio.CancelledError, which
            # gather(return_exceptions=True) returns but Exception misses.
            if isinstance(outcome, BaseException):
                set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(str(outcome)))
                record("task.step_failed_unhandled", orchestrator.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                state.stop_requested = True
                continue
            write_back_step(step, running.snapshot)
            if outcome.result is not None:
                state.observations[step.id] = outcome.result
            if outcome.kind == "failed":
                dependency_observation = self._dependency_observation(step, state.observations)
                outcome = await orchestrator.recovery_handler.recover_failed_step(
                    task,
                    plan,
                    step,
                    outcome.result,
                    context,
                    dependency_observation,
                    threaded_tools=True,
                )
                if outcome.result is not None:
                    state.observations[step.id] = outcome.result
            self._apply_outcome_flags(outcome, state)
            if state.stop_requested:
                break
        if state.stop_requested and state.running:
            await self._drain_running_after_stop(task, state)

    async def _drain_running_after_stop(self, task: Task, state: _ScheduleState) -> None:
        orchestrator = self.orchestrator
        for work in list(state.running.keys()):
            work.cancel()
        remaining = list(state.running.keys())
        outcomes = await asyncio.gather(*remaining, return_exceptions=True)
        for work, outcome in zip(remaining, outcomes):
            running = state.running.pop(work)
            step = running.step
            if isinstance(outcome, BaseException):
                set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                record("task.step_failed_unhandled", orchestrator.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                continue
            write_back_step(step, running.snapshot)
            if outcome.result is not None:
                state.observations[step.id] = outcome.result
            if outcome.kind == "waiting_user_approval":
                state.any_waiting = True
            elif outcome.kind == "revision_requested":
                state.revision_requested = True

    def _apply_outcome_flags(self, outcome: StepExecutionOutcome, state: _ScheduleState) -> None:
        if outcome.kind == "waiting_user_approval":
            state.any_waiting = True
            state.stop_requested = True
        elif outcome.kind == "revision_requested":
            state.revision_requested = True
            state.stop_requested = True
        elif outcome.kind in {"step_denied", "fatal_denied", "fatal_failed"}:
            state.stop_requested = True

    async def _cancel_running_steps(self, task: Task, state: _ScheduleState) -> None:
        # When the scheduler itself is cancelled (run cancellation), the
        # in-flight step tasks must be cancelled too or they keep running
        # tools detached from any supervision. Siblings that already finished
        # must be written back first (same contract as _drain_running_after_stop).
        orchestrator = self.orchestrator
        if not state.running:
            return
        remaining = list(state.running.keys())
        for work in remaining:
            work.cancel()
        outcomes = await asyncio.gather(*remaining, return_exceptions=True)
        for work, outcome in zip(remaining, outcomes):
            running = state.running.pop(work)
            step = running.step
            if isinstance(outcome, asyncio.CancelledError):
                set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                record("task.step_cancelled", orchestrator.name, {"step": step.id}, task_id=task.id)
                continue
            if isinstance(outcome, BaseException):
                set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                record(
                    "task.step_failed_unhandled",
                    orchestrator.name,
                    {"step": step.id, "error": str(outcome)},
                    task_id=task.id,
                )
                continue
            write_back_step(step, running.snapshot)
            record("task.step_cancelled_writeback", orchestrator.name, {"step": step.id}, task_id=task.id)

    def _finalize_plan_status(self, task: Task, plan: Plan, state: _ScheduleState) -> None:
        orchestrator = self.orchestrator
        if state.pending and any(step.status in {StepStatus.FAILED, StepStatus.DENIED} for step in plan.steps):
            self._mark_blocked_steps(state.pending, state.by_id)

        if task.status in {TaskStatus.DENIED, TaskStatus.FAILED}:
            orchestrator._persist_plan_update(plan, "Plan stopped after task reached a terminal safety state.")
            clear_task_read_states(task.id)
            record("task.finished_or_waiting", orchestrator.name, {"status": task.status}, task_id=task.id)
            return
        if state.revision_requested:
            target = TaskStatus.PAUSED
            summary = "A subagent requested plan revision; automatic replanning was not repeated for this step."
        elif state.any_waiting:
            target = TaskStatus.WAITING_USER_APPROVAL
            summary = "Plan generated and waiting for approval on modifying steps."
        elif any(step.status == StepStatus.DENIED for step in plan.steps):
            target = TaskStatus.DENIED
            summary = "Task denied by safety review before tool execution."
        elif any(step.status == StepStatus.FAILED for step in plan.steps):
            target = TaskStatus.FAILED
            summary = "Task failed while processing one or more steps."
        elif self._all_steps_skipped_with_blocked_dependencies(plan):
            target = TaskStatus.FAILED
            summary = "Task could not complete because plan steps were blocked by dependencies."
        elif self._has_success_with_blocked_skips(plan):
            target = TaskStatus.FAILED
            summary = "Task could not complete because some plan steps remained blocked by dependencies."
        else:
            target = TaskStatus.COMPLETED
            summary = "Task completed with read-only/open-only MVP tools."
        orchestrator._set_status(task, target, final_summary=summary)
        orchestrator._persist_plan_update(plan, "Plan status updated after step scheduling.")
        clear_task_read_states(task.id)
        record("task.finished_or_waiting", orchestrator.name, {"status": task.status}, task_id=task.id)

    def _build_step_graph(self, plan: Plan) -> tuple[dict[str, PlanStep], dict[str, set[str]]]:
        by_id: dict[str, PlanStep] = {}
        dependents: dict[str, set[str]] = {}
        for idx, step in enumerate(plan.steps, start=1):
            if not step.id:
                step.id = f"step_{idx}"
            if step.id in by_id:
                raise ValueError(f"Duplicate plan step id: {step.id}")
            by_id[step.id] = step
            dependents.setdefault(step.id, set())

        for step in plan.steps:
            normalized: list[str] = []
            for dependency in step.depends_on:
                dependency_id = str(dependency).strip()
                if not dependency_id:
                    continue
                if dependency_id == step.id:
                    raise ValueError(f"Plan step {step.id} cannot depend on itself.")
                if dependency_id not in by_id:
                    raise ValueError(f"Plan step {step.id} depends on unknown step id: {dependency_id}")
                if dependency_id not in normalized:
                    normalized.append(dependency_id)
                dependents.setdefault(dependency_id, set()).add(step.id)
            step.depends_on = normalized

        if self._has_step_cycle(by_id):
            raise ValueError("Plan step dependency graph contains a cycle.")
        return by_id, dependents

    def _has_step_cycle(self, by_id: dict[str, PlanStep]) -> bool:
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in permanent:
                return False
            if step_id in temporary:
                return True
            temporary.add(step_id)
            for dependency in by_id[step_id].depends_on:
                if visit(dependency):
                    return True
            temporary.remove(step_id)
            permanent.add(step_id)
            return False

        return any(visit(step_id) for step_id in by_id)

    def _ready_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> list[PlanStep]:
        ready = [
            by_id[step_id]
            for step_id in pending
            if all(self._dependency_finished(by_id[dependency]) for dependency in by_id[step_id].depends_on)
        ]
        return sorted(ready, key=lambda step: (step.order, step.id))

    def _dependency_finished(self, step: PlanStep) -> bool:
        if step.status == StepStatus.SUCCEEDED:
            return True
        if step.status == StepStatus.SKIPPED:
            return not self._dependency_blocked_skip(step)
        return False

    def _dependency_observation(self, step: PlanStep, observations: dict[str, ToolResult]) -> ToolResult | None:
        for dependency in reversed(step.depends_on):
            if dependency in observations:
                return observations[dependency]
        return None

    def _mark_blocked_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> None:
        orchestrator = self.orchestrator
        while True:
            marked_any = False
            for step_id in list(pending):
                step = by_id[step_id]
                blocked = [
                    dependency
                    for dependency in step.depends_on
                    if by_id[dependency].status in {StepStatus.FAILED, StepStatus.DENIED}
                    or self._dependency_blocked_skip(by_id[dependency])
                ]
                if blocked:
                    self._mark_step_blocked_by_dependencies(step, blocked)
                    pending.remove(step_id)
                    marked_any = True
                    orchestrator.bus.publish_text(
                        step.task_id,
                        orchestrator.name,
                        f"Skipped step because dependency did not complete: {', '.join(blocked)}",
                        message_type=MessageType.OBSERVATION,
                        step_id=step.id,
                        structured_payload={"blocked_by": blocked, "skip_reason": "blocked_dependency"},
                    )
            if not marked_any:
                break

    def _mark_step_blocked_by_dependencies(self, step: PlanStep, blocked: list[str]) -> None:
        model_action = dict(step.model_action or {})
        scheduler = dict(model_action.get("scheduler") or {})
        scheduler["skip_reason"] = "blocked_dependency"
        scheduler["blocked_by"] = list(blocked)
        model_action["scheduler"] = scheduler
        step.model_action = model_action
        set_step_status(step, StepStatus.SKIPPED, actor="StepSchedulerHandler")

    def revive_dependency_blocked_skips(self, plan: Plan) -> None:
        """Return dependency-blocked SKIPPED steps to PENDING once blockers succeed."""
        by_id = {step.id: step for step in plan.steps}
        for step in plan.steps:
            if step.status != StepStatus.SKIPPED or not self._dependency_blocked_skip(step):
                continue
            model_action = dict(step.model_action or {})
            scheduler = dict(model_action.get("scheduler") or {})
            blocked = [str(item) for item in (scheduler.get("blocked_by") or []) if str(item) in by_id]
            if not blocked:
                continue
            if not all(by_id[dependency].status == StepStatus.SUCCEEDED for dependency in blocked):
                continue
            scheduler.pop("skip_reason", None)
            scheduler.pop("blocked_by", None)
            if scheduler:
                model_action["scheduler"] = scheduler
            else:
                model_action.pop("scheduler", None)
            step.model_action = model_action or None
            set_step_status(step, StepStatus.PENDING, actor="StepSchedulerHandler")

    def _dependency_blocked_skip(self, step: PlanStep) -> bool:
        if step.status != StepStatus.SKIPPED:
            return False
        model_action = step.model_action if isinstance(step.model_action, dict) else {}
        scheduler = model_action.get("scheduler") if isinstance(model_action.get("scheduler"), dict) else {}
        return scheduler.get("skip_reason") == "blocked_dependency"

    def _all_steps_skipped_with_blocked_dependencies(self, plan: Plan) -> bool:
        if not plan.steps:
            return False
        if not all(step.status == StepStatus.SKIPPED for step in plan.steps):
            return False
        return any(self._dependency_blocked_skip(step) for step in plan.steps)

    def _has_success_with_blocked_skips(self, plan: Plan) -> bool:
        if not any(step.status == StepStatus.SUCCEEDED for step in plan.steps):
            return False
        return any(self._dependency_blocked_skip(step) for step in plan.steps)

    def _parallel_batch_allowed(self, task: Task, ready: list[PlanStep]) -> bool:
        if len(ready) <= 1:
            return False
        reviewer = getattr(self.orchestrator, "parallel_review", self.orchestrator.safety)
        review = reviewer.review_parallel_batch(task.id, ready, self.orchestrator.registry)
        if review.verdict == SafetyVerdict.ALLOW:
            record(
                "task.parallel_batch_allowed",
                "ParallelReviewAgent",
                {"step_ids": [step.id for step in ready], "count": len(ready)},
                task_id=task.id,
            )
            return True
        record(
            "task.parallel_batch_serialized",
            "ParallelReviewAgent",
            {"step_ids": [step.id for step in ready], "reasons": review.reasons},
            task_id=task.id,
        )
        return False
