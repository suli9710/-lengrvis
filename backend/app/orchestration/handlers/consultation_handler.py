from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.schemas import Plan, Task, TaskStatus
from app.orchestration.events import ConsultationDone, PlanReviewed

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core.schemas import SafetyReview
    from app.orchestration.dispatcher import EventDispatcher


class ConsultationHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register("consultation.done", self.handle_consultation_done)
        dispatcher.register("plan.reviewed", self.handle_plan_reviewed)

    def handle_consultation_done(self, event: ConsultationDone) -> None:  # pragma: no cover - registration hook
        return None

    def handle_plan_reviewed(self, event: PlanReviewed) -> None:  # pragma: no cover - registration hook
        return None

    def consult_and_review(self, task: Task, plan: Plan) -> SafetyReview:
        orchestrator = self.orchestrator
        orchestrator._set_status(task, TaskStatus.AGENT_CONSULTATION)
        for agent in orchestrator.subagents.values():
            agent.consult(plan)
            if not orchestrator._supervise_new_agent_messages(task.id, f"{agent.name}_consultation"):
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary=f"SafetyReviewAgent stopped the task after {agent.name} consultation.",
                )
                break

        plan_review = orchestrator.safety.review_plan(plan)
        orchestrator._set_status(task, TaskStatus.REVIEWING_PLAN)
        return plan_review
