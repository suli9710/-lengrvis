from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.core import db
from app.core.schemas import MessageType, PlanStep, SafetyReview
from app.policy.risk import RiskLevel, SafetyVerdict


class ParallelReviewAgent(BaseAgent):
    name = "ParallelReviewAgent"
    domain_summary = "Reviews ready plan steps before the scheduler runs them concurrently."
    prompt_file = "safety_review_agent.md"

    def allowed_tools(self, registry=None) -> list[str]:  # noqa: ARG002
        return []

    def review_parallel_batch(self, task_id: str, steps: list[PlanStep], registry: Any) -> SafetyReview:
        reasons: list[str] = []
        max_level = RiskLevel.R0_READ_ONLY
        for step in steps:
            try:
                tool = registry.get(step.tool_name)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"Step {step.id} references unavailable tool {step.tool_name}: {exc}.")
                max_level = RiskLevel.R4_FORBIDDEN_OR_HANDOFF
                continue

            risk = getattr(tool, "risk_level", step.risk_level)
            if _risk_order(risk) > _risk_order(max_level):
                max_level = risk
            if not tool.is_concurrency_safe(step.args):
                reasons.append(f"Step {step.id} tool {tool.name} is not marked concurrency safe.")
            effects = {str(effect).casefold() for effect in getattr(tool, "effects", []) or []}
            if effects & {"write", "delete", "move", "send", "submit", "shell", "browser_write"}:
                reasons.append(f"Step {step.id} tool {tool.name} has write-like effects: {sorted(effects)}.")
            risk_value = getattr(risk, "value", str(risk))
            if risk_value.startswith(("R2", "R3", "R4")):
                reasons.append(f"Step {step.id} tool {tool.name} risk {risk_value} is not eligible for parallel execution.")

        if reasons:
            return self._record_review(
                SafetyReview(
                    task_id=task_id,
                    target_type="parallel_batch",
                    verdict=SafetyVerdict.REVISE_PLAN,
                    risk_level=max_level,
                    reasons=reasons,
                    safe_alternative="Run the ready steps serially or split write-like work behind explicit dependencies.",
                )
            )

        return self._record_review(
            SafetyReview(
                task_id=task_id,
                target_type="parallel_batch",
                verdict=SafetyVerdict.ALLOW,
                risk_level=RiskLevel.R0_READ_ONLY,
                reasons=[f"ParallelReviewAgent approved {len(steps)} concurrency-safe read-only step(s) for parallel execution."],
            )
        )

    def _record_review(self, review: SafetyReview) -> SafetyReview:
        db.upsert_model("safety_reviews", review)
        self.bus.publish_text(
            review.task_id,
            self.name,
            f"parallel batch supervision -> {review.verdict}",
            message_type=MessageType.REVIEW,
            step_id=review.step_id,
            structured_payload=review.model_dump(),
        )
        return review


def _risk_order(level: Any) -> int:
    value = getattr(level, "value", str(level))
    order = {
        RiskLevel.R0_READ_ONLY.value: 0,
        RiskLevel.R1_OPEN_ONLY.value: 1,
        RiskLevel.R2_REVERSIBLE_MODIFY.value: 2,
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value: 3,
        RiskLevel.R4_FORBIDDEN_OR_HANDOFF.value: 4,
    }
    return order.get(value, 4)
