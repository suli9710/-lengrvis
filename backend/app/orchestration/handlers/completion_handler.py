from __future__ import annotations

from typing import TYPE_CHECKING

from app.context.management import summarize_messages
from app.context.summary_provenance import build_summary_content_envelope
from app.core.audit import record
from app.core.schemas import Plan, Task, TaskStatus
from app.core.session_context import SessionSummaryConflictError
from app.llm.registry import get_effective_settings
from app.policy.risk import SafetyVerdict

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


class CompletionHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, _dispatcher: EventDispatcher) -> None:
        """Compatibility no-op; completion is invoked directly."""
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
        if not get_effective_settings().memory_auto_learning_enabled:
            record(
                "memory.auto_learning_skipped",
                orchestrator.name,
                {"task_id": task.id, "reason": "disabled_by_default"},
                task_id=task.id,
            )
            return
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
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: memory consolidation is best-effort.
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
            record(
                "memory.lessons_extracted", orchestrator.name, {"task_id": task.id, "count": learned}, task_id=task.id
            )

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
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: goal-stack completion should not fail task completion.
            record(
                "goal_stack.complete_failed",
                self.orchestrator.name,
                {"task_id": task.id, "error": str(exc)},
                task_id=task.id,
            )

    def _mark_session_task_complete(self, task: Task) -> None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return
        try:
            self._update_session_summary(task)
            store.complete_task(task.id)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: session completion should not fail task completion.
            record(
                "session_context.complete_failed",
                self.orchestrator.name,
                {"task_id": task.id, "error": str(exc)},
                task_id=task.id,
            )

    def _update_session_summary(self, task: Task) -> None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return
        messages = sorted(
            self.orchestrator.bus.get_messages(task.id),
            key=lambda message: (message.created_at, message.id),
        )
        if not messages:
            return
        settings = get_effective_settings()
        llm_messages = self.orchestrator.bus.get_llm_messages(task.id, settings, limit=80)
        summary = summarize_messages(llm_messages, settings)
        if not summary:
            return
        source_messages = messages[-80:]
        source_message_ids = [message.id for message in source_messages]
        for _attempt in range(3):
            context = store.load(store.current.id)
            merged_summary = _merge_session_summary(context.conversation_summary, summary)
            summary_envelope = build_summary_content_envelope(
                merged_summary,
                llm_messages,
                session_id=context.id,
                last_message_id=messages[-1].id,
                source_message_ids=source_message_ids,
                existing_summary=context.conversation_summary,
                existing_envelope=context.conversation_summary_envelope,
                existing_last_message_id=context.last_summarized_message_id,
                task_id=task.id,
                allow_message_id_count_mismatch=True,
            )
            try:
                store.remember_summary(
                    merged_summary,
                    last_message_id=messages[-1].id,
                    summary_envelope=summary_envelope,
                    expected_updated_at=context.updated_at,
                    token_stats={
                        "last_task_id": task.id,
                        "summarized_message_count": len(source_messages),
                        "summary_source_message_ids": source_message_ids,
                    },
                )
            except SessionSummaryConflictError:
                continue
            return
        raise SessionSummaryConflictError("session summary changed repeatedly while completing a task")


def _merge_session_summary(existing: str, new_summary: str) -> str:
    existing_text = str(existing or "").strip()
    new_text = str(new_summary or "").strip()
    if existing_text and new_text:
        return f"{existing_text}\n\n{new_text}"
    return existing_text or new_text
