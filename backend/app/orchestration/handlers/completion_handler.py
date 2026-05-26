from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.audit import record
from app.core.schemas import Plan, Task, TaskStatus
from app.context_management import summarize_messages
from app.llm.registry import get_effective_settings
from app.policy.risk import SafetyVerdict

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher
    from app.orchestration.events import AllStepsResolved, TaskFinalized


class CompletionHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register("all_steps.resolved", self.handle_all_steps_resolved)
        dispatcher.register("task.finalized", self.handle_task_finalized)

    def handle_all_steps_resolved(self, event: AllStepsResolved) -> None:  # pragma: no cover - registration hook
        return None

    def handle_task_finalized(self, event: TaskFinalized) -> None:  # pragma: no cover - registration hook
        return None

    async def finalize(self, task: Task, plan: Plan) -> None:
        orchestrator = self.orchestrator
        if task.status not in {TaskStatus.DENIED, TaskStatus.FAILED}:
            final_review = orchestrator.safety.final_review(plan, task.status, task.final_summary)
            if final_review.verdict == SafetyVerdict.DENY:
                orchestrator._set_status(task, TaskStatus.DENIED, final_summary=final_review.safe_alternative)
        if task.status in {TaskStatus.COMPLETED, TaskStatus.DENIED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            self._mark_session_task_complete(task)
        if task.status == TaskStatus.COMPLETED:
            self._mark_goal_complete(task)
            await self.consolidate_memory(task, plan)

    async def consolidate_memory(self, task: Task, plan: Plan) -> None:
        orchestrator = self.orchestrator
        summary = task.final_summary or f"Completed task: {task.user_goal}"
        try:
            await orchestrator.memory.remember(
                summary,
                task_id=task.id,
                kind="task_summary",
                tags=[step.agent_name for step in plan.steps if step.agent_name][:3],
                source=orchestrator.name,
            )
            await self.extract_lessons(task, plan)
        except Exception as exc:
            record("memory.consolidate_failed", orchestrator.name, {"task_id": task.id, "error": str(exc)})

    async def extract_lessons(self, task: Task, plan: Plan) -> None:
        orchestrator = self.orchestrator
        learned = 0
        for step in plan.steps:
            if step.status.value != "succeeded":
                continue
            lesson = {
                "goal_pattern": task.user_goal,
                "tool": step.tool_name,
                "args_pattern": self._args_pattern(step.args),
                "outcome": "succeeded",
                "reason": step.expected_observation or step.description,
            }
            await orchestrator.memory.remember_lesson(
                lesson,
                task_id=task.id,
                tags=[step.agent_name] if step.agent_name else [],
                source=orchestrator.name,
            )
            learned += 1
        if learned:
            record("memory.lessons_extracted", orchestrator.name, {"task_id": task.id, "count": learned}, task_id=task.id)

    def _args_pattern(self, args: dict) -> dict:
        pattern: dict = {}
        for key, value in (args or {}).items():
            if isinstance(value, str):
                pattern[key] = "<path>" if "\\" in value or "/" in value else value[:80]
            else:
                pattern[key] = type(value).__name__
        return pattern

    def _mark_goal_complete(self, task: Task) -> None:
        goal_stack = getattr(self.orchestrator, "goal_stack", None)
        if goal_stack is None:
            return
        try:
            active = goal_stack.peek()
            if active and task.id in active.related_task_ids:
                goal_stack.pop()
        except Exception as exc:
            record("goal_stack.complete_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)

    def _mark_session_task_complete(self, task: Task) -> None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return
        try:
            self._update_session_summary(task)
            store.complete_task(task.id)
        except Exception as exc:
            record("session_context.complete_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)

    def _update_session_summary(self, task: Task) -> None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return
        messages = self.orchestrator.bus.get_messages(task.id)
        if not messages:
            return
        settings = get_effective_settings()
        llm_messages = [message.to_openai_dict(include_legacy=False) for message in messages[-80:]]
        summary = summarize_messages(llm_messages, settings)
        if not summary:
            return
        store.remember_summary(
            summary,
            last_message_id=messages[-1].id,
            token_stats={"last_task_id": task.id, "summarized_message_count": len(messages[-80:])},
        )
