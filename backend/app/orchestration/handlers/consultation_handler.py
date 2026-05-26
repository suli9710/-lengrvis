from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.schemas import Plan, Task, TaskStatus
from app.orchestration.events import ConsultationDone, PlanReviewed
from app.policy.risk import RiskLevel, SafetyVerdict

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
        for agent_name, agent in orchestrator.subagents.items():
            before_ids = {message.id for message in orchestrator.bus.get_messages(task.id)}
            consult = getattr(agent, "consult", None)
            if callable(consult):
                consult(plan)
            stage = f"{agent_name}_consultation"
            if not orchestrator._supervise_new_agent_messages(task.id, stage):
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary=f"SafetyReviewAgent stopped the task after {agent_name} consultation.",
                )
                break
            self._record_empty_consultation_review(task, stage, agent_name, before_ids)

        plan_review = orchestrator.safety.review_plan(plan)
        orchestrator._set_status(task, TaskStatus.REVIEWING_PLAN)
        return plan_review

    def _record_empty_consultation_review(self, task: Task, stage: str, agent_name: str, before_ids: set[str]) -> None:
        orchestrator = self.orchestrator
        rows = orchestrator.bus.get_messages(task.id)
        if any(
            message.from_agent == orchestrator.safety.name
            and message.structured_payload
            and message.structured_payload.get("target_type") == f"agent_message:{stage}"
            for message in rows
        ):
            return
        if any(message.from_agent == agent_name and message.id not in before_ids for message in rows):
            return
        from app.core.schemas import SafetyReview

        review = SafetyReview(
            task_id=task.id,
            step_id=None,
            target_type=f"agent_message:{stage}",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=[f"{agent_name} consultation emitted no agent messages."],
        )
        orchestrator.safety._record_review(review, f"{stage}: no agent messages emitted")
