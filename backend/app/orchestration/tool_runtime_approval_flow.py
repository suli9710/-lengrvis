from __future__ import annotations

from typing import Any

from app.core import db
from app.core.schemas import (
    MessageType,
    PlanStep,
    StepStatus,
    Task,
)
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_runtime_approval import (
    approval_boundary_facts,
    approval_dry_run_summary,
    dry_run_preview_contract_error,
    requires_runtime_approval,
    runtime_control_fields,
)
from app.orchestration.tool_runtime_approval import (
    auto_approved_args as build_auto_approved_args,
)
from app.orchestration.tool_runtime_approval_record import build_tool_approval_record
from app.orchestration.tool_runtime_dry_run_flow import (
    build_approval_dry_run_preview_result,
    deny_approval_without_dry_run,
    deny_dry_run_contract,
    deny_post_preview_review,
    fail_dry_run_preview,
)
from app.orchestration.tool_runtime_support import RuntimeExecutionResult
from app.policy.risk import SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.tools.schemas import ToolDefinition


class ToolRuntimeApprovalFlowMixin:
    async def _prepare_approval(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        confirmation_message: str,
        risk_binding: dict[str, str],
        *,
        threaded_tools: bool,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        preview_result = await build_approval_dry_run_preview_result(
            self,
            task,
            step,
            tool,
            runtime,
            threaded_tools=threaded_tools,
        )
        preview = preview_result.output
        if not preview_result.ok:
            return fail_dry_run_preview(orchestrator, task, step, preview_result)
        preview_contract_error = dry_run_preview_contract_error(preview)
        if preview_contract_error:
            return deny_dry_run_contract(
                orchestrator,
                task,
                step,
                tool,
                preview_result,
                preview_contract_error,
            )

        post_preview_review = orchestrator.safety.review_tool_result(
            task.id,
            step.id,
            step.tool_name,
            preview_result,
            tool.risk_level,
            tool_definition=tool,
        )
        if post_preview_review.verdict == SafetyVerdict.DENY:
            return deny_post_preview_review(orchestrator, task, step, preview_result, post_preview_review, runtime)

        approval = build_tool_approval_record(
            task=task,
            step=step,
            tool=tool,
            runtime=runtime,
            confirmation_message=confirmation_message,
            preview=preview,
            risk_binding=risk_binding,
        )
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)
        set_step_status(step, StepStatus.WAITING_USER_APPROVAL, actor="ToolRuntime")
        orchestrator.bus.publish_text(
            task.id,
            "HumanGateAgent",
            "Waiting for user approval before executing modifying operation.",
            message_type=MessageType.REVIEW,
            step_id=step.id,
        )
        orchestrator._supervise_new_agent_messages(task.id, "approval_gate")
        return RuntimeExecutionResult("waiting_user_approval", preview_result)

    def _deny_approval_without_dry_run(
        self, task: Task, step: PlanStep, tool: ToolDefinition
    ) -> RuntimeExecutionResult:
        return deny_approval_without_dry_run(self.orchestrator, task, step, tool)

    def _dry_run_preview_contract_error(self, preview: dict[str, Any]) -> str:
        return dry_run_preview_contract_error(preview)

    def _requires_runtime_approval(self, step: PlanStep, tool: ToolDefinition, runtime: TaskRuntimeContext) -> bool:
        return requires_runtime_approval(step, tool, runtime)

    def auto_approved_args(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        runtime: TaskRuntimeContext,
    ) -> dict[str, Any] | None:
        return build_auto_approved_args(tool, args, runtime)

    def _approval_dry_run_summary(self, tool: ToolDefinition, preview: dict[str, Any]) -> str:
        return approval_dry_run_summary(tool, preview)

    def _runtime_control_fields(self) -> dict[str, Any]:
        return runtime_control_fields()

    def _approval_boundary_facts(
        self,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        safe_preview: dict[str, Any],
    ) -> dict[str, Any]:
        return approval_boundary_facts(step, tool, runtime, safe_preview)
