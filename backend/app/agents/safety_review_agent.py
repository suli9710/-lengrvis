from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.base import BaseAgent
from app.config import AppSettings
from app.core import db
from app.core.schemas import AgentMessage, MessageType, Plan, SafetyReview, ToolResult
from app.llm.registry import get_effective_settings
from app.policy.policy_engine import FORBIDDEN_TERMS, PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict


@dataclass(slots=True)
class BatchMessageReview:
    aggregate: SafetyReview
    message_reviews: list[SafetyReview]
    supervised_message_ids: list[str]
    fast_path_count: int = 0
    slow_review_count: int = 0
    short_circuited: bool = False

    @property
    def verdict(self) -> SafetyVerdict:
        return self.aggregate.verdict

    @property
    def risk_level(self) -> RiskLevel:
        return self.aggregate.risk_level


class SafetyReviewAgent(BaseAgent):
    name = "SafetyReviewAgent"
    domain_summary = "Reviews goals, plans, tool calls, tool results, and agent messages for policy and risk violations."
    prompt_file = "safety_review_agent.md"

    def __init__(self, bus=None, settings: AppSettings | None = None) -> None:
        super().__init__(bus)
        self._last_review_message: AgentMessage | None = None
        effective = settings
        if effective is None:
            try:
                effective = get_effective_settings()
            except Exception:
                effective = None
        self.policy = PolicyEngine(effective)

    def _record_review(
        self,
        review: SafetyReview,
        content: str | None = None,
        message_type: MessageType = MessageType.REVIEW,
    ) -> SafetyReview:
        db.upsert_model("safety_reviews", review)
        self._last_review_message = self.bus.publish_text(
            review.task_id,
            self.name,
            content or "; ".join(review.reasons),
            message_type=message_type,
            step_id=review.step_id,
            structured_payload=review.model_dump(),
        )
        return review

    def review_goal(self, task_id: str, goal: str) -> SafetyReview:
        review = self.policy.review_goal_text(task_id, goal)
        return self._record_review(review)

    def review_plan(self, plan: Plan) -> SafetyReview:
        review = self.policy.review_plan(plan)
        return self._record_review(review)

    def review_tool_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
        context: dict[str, Any] | None = None,
        tool_definition: Any | None = None,
    ) -> SafetyReview:
        review = self.policy.review_tool_call(
            task_id,
            step_id,
            tool_name,
            args,
            risk_level,
            context=context,
            tool_definition=tool_definition,
        )
        return self._record_review(review, f"{tool_name}: {review.verdict} ({review.risk_level})")

    def review_browser_write(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict,
    ) -> SafetyReview | None:
        review = self.policy.review_browser_write_call(task_id, step_id, tool_name, args)
        if review is None:
            return None
        return self._record_review(review, f"{tool_name}: browser-write supervision -> {review.verdict}")

    def review_agent_message(self, message: AgentMessage, stage: str) -> SafetyReview:
        review = self.policy.review_agent_message(message, stage)
        return self._record_review(
            review,
            f"{stage}: {message.from_agent} message supervision -> {review.verdict}",
        )

    def review_agent_messages_batch(self, messages: list[AgentMessage], stage: str) -> BatchMessageReview:
        """Supervise many messages and return aggregate plus per-message verdicts."""
        if not messages:
            aggregate = SafetyReview(
                task_id="",
                step_id=None,
                target_type=f"agent_message:{stage}",
                verdict=SafetyVerdict.ALLOW,
                risk_level=RiskLevel.R0_READ_ONLY,
                reasons=["Batch supervision found no new agent messages."],
            )
            return BatchMessageReview(aggregate=aggregate, message_reviews=[], supervised_message_ids=[])

        scanned_ids: list[str] = []
        message_reviews: list[SafetyReview] = []
        fast_path_count = 0
        slow_review_count = 0
        last_task_id = ""
        last_step_id: str | None = None

        for message in messages:
            last_task_id = message.task_id or last_task_id
            last_step_id = message.step_id or last_step_id
            scanned_ids.append(message.id)
            review = self._fast_path_agent_message_review(message, stage)
            if review is None:
                slow_review_count += 1
                review = self.policy.review_agent_message(message, stage)
            else:
                fast_path_count += 1
            message_reviews.append(review)
            if review.verdict == SafetyVerdict.DENY:
                recorded = self._record_review(
                    review,
                    content=f"{stage}: batch denied at {message.from_agent} - {'; '.join(review.reasons)}",
                )
                self._tag_supervised_ids(recorded.task_id, scanned_ids, stage)
                return BatchMessageReview(
                    aggregate=recorded,
                    message_reviews=message_reviews,
                    supervised_message_ids=list(scanned_ids),
                    fast_path_count=fast_path_count,
                    slow_review_count=slow_review_count,
                    short_circuited=True,
                )

        count = len(messages)
        aggregate = SafetyReview(
            task_id=last_task_id,
            step_id=last_step_id,
            target_type=f"agent_message:{stage}",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=[
                f"Batch supervision passed {count} agent message(s).",
                f"Deterministic fast path cleared {fast_path_count} message(s); full policy reviewed {slow_review_count}.",
            ],
        )
        recorded = self._record_review(aggregate, f"{stage}: batch supervised {count} messages OK")
        self._tag_supervised_ids(recorded.task_id, scanned_ids, stage)
        return BatchMessageReview(
            aggregate=recorded,
            message_reviews=message_reviews,
            supervised_message_ids=list(scanned_ids),
            fast_path_count=fast_path_count,
            slow_review_count=slow_review_count,
        )

    def batch_review_messages(self, messages: list[AgentMessage], stage: str) -> SafetyReview:
        """Compatibility wrapper for existing callers that need only aggregate review."""
        return self.review_agent_messages_batch(messages, stage).aggregate

    def _fast_path_agent_message_review(self, message: AgentMessage, stage: str) -> SafetyReview | None:
        if message.message_type != MessageType.OBSERVATION:
            return None
        inspected_text = self._inspect_message_text(message)
        if self._contains_supervision_term(inspected_text):
            return None
        return SafetyReview(
            task_id=message.task_id,
            step_id=message.step_id,
            target_type=f"agent_message:{stage}",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Deterministic fast path cleared low-risk observation/tool-result message."],
        )

    def _inspect_message_text(self, message: AgentMessage) -> str:
        items: list[Any] = [message.content, message.structured_payload, message.metadata]
        return " ".join(str(item).lower() for item in items if item is not None)

    def _contains_supervision_term(self, text: str) -> bool:
        return any(term.lower() in text for term in FORBIDDEN_TERMS)

    def _tag_supervised_ids(self, task_id: str, ids: list[str], stage: str) -> None:
        """Pin supervised message ids on the latest safety bus message."""
        if not task_id or not ids:
            return
        latest = self._last_review_message if self._last_review_message and self._last_review_message.task_id == task_id else None
        if latest is None:
            for message in reversed(self.bus.get_messages(task_id)):
                if message.from_agent == self.name:
                    latest = message
                    break
        if latest is None:
            return
        latest.metadata["supervised_message_ids"] = list(ids)
        latest.metadata["supervision_stage"] = stage
        latest.metadata["supervised_message_id"] = ids[-1]
        db.upsert_model("agent_messages", latest)

    def review_tool_result(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        result: ToolResult,
        risk_level: RiskLevel,
    ) -> SafetyReview:
        review = self.policy.review_tool_result(task_id, step_id, tool_name, result, risk_level)
        return self._record_review(review, f"{tool_name}: post-tool supervision -> {review.verdict}")

    def final_review(self, plan: Plan, task_status: str, final_summary: str) -> SafetyReview:
        review = self.policy.final_review(plan, task_status, final_summary)
        return self._record_review(review, f"final runtime supervision -> {review.verdict}")

    def requires_immediate_stop(self, review: SafetyReview) -> bool:
        return review.verdict == SafetyVerdict.DENY

    def supervision_terms(self) -> set[str]:
        return set(FORBIDDEN_TERMS)
