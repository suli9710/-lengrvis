from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.content_provenance import (
    ContentRevalidationRequired,
    assert_content_revalidated,
    collect_content_envelopes,
    content_binding_payload,
    content_envelope_for_tool_output,
    propagate_content_envelope,
)
from app.core.schemas import (
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolResult,
)
from app.observability.tracing import span
from app.orchestration.automation_runtime_guard import (
    AutomationExecutionDenied,
    authorize_automation_execution,
)
from app.orchestration.result_budget import apply_result_budget, reviewed_large_result_artifact_valid
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_execution_journal import (
    durable_post_tool_denial_reason,
    is_durable_post_tool_denial,
    load_tool_call,
    load_tool_result,
    mark_tool_call_committed,
    mark_tool_call_executing,
    mark_tool_call_outcome_unknown,
    quarantine_result_for_reuse,
    runtime_review_allows_result_reuse,
)
from app.orchestration.tool_runtime_approval_flow import ToolRuntimeApprovalFlowMixin
from app.orchestration.tool_runtime_execution import ToolRuntimeExecutionMixin
from app.orchestration.tool_runtime_execution_review import ToolRuntimeExecutionReviewMixin
from app.orchestration.tool_runtime_lifecycle import ToolRuntimeLifecycleMixin
from app.orchestration.tool_runtime_paths import (
    authorized_path_error,
    ensure_authorized_paths,
)
from app.orchestration.tool_runtime_review import ToolRuntimeReviewMixin
from app.orchestration.tool_runtime_support import (
    RuntimeExecutionResult,
    _discard_persisted_result,
    _pending_review_result_stub,
    _persistable_tool_result,
    _safe_runtime_error_text,
    _withheld_tool_result,
)
from app.orchestration.tool_runtime_support import (
    _actionable_error_text as _actionable_error_text,
)
from app.orchestration.tool_runtime_support import (
    _exception_error_text as _exception_error_text,
)
from app.policy.effective_risk_binding import build_effective_risk_binding
from app.policy.model_boundary import model_control_arg_error
from app.policy.risk import SafetyVerdict
from app.tools.schemas import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRuntime(
    ToolRuntimeApprovalFlowMixin,
    ToolRuntimeExecutionMixin,
    ToolRuntimeExecutionReviewMixin,
    ToolRuntimeLifecycleMixin,
    ToolRuntimeReviewMixin,
):
    """Owns the tool lifecycle: validation, permissions, execution, and result budgeting."""

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    async def review_and_maybe_prepare_approval(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        *,
        threaded_tools: bool = False,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        orchestrator._set_status(task, TaskStatus.REVIEWING_TOOL_CALL)
        boundary_error = model_control_arg_error(step.args)
        if boundary_error:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            record(
                "model_boundary.tool_args_denied",
                "ToolRuntime",
                {"tool": step.tool_name, "error": boundary_error},
                task_id=task.id,
            )
            orchestrator.bus.publish_text(task.id, orchestrator.name, f"Denied step: {boundary_error}", step_id=step.id)
            orchestrator._supervise_new_agent_messages(task.id, "model_boundary_denied")
            result = ToolResult(
                tool_call_id=f"{step.id}_model_boundary",
                ok=False,
                error=boundary_error,
                observation=f"{step.tool_name} was denied by model boundary constraints.",
            )
            return RuntimeExecutionResult("step_denied", result)
        validation_error = self._validate_input(tool, step.args, runtime)
        if validation_error:
            set_step_status(step, StepStatus.FAILED, actor="ToolRuntime")
            result = ToolResult(
                tool_call_id=f"{step.id}_validation",
                ok=False,
                error=validation_error,
                observation=f"{step.tool_name} input validation failed.",
            )
            return RuntimeExecutionResult("fatal_failed", result)

        permission_error = self._check_permission(tool, step.args, runtime)
        if permission_error:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator.bus.publish_text(
                task.id,
                orchestrator.name,
                f"Denied step: {permission_error}",
                step_id=step.id,
            )
            orchestrator._supervise_new_agent_messages(task.id, "tool_permission_denied")
            return RuntimeExecutionResult("step_denied")

        review_context = runtime.tool_context()
        review_context.update({"task_id": task.id, "step_id": step.id})
        browser_review = None
        browser_review_agent = getattr(orchestrator, "browser_activity_review", None)
        if browser_review_agent is not None and str(step.tool_name or "").startswith("browser."):
            browser_review = browser_review_agent.review_tool_call(
                task.id,
                step.id,
                step.tool_name,
                step.args,
                tool.risk_level,
                context=review_context,
                tool_definition=tool,
            )
            if browser_review is not None and browser_review.verdict == SafetyVerdict.DENY:
                set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
                orchestrator.bus.publish_text(
                    task.id,
                    orchestrator.name,
                    f"Denied browser activity {step.tool_name}: {'; '.join(browser_review.reasons)}",
                    step_id=step.id,
                )
                orchestrator._supervise_new_agent_messages(task.id, "browser_activity_denied")
                return RuntimeExecutionResult("step_denied")

        review = self._review_tool_call(
            orchestrator.safety,
            task.id,
            step.id,
            step.tool_name,
            step.args,
            tool.risk_level,
            context=review_context,
            tool_definition=tool,
        )
        if review.verdict == SafetyVerdict.DENY:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator.bus.publish_text(
                task.id, orchestrator.name, f"Denied step: {step.description}", step_id=step.id
            )
            orchestrator._supervise_new_agent_messages(task.id, "tool_call_denied")
            return RuntimeExecutionResult("step_denied")

        risk_reviews = [review]
        if browser_review is not None:
            risk_reviews.append(browser_review)
        declared_risk = review.declared_risk_level or tool.risk_level
        risk_binding = build_effective_risk_binding(declared_risk, risk_reviews)
        runtime.extra_context["effective_risk_binding"] = risk_binding

        # Single-source user-policy backstop (P0-18 convergence): the safety
        # review above already evaluates the PermissionStore and records the
        # deny; this re-check only fires if the review rail ever drifts and
        # stops consulting user rules, so the execution boundary itself can
        # never run a user-denied tool.
        backstop_error = self._user_permission_error(tool, step.args, runtime, getattr(orchestrator, "safety", None))
        if backstop_error:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator.bus.publish_text(task.id, orchestrator.name, f"Denied step: {backstop_error}", step_id=step.id)
            orchestrator._supervise_new_agent_messages(task.id, "tool_permission_denied")
            return RuntimeExecutionResult("step_denied")

        approval_review = (
            browser_review
            if browser_review is not None and browser_review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
            else None
        )
        if approval_review is None and review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            approval_review = review
        requires_runtime_approval = self._requires_runtime_approval(step, tool, runtime)
        if approval_review is not None or requires_runtime_approval:
            if not tool.supports_dry_run:
                return self._deny_approval_without_dry_run(task, step, tool)
            confirmation_message = (
                approval_review.user_confirmation_message
                if approval_review is not None and approval_review.user_confirmation_message
                else step.description
            )
            return await self._prepare_approval(
                task,
                step,
                tool,
                runtime,
                confirmation_message,
                risk_binding,
                threaded_tools=threaded_tools,
            )
        return RuntimeExecutionResult("allowed")

    async def execute_allowed(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        *,
        threaded_tools: bool = False,
        approved_args: dict[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> RuntimeExecutionResult:
        """Execute a tool while emitting a safe lifecycle span.

        The span intentionally records only stable identifiers and result
        status.  Prompt text, tool arguments, and tool output bodies remain
        outside the observability payload even when a caller passes them in.
        """

        args = approved_args if approved_args is not None else step.args
        reviews, declared_risk = self._fresh_execution_reviews(task, step, tool, runtime, args)
        # The execution boundary owns this value. A caller-supplied or stale
        # binding must never authorize a live tool call.
        runtime.extra_context.pop("effective_risk_binding", None)
        if approval_id:
            risk_binding, binding_error = self._approved_execution_risk_binding(
                approval_id,
                task,
                step,
                tool,
                reviews,
                declared_risk,
            )
            if binding_error:
                return self._block_stale_execution_review(task, step, runtime, binding_error)
            runtime.extra_context["effective_risk_binding"] = risk_binding
        else:
            blocked_review = next((review for review in reviews if review.verdict != SafetyVerdict.ALLOW), None)
            if blocked_review is not None or self._requires_runtime_approval(step, tool, runtime):
                reason = (
                    "; ".join(blocked_review.reasons)
                    if blocked_review is not None
                    else "Tool execution requires a fresh preview and explicit user approval."
                )
                return self._block_stale_execution_review(task, step, runtime, reason)
            runtime.extra_context["effective_risk_binding"] = build_effective_risk_binding(
                declared_risk,
                reviews,
            )

        attributes: dict[str, object] = {
            "task.id": task.id,
            "step.id": step.id,
            "tool.name": tool.name,
            "tool.approval": bool(approval_id),
        }
        with span("tool.execute", attributes) as tool_span:
            result = await self._execute_allowed_impl(
                task,
                step,
                tool,
                runtime,
                threaded_tools=threaded_tools,
                approved_args=approved_args,
                approval_id=approval_id,
            )
            tool_span.set_attribute("tool.result_kind", result.kind)
            if result.result is not None:
                tool_span.set_attribute("tool.call_id", result.result.tool_call_id)
                tool_span.set_attribute("tool.ok", result.result.ok)
                unknown = bool((result.result.output or {}).get("outcome_unknown"))
                if unknown:
                    tool_span.mark_outcome_unknown()
                    tool_span.set_status("error")
            return result

    async def _execute_allowed_impl(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        *,
        threaded_tools: bool = False,
        approved_args: dict[str, Any] | None = None,
        approval_id: str | None = None,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        args = approved_args if approved_args is not None else step.args
        if _tool_requires_content_revalidation(tool, args):
            try:
                envelopes = collect_content_envelopes(args, runtime.extra_context, step.model_action)
                if envelopes:
                    assert_content_revalidated(
                        envelopes,
                        task_scopes={task.id, str(runtime.extra_context.get("automation_run_id") or "")},
                        boundary=f"{tool.name} execution",
                        content=content_binding_payload(args),
                    )
            except ContentRevalidationRequired as exc:
                runtime.abort_requested = True
                set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary=f"Tool execution was denied: {exc}",
                )
                orchestrator.bus.publish_text(
                    task.id,
                    orchestrator.name,
                    f"Denied tool execution: {exc}",
                    step_id=step.id,
                )
                result = ToolResult(
                    tool_call_id=f"{step.id}_content_revalidation_guard",
                    ok=False,
                    error=str(exc),
                    observation=f"{step.tool_name} was blocked by the content revalidation guard.",
                )
                db.upsert_model("tool_results", result)
                return RuntimeExecutionResult("fatal_denied", result)
        try:
            authorization = authorize_automation_execution(
                task=task,
                step=step,
                tool=tool,
                runtime=runtime,
                args=args,
                threaded_tools=threaded_tools,
            )
        except AutomationExecutionDenied as exc:
            runtime.abort_requested = exc.hard_stop
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED if exc.hard_stop else TaskStatus.PAUSED,
                final_summary=(
                    f"Automated execution was denied: {exc.reason}"
                    if exc.hard_stop
                    else f"Automated execution was paused for user review: {exc.reason}"
                ),
            )
            orchestrator.bus.publish_text(
                task.id,
                orchestrator.name,
                (
                    f"Denied automated tool execution: {exc.reason}"
                    if exc.hard_stop
                    else f"Paused automated tool execution: {exc.reason}"
                ),
                step_id=step.id,
            )
            result = ToolResult(
                tool_call_id=f"{step.id}_automation_guard",
                ok=False,
                error=exc.reason,
                observation=f"{step.tool_name} was blocked by the automation execution guard.",
            )
            db.upsert_model("tool_results", result)
            return RuntimeExecutionResult("fatal_denied", result)
        if authorization is not None:
            runtime.extra_context["automation_action_fingerprint"] = authorization.action_fingerprint
            runtime.extra_context["automation_intent_capsule_id"] = authorization.capsule_id
            runtime.extra_context["automation_budget_version"] = authorization.budget_version
            runtime.extra_context["automation_budget_soft_exceeded"] = authorization.soft_exceeded
        call, created = self._publish_tool_call_proposal(
            task,
            step,
            tool,
            runtime,
            args,
            approval_id=approval_id,
        )
        if not created:
            existing = await self._handle_existing_tool_execution(task, step, tool, runtime, call, approval_id)
            if existing is not None:
                return existing
        stage = "approved_tool_call_proposed" if approval_id else "tool_call_proposed"
        if not orchestrator._supervise_new_agent_messages(task.id, stage):
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task before executing a tool call.",
            )
            return RuntimeExecutionResult("fatal_denied")

        claimed_call = mark_tool_call_executing(call)
        if claimed_call is None:
            current_call = load_tool_call(call.id) or call
            existing = await self._handle_existing_tool_execution(
                task,
                step,
                tool,
                runtime,
                current_call,
                approval_id,
            )
            if existing is not None:
                return existing
            return self._block_duplicate_tool_execution(task, step, current_call)
        call = claimed_call

        try:
            result = await self._execute_tool_call(
                task,
                step,
                tool,
                runtime,
                call,
                args,
                threaded_tools=threaded_tools,
                approval_id=approval_id,
            )
        except asyncio.CancelledError:
            # A cancelled in-flight tool must not leave the ToolCall row stuck in
            # "executing": that row would block every future resume of the same
            # step (same execution_key) as a duplicate until the process
            # restarts. Record the outcome as unknown (the side effect may or may
            # not have applied) so replay is deliberately gated instead of the
            # step being permanently wedged.
            mark_tool_call_outcome_unknown(call, expected_status="executing")
            raise

        upstream_envelopes = collect_content_envelopes(runtime.extra_context.get("upstream_content_envelopes"))
        if result.content_envelope is not None:
            upstream_envelopes.append(result.content_envelope)
        result.content_envelope = content_envelope_for_tool_output(
            step.tool_name,
            result.output,
            tool_call_id=result.tool_call_id,
            task_scope=task.id,
            trust_tier=tool.trust_tier,
            external_network=tool.external_network,
            resource_kinds=tool.resource_kinds,
            upstream=upstream_envelopes,
            output_provenance=result._output_provenance,  # noqa: SLF001 - in-process lifecycle handoff.
        )
        # Keep raw output in memory until post-tool review has inspected it in
        # full. The pending stub preserves rollback evidence and also gives
        # crash recovery a deterministic artifact identity to clean up if the
        # process stops after review while publishing a large allowed result.
        pending_result = _pending_review_result_stub(result)
        db.upsert_model("tool_results", pending_result)
        try:
            post_tool_review = self._review_tool_result(task, step, tool, result, call.risk_level)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: review failure makes outcome unknown
            _discard_persisted_result(result, runtime, tool_name=step.tool_name)
            mark_tool_call_outcome_unknown(call, expected_status="executing")
            raise
        result = result.model_copy(
            update={
                "runtime_review_id": post_tool_review.id,
                "runtime_review_verdict": post_tool_review.verdict.value,
                "runtime_review_completed": True,
            },
            deep=True,
        )
        if post_tool_review.verdict == SafetyVerdict.DENY:
            result = _withheld_tool_result(result, post_tool_review, runtime, tool_name=step.tool_name)
            db.upsert_model("tool_results", result)
            if bool(result.output.get("outcome_unknown")):
                mark_tool_call_outcome_unknown(call, expected_status="executing")
            else:
                mark_tool_call_committed(call)
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            if result.output.get("artifact_cleanup_required"):
                self._require_artifact_cleanup(
                    task,
                    call,
                    "A withheld result artifact requires manual cleanup before this denial is settled.",
                )
            return RuntimeExecutionResult("fatal_denied", result)

        reviewed_output = result.output
        result = _persistable_tool_result(result)
        if result.output is not reviewed_output and result.content_envelope is not None:
            result.content_envelope = propagate_content_envelope(
                result.content_envelope,
                result.output,
                sanitizer="rollback_evidence",
            )
        raw_envelope = result.content_envelope
        raw_output = result.output
        try:
            result = apply_result_budget(
                result,
                tool_name=step.tool_name,
                max_result_size=tool.max_result_size,
                runtime=runtime,
                review_completed=True,
            )
            if result.output is not raw_output and raw_envelope is not None:
                result.content_envelope = propagate_content_envelope(
                    raw_envelope,
                    result.output,
                    sanitizer="result_budget",
                )
            db.upsert_model("tool_results", result)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: persistence failure makes outcome unknown
            _discard_persisted_result(result, runtime, tool_name=step.tool_name)
            mark_tool_call_outcome_unknown(call, expected_status="executing")
            raise
        if bool(result.output.get("outcome_unknown")):
            mark_tool_call_outcome_unknown(call, expected_status="executing")
        else:
            mark_tool_call_committed(call)

        return await self._publish_result_and_finish(
            task,
            step,
            call,
            result,
            approval_id=approval_id,
            post_tool_review=post_tool_review,
        )

    async def _handle_existing_tool_execution(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        call,
        approval_id: str | None,
    ) -> RuntimeExecutionResult | None:
        if call.status == "prepared":
            return None
        result = load_tool_result(call.id)
        if result is not None and is_durable_post_tool_denial(result):
            if bool((result.output or {}).get("artifact_cleanup_required")):
                self._require_artifact_cleanup(
                    task,
                    call,
                    "A withheld result artifact requires manual cleanup before this denial can be replayed.",
                )
                set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
                record(
                    "tool.execution_replay_blocked",
                    "ToolRuntime",
                    {
                        "tool_call_id": call.id,
                        "execution_key": call.execution_key,
                        "tool_name": call.tool_name,
                        "reason": "artifact_cleanup_required",
                    },
                    task_id=task.id,
                )
                return RuntimeExecutionResult("fatal_failed", result)
            return self._replay_durable_post_tool_denial(task, step, call, result)
        if call.status != "committed":
            return self._block_duplicate_tool_execution(task, step, call)
        if result is None:
            call = mark_tool_call_outcome_unknown(call, expected_status="committed")
            return self._block_duplicate_tool_execution(task, step, call)
        if bool((result.output or {}).get("review_pending") or (result.output or {}).get("outcome_unknown")):
            call = mark_tool_call_outcome_unknown(call, expected_status="committed")
            return self._block_duplicate_tool_execution(task, step, call)
        reusable = runtime_review_allows_result_reuse(result)
        if reusable and bool((result.output or {}).get("persisted_result")):
            reusable = reviewed_large_result_artifact_valid(
                result,
                data_dir=runtime.settings.data_dir,
                task_id=task.id,
                tool_name=step.tool_name,
            )
        if not reusable:
            call, result = quarantine_result_for_reuse(call, result)
            return self._block_duplicate_tool_execution(task, step, call, result=result)
        record(
            "tool.execution_result_reused",
            "ToolRuntime",
            {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
            task_id=task.id,
        )
        post_tool_review = self._review_tool_result(task, step, tool, result, call.risk_level)
        if post_tool_review.verdict == SafetyVerdict.DENY:
            result = _withheld_tool_result(result, post_tool_review, runtime, tool_name=step.tool_name)
            db.upsert_model("tool_results", result)
            if bool(result.output.get("outcome_unknown")):
                mark_tool_call_outcome_unknown(call, expected_status="committed")
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            self.orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            if result.output.get("artifact_cleanup_required"):
                self._require_artifact_cleanup(
                    task,
                    call,
                    "A withheld result artifact requires manual cleanup before this denial is settled.",
                )
            return RuntimeExecutionResult("fatal_denied", result)
        return await self._publish_result_and_finish(
            task,
            step,
            call,
            result,
            approval_id=approval_id,
            post_tool_review=post_tool_review,
        )

    def _replay_durable_post_tool_denial(self, task: Task, step: PlanStep, call, result: ToolResult):
        reason = durable_post_tool_denial_reason(result)
        set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
        self.orchestrator._set_status(task, TaskStatus.DENIED, final_summary=reason)
        record(
            "tool.execution_denial_reused",
            "ToolRuntime",
            {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
            task_id=task.id,
        )
        return RuntimeExecutionResult("fatal_denied", result)

    def _require_artifact_cleanup(self, task: Task, call, _summary: str) -> None:
        task.metadata = {
            **task.metadata,
            "execution_recovery": {
                "version": 1,
                "state": "artifact_cleanup_required",
                "issues": [
                    {"code": "artifact_cleanup_required", "tool_call_id": call.id},
                    {"code": "outcome_unknown", "tool_call_id": call.id},
                ],
                "tool_call_ids": [call.id],
                "requires_user_review": True,
                "automatic_replay_blocked": True,
            },
        }
        self.orchestrator._set_status(
            task,
            TaskStatus.REPAIR_REQUIRED,
            final_summary=(
                "A withheld tool-result artifact could not be deleted. "
                "Automatic replay is blocked; manual repair is required."
            ),
        )

    def _block_duplicate_tool_execution(
        self,
        task: Task,
        step: PlanStep,
        call,
        *,
        result: ToolResult | None = None,
    ) -> RuntimeExecutionResult:
        outcome_unknown = call.status == "outcome_unknown"
        detail = (
            "A prior execution may already have applied its side effect; automatic replay is blocked."
            if outcome_unknown
            else "The same tool execution is already in progress; a duplicate side effect was blocked."
        )
        if outcome_unknown:
            task.metadata = {
                **task.metadata,
                "execution_recovery": {
                    "version": 1,
                    "state": "outcome_unknown",
                    "issues": [{"code": "outcome_unknown", "tool_call_id": call.id}],
                    "tool_call_ids": [call.id],
                    "requires_user_review": True,
                    "automatic_replay_blocked": True,
                },
            }
        set_step_status(step, StepStatus.FAILED, actor="ToolRuntime")
        self.orchestrator._set_status(
            task,
            TaskStatus.REPAIR_REQUIRED if outcome_unknown else TaskStatus.FAILED,
            final_summary=detail,
        )
        record(
            "tool.execution_replay_blocked",
            "ToolRuntime",
            {
                "tool_call_id": call.id,
                "execution_key": call.execution_key,
                "tool_name": call.tool_name,
                "status": call.status,
            },
            task_id=task.id,
        )
        result = result or ToolResult(
            tool_call_id=call.id,
            ok=False,
            output={"outcome_unknown": outcome_unknown, "automatic_replay_blocked": True},
            error=detail,
            observation=detail,
        )
        return RuntimeExecutionResult("fatal_failed", result)

    def _validate_input(self, tool: ToolDefinition, args: dict[str, Any], runtime: TaskRuntimeContext) -> str:
        if not tool.validate_input:
            return ""
        try:
            tool.validate_input(args, runtime.tool_context())
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            error = _safe_runtime_error_text(exc)
            record(
                "tool.validation_failed", "ToolRuntime", {"tool": tool.name, "error": error}, task_id=runtime.task.id
            )
            return error
        return ""

    def _check_permission(self, tool: ToolDefinition, args: dict[str, Any], runtime: TaskRuntimeContext) -> str:
        path_error = authorized_path_error(tool, args, runtime.tool_context())
        if path_error:
            return path_error
        if not tool.permission_policy:
            return ""
        try:
            allowed = tool.permission_policy(args, runtime.tool_context())
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            error = _safe_runtime_error_text(exc)
            record(
                "tool.permission_failed", "ToolRuntime", {"tool": tool.name, "error": error}, task_id=runtime.task.id
            )
            return error
        return "" if allowed else f"Tool permission policy denied {tool.name}."

    def _user_permission_error(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        runtime: TaskRuntimeContext,
        safety: Any | None,
    ) -> str:
        # safety is usually a SafetyReviewAgent wrapping a PolicyEngine at
        # .policy; tests may hand a PolicyEngine directly.
        engine = None
        for candidate in (safety, getattr(safety, "policy", None)):
            if candidate is not None and (
                getattr(candidate, "permission_store", None) is not None
                or getattr(candidate, "permission_policy", None) is not None
            ):
                engine = candidate
                break
        if engine is None:
            return ""
        try:
            from app.policy.permissions import evaluate_user_permission_for_tool

            decision = evaluate_user_permission_for_tool(
                tool_name=tool.name,
                args=args,
                context=runtime.tool_context(),
                policy_engine=engine,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            error = _safe_runtime_error_text(exc)
            record(
                "tool.permission_store_failed",
                "ToolRuntime",
                {"tool": tool.name, "error": error},
                task_id=runtime.task.id,
            )
            return f"Permission policy evaluation failed for {tool.name}: {error}"
        if getattr(decision, "allowed", True):
            return ""
        reason = str(getattr(decision, "reason", "") or f"Permission policy denied {tool.name}.")
        rule_id = str(getattr(decision, "matched_rule_id", "") or getattr(decision, "rule_id", "") or "")
        return f"{reason} (rule: {rule_id})" if rule_id else reason

    def _observation(self, step: PlanStep, tool: ToolDefinition, output: dict[str, Any]) -> str:
        if tool.result_summary:
            try:
                summary = tool.result_summary(output)
                if summary:
                    return summary
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: result summarizers are best-effort diagnostics.
                logger.debug("tool result summary failed for %s: %s", tool.name, exc, exc_info=True)
        return step.expected_observation or f"{step.tool_name} completed."

    def _authorized_path_error(self, tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> str:
        return authorized_path_error(tool, args, context)

    def _ensure_authorized_paths(self, tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> None:
        ensure_authorized_paths(tool, args, context)


def _tool_requires_content_revalidation(tool: ToolDefinition, args: dict[str, Any]) -> bool:
    if args.get("dry_run") is True:
        return False
    name = tool.name.casefold()
    effects = {str(item).strip().casefold() for item in tool.effects if str(item).strip()}
    capabilities = {str(item).strip().casefold() for item in tool.capabilities if str(item).strip()}
    sensitive_keys = {str(item).strip().casefold() for item in tool.sensitive_arg_keys if str(item).strip()}
    side_effect_markers = {
        "browser_write",
        "click",
        "control",
        "create",
        "delete",
        "external_post",
        "input",
        "modify",
        "move",
        "send",
        "submit",
        "type",
        "upload",
        "write",
    }
    credential_markers = {"credential", "credentials", "password", "secret", "token"}
    return bool(
        tool.destructive
        or not tool.is_read_only()
        or tool.external_network
        or effects.intersection(side_effect_markers)
        or capabilities.intersection({"mcp", "credential", "credentials"})
        or sensitive_keys.intersection(credential_markers)
        or name.startswith("mcp.")
        or "credential" in name
    )
