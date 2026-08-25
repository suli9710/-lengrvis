from __future__ import annotations

from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, PlanStep, SafetyReview, StepStatus, Task
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_runtime_support import RuntimeExecutionResult, _safe_runtime_error_text
from app.policy.effective_risk_binding import (
    approval_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
    risk_revalidation_context,
)
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


class ToolRuntimeExecutionReviewMixin:
    def _fresh_execution_reviews(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        args: dict[str, Any],
    ) -> tuple[list[SafetyReview], RiskLevel]:
        review_context = risk_revalidation_context(runtime.tool_context(), task_id=task.id)
        review_context.pop("effective_risk_binding", None)
        review_context.update({"task_id": task.id, "step_id": step.id})
        reviews: list[SafetyReview] = []
        browser_review_agent = getattr(self.orchestrator, "browser_activity_review", None)
        if browser_review_agent is not None and str(step.tool_name or "").startswith("browser."):
            browser_review = browser_review_agent.review_tool_call(
                task.id,
                step.id,
                step.tool_name,
                args,
                tool.risk_level,
                context=review_context,
                tool_definition=tool,
            )
            if browser_review is not None:
                reviews.append(browser_review)
        safety = self.orchestrator.safety
        if not callable(getattr(safety, "review_tool_call", None)):
            safety = PolicyEngine(settings=runtime.settings)
        review = self._review_tool_call(
            safety,
            task.id,
            step.id,
            step.tool_name,
            args,
            tool.risk_level,
            context=review_context,
            tool_definition=tool,
        )
        reviews.insert(0, review)
        return reviews, review.declared_risk_level or tool.risk_level

    def _approved_execution_risk_binding(
        self,
        approval_id: str,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        reviews: list[SafetyReview],
        declared_risk: RiskLevel,
    ) -> tuple[dict[str, str], str]:
        data = db.fetch_one("approvals", approval_id)
        if not data:
            return {}, "Approved execution requires its stored approval record."
        approval = Approval.model_validate(data)
        if approval.status != ApprovalStatus.APPROVED or not approval.consumed_at:
            return {}, "Approved execution requires an approved, atomically consumed approval."
        if approval.task_id != task.id or (approval.step_id and approval.step_id != step.id):
            return {}, "Approved execution does not match the current task and step."
        if approval.tool_name != tool.name:
            return {}, "Approved execution is bound to a different tool."
        binding = approval_risk_binding(approval)
        binding_error = effective_risk_binding_error(
            binding,
            current_declared_risk=declared_risk,
            approval_risk_level=approval.risk_level,
        )
        if binding_error:
            return {}, binding_error
        for review in reviews:
            refreshed_error = refreshed_effective_risk_error(binding, review)
            if refreshed_error:
                return {}, refreshed_error
        return dict(binding), ""

    def _block_stale_execution_review(
        self,
        task: Task,
        step: PlanStep,
        runtime: TaskRuntimeContext,
        reason: str,
    ) -> RuntimeExecutionResult:
        runtime.abort_requested = True
        set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
        record(
            "tool.execution_review_blocked",
            "ToolRuntime",
            {"tool": step.tool_name, "reason": _safe_runtime_error_text(reason)},
            task_id=task.id,
        )
        return RuntimeExecutionResult("fatal_denied")
