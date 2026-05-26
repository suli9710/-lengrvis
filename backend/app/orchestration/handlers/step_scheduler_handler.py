from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.audit import record
from app.core.schemas import MessageType, Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.step_phase import set_step_status

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher
    from app.orchestration.events import StepReady


class StepSchedulerHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register("step.ready", self.handle_step_ready)

    def handle_step_ready(self, event: StepReady) -> None:  # pragma: no cover - registration hook
        return None

    async def process_steps(self, task: Task, plan: Plan) -> None:
        orchestrator = self.orchestrator
        try:
            by_id, _dependents = self._build_step_graph(plan)
        except ValueError as exc:
            for step in plan.steps:
                if step.status == StepStatus.PENDING:
                    set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=str(exc))
            record("task.step_graph_invalid", orchestrator.name, {"error": str(exc)}, task_id=task.id)
            return

        context = orchestrator._tool_context()
        pending = {
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
        running: dict[asyncio.Task, PlanStep] = {}
        observations: dict[str, ToolResult] = {}
        any_waiting = False
        revision_requested = False
        stop_requested = False

        while pending or running:
            if not stop_requested:
                ready = self._ready_steps(pending, by_id)
                threaded_tools = len(ready) > 1
                if len(ready) == 1 and not running:
                    step = ready[0]
                    pending.remove(step.id)
                    observation = self._dependency_observation(step, observations)
                    outcome = await orchestrator._execute_step(task, plan, step, context, observation, threaded_tools=False)
                    if outcome.result is not None:
                        observations[step.id] = outcome.result
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
                            observations[step.id] = outcome.result
                    if outcome.kind == "waiting_user_approval":
                        any_waiting = True
                        stop_requested = True
                    elif outcome.kind == "revision_requested":
                        revision_requested = True
                        stop_requested = True
                    elif outcome.kind in {"step_denied", "fatal_denied", "fatal_failed"}:
                        stop_requested = True
                    if stop_requested:
                        break
                    continue
                for step in ready:
                    pending.remove(step.id)
                    observation = self._dependency_observation(step, observations)
                    work = asyncio.create_task(
                        orchestrator._execute_step(task, plan, step, context, observation, threaded_tools=threaded_tools),
                        name=f"step-{step.id}",
                    )
                    running[work] = step

            if not running:
                self._mark_blocked_steps(pending, by_id)
                break

            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            outcomes = await asyncio.gather(*done, return_exceptions=True)
            for work, outcome in zip(done, outcomes):
                step = running.pop(work)
                if isinstance(outcome, Exception):
                    set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                    orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(str(outcome)))
                    record("task.step_failed_unhandled", orchestrator.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                    stop_requested = True
                    continue
                if outcome.result is not None:
                    observations[step.id] = outcome.result
                if outcome.kind == "failed":
                    dependency_observation = self._dependency_observation(step, observations)
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
                        observations[step.id] = outcome.result
                if outcome.kind == "waiting_user_approval":
                    any_waiting = True
                    stop_requested = True
                elif outcome.kind == "revision_requested":
                    revision_requested = True
                    stop_requested = True
                elif outcome.kind in {"step_denied", "fatal_denied", "fatal_failed"}:
                    stop_requested = True

            if stop_requested and running:
                remaining = list(running.keys())
                outcomes = await asyncio.gather(*remaining, return_exceptions=True)
                for work, outcome in zip(remaining, outcomes):
                    step = running.pop(work)
                    if isinstance(outcome, Exception):
                        set_step_status(step, StepStatus.FAILED, actor="StepSchedulerHandler")
                        record("task.step_failed_unhandled", orchestrator.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                        continue
                    if outcome.result is not None:
                        observations[step.id] = outcome.result
                    if outcome.kind == "waiting_user_approval":
                        any_waiting = True
                    elif outcome.kind == "revision_requested":
                        revision_requested = True
                break

            if not running and pending and not self._ready_steps(pending, by_id):
                self._mark_blocked_steps(pending, by_id)
                break

        if task.status in {TaskStatus.DENIED, TaskStatus.FAILED}:
            orchestrator._persist_plan_update(plan, "Plan stopped after task reached a terminal safety state.")
            record("task.finished_or_waiting", orchestrator.name, {"status": task.status}, task_id=task.id)
            return
        if revision_requested:
            target = TaskStatus.PAUSED
            summary = "A subagent requested plan revision; automatic replanning was not repeated for this step."
        elif any_waiting:
            target = TaskStatus.WAITING_USER_APPROVAL
            summary = "Plan generated and waiting for approval on modifying steps."
        elif any(step.status == StepStatus.DENIED for step in plan.steps):
            target = TaskStatus.DENIED
            summary = "Task denied by safety review before tool execution."
        elif any(step.status == StepStatus.FAILED for step in plan.steps):
            target = TaskStatus.FAILED
            summary = "Task failed while processing one or more steps."
        else:
            target = TaskStatus.COMPLETED
            summary = "Task completed with read-only/open-only MVP tools."
        orchestrator._set_status(task, target, final_summary=summary)
        orchestrator._persist_plan_update(plan, "Plan status updated after step scheduling.")
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
        return step.status in {
            StepStatus.SUCCEEDED,
            StepStatus.SKIPPED,
        }

    def _dependency_observation(self, step: PlanStep, observations: dict[str, ToolResult]) -> ToolResult | None:
        for dependency in reversed(step.depends_on):
            if dependency in observations:
                return observations[dependency]
        return None

    def _mark_blocked_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> None:
        orchestrator = self.orchestrator
        for step_id in list(pending):
            step = by_id[step_id]
            blocked = [
                dependency
                for dependency in step.depends_on
                if by_id[dependency].status in {StepStatus.FAILED, StepStatus.DENIED, StepStatus.WAITING_USER_APPROVAL}
            ]
            if blocked:
                set_step_status(step, StepStatus.SKIPPED, actor="StepSchedulerHandler")
                pending.remove(step_id)
                orchestrator.bus.publish_text(
                    step.task_id,
                    orchestrator.name,
                    f"Skipped step because dependency did not complete: {', '.join(blocked)}",
                    message_type=MessageType.OBSERVATION,
                    step_id=step.id,
                    structured_payload={"blocked_by": blocked},
                )
