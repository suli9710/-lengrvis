from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.audit import record
from app.core import db
from app.core.schemas import Plan, Task, TaskStatus
from app.orchestration.events import GoalReviewed, PlanGenerated, TaskCreated
from app.perception.context_store import latest_perception_context
from app.policy.risk import SafetyVerdict

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


class PlanningHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register("task.created", self.handle_task_created)
        dispatcher.register("goal.reviewed", self.handle_goal_reviewed)
        dispatcher.register("plan.generated", self.handle_plan_generated)

    def handle_task_created(self, event: TaskCreated) -> None:  # pragma: no cover - registration hook
        return None

    def handle_goal_reviewed(self, event: GoalReviewed) -> None:  # pragma: no cover - registration hook
        return None

    def handle_plan_generated(self, event: PlanGenerated) -> None:  # pragma: no cover - registration hook
        return None

    async def run_task(self, task: Task) -> Task:
        orchestrator = self.orchestrator
        goal = task.user_goal
        mode = task.mode
        if not orchestrator._supervise_new_agent_messages(task.id, "user_goal"):
            return orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task during initial runtime supervision.",
            )

        goal_review = orchestrator.safety.review_goal(task.id, goal)
        if goal_review.verdict == SafetyVerdict.DENY:
            return orchestrator._set_status(task, TaskStatus.DENIED, final_summary=goal_review.safe_alternative)

        memory_context = await orchestrator._recall_memory(goal)
        goal_context = self._goal_context_for_planning(task, goal)
        session_context = self._session_context_for_planning(task)
        plan = await self._create_plan(task, goal, mode, memory_context, goal_context, session_context)
        db.upsert_model("plans", plan)
        if not orchestrator._supervise_new_agent_messages(task.id, "planner_output"):
            return orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after PlannerAgent output.",
            )

        plan_review = orchestrator.consultation_handler.consult_and_review(task, plan)
        if plan_review.verdict == SafetyVerdict.DENY:
            return orchestrator._set_status(task, TaskStatus.DENIED, final_summary=plan_review.safe_alternative)

        await orchestrator._process_steps(task, plan)
        await orchestrator.completion_handler.finalize(task, plan)
        return task

    async def _create_plan(
        self,
        task: Task,
        goal: str,
        mode: str,
        memory_context: list,
        goal_context: dict | None = None,
        session_context: dict | None = None,
    ) -> Plan:
        orchestrator = self.orchestrator
        list_tools = getattr(orchestrator.registry, "list_for_planning", orchestrator.registry.list)
        tools = [tool.name for tool in list_tools() if tool.name == "tool.search" or not getattr(tool, "defer_loading", False)]
        perception_context = latest_perception_context()
        try:
            return await orchestrator.planner.create_plan(
                task.id,
                goal,
                mode,
                tools,
                memory_context=memory_context,
                perception_context=perception_context,
                goal_context=goal_context,
                session_context=session_context,
            )
        except TypeError as exc:
            if (
                "perception_context" not in str(exc)
                and "goal_context" not in str(exc)
                and "session_context" not in str(exc)
            ):
                raise
            try:
                return await orchestrator.planner.create_plan(
                    task.id,
                    goal,
                    mode,
                    tools,
                    memory_context=memory_context,
                    perception_context=perception_context,
                    goal_context=goal_context,
                )
            except TypeError as inner_exc:
                if "perception_context" not in str(inner_exc) and "goal_context" not in str(inner_exc):
                    raise
                return await orchestrator.planner.create_plan(
                    task.id,
                    goal,
                    mode,
                    tools,
                    memory_context=memory_context,
                )

    def _goal_context_for_planning(self, task: Task, goal: str) -> dict | None:
        goal_stack = getattr(self.orchestrator, "goal_stack", None)
        if goal_stack is None:
            return None
        try:
            related_goal = goal_stack.find_related(goal)
            if related_goal is None:
                goal_stack.push(goal, task_id=task.id, parent_goal_id="")
            else:
                goal_stack.relate_task(task.id, related_goal.id)
            return goal_stack.get_context_for_planning(goal)
        except Exception as exc:
            record("goal_stack.context_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)
            return None

    def _session_context_for_planning(self, task: Task) -> dict | None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return None
        try:
            store.remember_task(task.id, workflow_state={"latest_goal": task.user_goal, "latest_task_id": task.id})
            return store.planning_context()
        except Exception as exc:
            record("session_context.planning_context_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)
            return None
