from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.core import db
from app.core.audit import record
from app.core.schemas import (
    Approval,
    ApprovalStatus,
    MessageType,
    Plan,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolResult,
    now_iso,
)
from app.orchestration.events import ApprovalNeeded, SafetyReviewDone, SubagentResponded, ToolExecuted
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.approval_binding import args_binding_hmac, binding_preview, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


class StepExecutionHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator
        self.tool_runtime = ToolRuntime(orchestrator)

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register("subagent.responded", self.handle_subagent_responded)
        dispatcher.register("safety_review.done", self.handle_safety_review_done)
        dispatcher.register("approval.needed", self.handle_approval_needed)
        dispatcher.register("tool.executed", self.handle_tool_executed)

    def handle_subagent_responded(self, event: SubagentResponded) -> None:  # pragma: no cover - registration hook
        return None

    def handle_safety_review_done(self, event: SafetyReviewDone) -> None:  # pragma: no cover - registration hook
        return None

    def handle_approval_needed(self, event: ApprovalNeeded) -> None:  # pragma: no cover - registration hook
        return None

    def handle_tool_executed(self, event: ToolExecuted) -> None:  # pragma: no cover - registration hook
        return None

    async def _yield_if_parallel(self, threaded_tools: bool) -> None:
        if threaded_tools:
            await asyncio.sleep(0)

    def _runtime_context(self, task: Task, context: dict[str, Any] | None = None) -> TaskRuntimeContext:
        orchestrator = self.orchestrator
        raw = context or orchestrator._tool_context()
        settings = raw.get("settings")
        if settings is None:
            from app.llm.registry import get_effective_settings

            settings = get_effective_settings()
        runtime = TaskRuntimeContext.from_task(task, settings, orchestrator.bus)
        if raw.get("allowed_directories") is not None:
            runtime.allowed_directories = list(raw.get("allowed_directories") or [])
        runtime.extra_context.update({key: value for key, value in raw.items() if key not in {"allowed_directories", "settings"}})
        return runtime

    async def execute_step(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool = False,
    ) -> StepExecutionOutcome:
        orchestrator = self.orchestrator
        step.task_id = step.task_id or task.id
        orchestrator._set_status(task, TaskStatus.EXECUTING_STEP)
        await self._yield_if_parallel(threaded_tools)
        try:
            tool = orchestrator.registry.get(step.tool_name)
        except KeyError as exc:
            set_step_status(step, StepStatus.FAILED, actor="StepExecutionHandler")
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(str(exc)))
            return StepExecutionOutcome("fatal_failed")

        risk = tool.risk_level
        runtime = self._runtime_context(task, context)
        safety_outcome = await self.tool_runtime.review_and_maybe_prepare_approval(
            task,
            step,
            tool,
            runtime,
            threaded_tools=threaded_tools,
        )
        if safety_outcome.kind in {"step_denied", "fatal_denied"}:
            return StepExecutionOutcome(safety_outcome.kind, safety_outcome.result)
        if safety_outcome.kind == "waiting_user_approval":
            return StepExecutionOutcome(safety_outcome.kind, safety_outcome.result)
        if safety_outcome.kind not in {"allowed"}:
            return StepExecutionOutcome(safety_outcome.kind, safety_outcome.result)

        action = await orchestrator._consult_subagent(task, step, observation=observation)
        await self._yield_if_parallel(threaded_tools)
        if action and action.kind == "done":
            set_step_status(step, StepStatus.SKIPPED, actor="StepExecutionHandler")
            result = ToolResult(
                tool_call_id=f"{step.id}_subagent_done",
                ok=True,
                observation=action.rationale or f"{step.tool_name} already complete.",
            )
            orchestrator.bus.publish_text(
                task.id,
                orchestrator.name,
                f"Skipped step after {step.agent_name} marked it done: {step.description}",
                message_type=MessageType.OBSERVATION,
                step_id=step.id,
                structured_payload={"subagent_action": action.model_dump(), "skipped": True},
            )
            orchestrator._supervise_new_agent_messages(task.id, "subagent_done")
            return StepExecutionOutcome("skipped", result)
        if action and action.kind == "request_revision":
            orchestrator._handle_subagent_revision_request(task, step, action)
            result = ToolResult(
                tool_call_id=f"{step.id}_revision_request",
                ok=True,
                observation=action.follow_up_question or action.rationale or "Subagent requested plan revision.",
            )
            if not orchestrator._supervise_new_agent_messages(task.id, "subagent_revision_request"):
                set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after a subagent revision request.",
                )
                return StepExecutionOutcome("fatal_denied", result)
            return StepExecutionOutcome("revision_requested", result)
        if action and action.kind == "propose_tool":
            original_tool_name = step.tool_name
            original_args = dict(step.args or {})
            proposed_tool_name = action.tool_name or step.tool_name
            proposed_args = {**original_args, **dict(action.args or {})} if proposed_tool_name == step.tool_name else dict(action.args or {})
            if threaded_tools and (proposed_tool_name != original_tool_name or proposed_args != original_args):
                action.kind = "request_revision"
                action.rationale = action.rationale or (
                    "Subagent proposed a different tool call while this step was in a parallel batch."
                )
                action.follow_up_question = action.follow_up_question or (
                    "Run this step serially so the revised tool call can receive a fresh parallel-safety review."
                )
                orchestrator._handle_subagent_revision_request(task, step, action)
                result = ToolResult(
                    tool_call_id=f"{step.id}_parallel_mutation_blocked",
                    ok=True,
                    observation=action.follow_up_question or action.rationale,
                )
                if not orchestrator._supervise_new_agent_messages(task.id, "subagent_parallel_mutation_blocked"):
                    set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
                    orchestrator._set_status(
                        task,
                        TaskStatus.DENIED,
                        final_summary="SafetyReviewAgent stopped the task after a parallel tool mutation was blocked.",
                    )
                    return StepExecutionOutcome("fatal_denied", result)
                return StepExecutionOutcome("revision_requested", result)
            try:
                tool = orchestrator._apply_subagent_tool_proposal(task, step, action)
            except KeyError as exc:
                set_step_status(step, StepStatus.FAILED, actor="StepExecutionHandler")
                orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(str(exc)))
                orchestrator.bus.publish_text(
                    task.id,
                    orchestrator.name,
                    f"Subagent proposed an unavailable tool: {action.tool_name}",
                    message_type=MessageType.REVISION,
                    step_id=step.id,
                    structured_payload={"subagent_action": action.model_dump(), "error": str(exc)},
                )
                orchestrator._supervise_new_agent_messages(task.id, "subagent_invalid_tool")
                return StepExecutionOutcome("fatal_failed")
            risk = tool.risk_level
            if not orchestrator._supervise_new_agent_messages(task.id, "subagent_proposal_applied"):
                set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after applying a subagent proposal.",
                )
                return StepExecutionOutcome("fatal_denied")
            orchestrator._persist_plan_update(plan, "Plan step updated from subagent tool proposal.")
            await self._yield_if_parallel(threaded_tools)
            if step.tool_name != original_tool_name or dict(step.args or {}) != original_args:
                runtime = self._runtime_context(task, context)
                review_outcome = await self.tool_runtime.review_and_maybe_prepare_approval(
                    task,
                    step,
                    tool,
                    runtime,
                    threaded_tools=threaded_tools,
                )
                if review_outcome.kind != "allowed":
                    return StepExecutionOutcome(review_outcome.kind, review_outcome.result)
        await self._yield_if_parallel(threaded_tools)
        execution = await self.tool_runtime.execute_allowed(
            task,
            step,
            tool,
            runtime,
            threaded_tools=threaded_tools,
        )
        return StepExecutionOutcome(execution.kind, execution.result)

    async def execute_approved_step(self, approval: Approval) -> Task:
        orchestrator = self.orchestrator
        latest_approval_data = db.fetch_one("approvals", approval.id)
        if latest_approval_data:
            approval = Approval.model_validate(latest_approval_data)
        task_data = db.fetch_one("tasks", approval.task_id)
        if not task_data:
            raise KeyError(f"Task not found: {approval.task_id}")
        task = Task.model_validate(task_data)

        plan = orchestrator._latest_plan_for_task(task.id)
        step = next((item for item in plan.steps if item.id == approval.step_id), None)
        if step is None:
            raise KeyError(f"Step not found for approval: {approval.step_id}")
        if step.status == StepStatus.SUCCEEDED:
            return task
        if approval.consumed_at:
            return self._deny_approved_step(task, plan, step, approval, "Approval has already been consumed.")
        state_error = self._approval_execution_state_error(task, step)
        if state_error:
            return self._expire_nonexecutable_step(task, plan, step, approval, state_error)

        tool = orchestrator.registry.get(step.tool_name)
        binding_error = self._approval_binding_error(approval, task, step, tool)
        if binding_error:
            return self._deny_approved_step(task, plan, step, approval, binding_error)

        action = None if approval.approval_type == "remote_input" else await orchestrator._consult_subagent(task, step, observation=None)
        if action and action.kind == "done":
            set_step_status(step, StepStatus.SKIPPED, actor="StepExecutionHandler")
            orchestrator._persist_plan_update(plan, "Approved step skipped after subagent marked it done.")
            orchestrator._set_status(task, TaskStatus.COMPLETED, final_summary="Approved step was already complete.")
            return task
        if action and action.kind == "request_revision":
            orchestrator._handle_subagent_revision_request(task, step, action)
            set_step_status(step, StepStatus.SKIPPED, actor="StepExecutionHandler")
            orchestrator._persist_plan_update(plan, "Approved step paused after subagent requested plan revision.")
            orchestrator._set_status(
                task,
                TaskStatus.PAUSED,
                final_summary="A subagent requested plan revision before the approved step could execute.",
            )
            return task
        if action and action.kind == "propose_tool":
            proposed_tool_name = action.tool_name or step.tool_name
            merged_args = {**dict(step.args or {}), **dict(action.args or {})}
            if proposed_tool_name != step.tool_name or merged_args != step.args:
                orchestrator._handle_subagent_revision_request(task, step, action)
                set_step_status(step, StepStatus.SKIPPED, actor="StepExecutionHandler")
                orchestrator._persist_plan_update(plan, "Approved step paused because subagent proposed a different tool call.")
                orchestrator._set_status(
                    task,
                    TaskStatus.PAUSED,
                    final_summary="A subagent proposed a different tool call after approval; a fresh review is required.",
                )
                return task

        runtime = self._runtime_context(task)
        resource_error = await self._approval_resource_state_error(approval, step, tool, runtime)
        if resource_error:
            set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
            orchestrator._persist_plan_update(plan, "Approved step denied because the reviewed resource state changed.")
            orchestrator._set_status(
                task,
                TaskStatus.PAUSED,
                final_summary="The reviewed file state changed after approval. Please run a fresh preview.",
            )
            record(
                "approval.resource_state_mismatch",
                orchestrator.name,
                {"approval_id": approval.id, "reason": resource_error, "tool_name": step.tool_name},
                task_id=task.id,
            )
            return task

        claimed = db.claim_approval_for_execution(approval.id, now_iso())
        if not claimed:
            return self._deny_approved_step(task, plan, step, approval, "Approval has already been consumed.")
        approval = Approval.model_validate(claimed)

        approved_args = {**step.args, "dry_run": False, "approved": True, "approval_id": approval.id}
        orchestrator._persist_plan_update(plan, "Plan status updated after user approval.")
        execution = await self.tool_runtime.execute_allowed(
            task,
            step,
            tool,
            runtime,
            approved_args=approved_args,
            approval_id=approval.id,
        )
        result = execution.result
        if execution.kind == "fatal_denied":
            orchestrator._persist_plan_update(plan, "Plan denied during approved tool execution.")
            return task

        if result and result.ok:
            db.upsert_model("approvals", approval, status=approval.status)
            pending_approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task.id, "pending"), limit=100)
            target_status = (
                TaskStatus.WAITING_USER_APPROVAL
                if pending_approvals
                else TaskStatus.EXECUTING_STEP
                if self._has_pending_ready_steps(plan)
                else TaskStatus.COMPLETED
            )
            summary = (
                "Approved modifying operation completed; continuing remaining plan steps."
                if target_status == TaskStatus.EXECUTING_STEP
                else
                "Approved file trash operation completed."
                if step.tool_name == "file.trash"
                else "Approved modifying operation completed."
            )
            orchestrator._set_status(task, target_status, final_summary=summary)
        else:
            error = result.error if result else "Approved tool execution did not return a result."
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(error))
        orchestrator._persist_plan_update(plan, "Plan status updated after approved tool execution.")
        record(
            "task.approved_step_executed",
            orchestrator.name,
            {"approval_id": approval.id, "ok": bool(result and result.ok), "runtime_kind": execution.kind},
            task_id=task.id,
        )
        return task

    def _has_pending_ready_steps(self, plan: Plan) -> bool:
        try:
            by_id, _dependents = self.orchestrator._build_step_graph(plan)
        except ValueError:
            return False
        pending = {step.id for step in plan.steps if step.status == StepStatus.PENDING}
        return bool(self.orchestrator._ready_steps(pending, by_id))

    def _approval_execution_state_error(self, task: Task, step: PlanStep) -> str:
        if task.execution_stage != ExecutionStage.AWAITING_APPROVAL:
            return f"Task execution stage is {task.execution_stage}; expected awaiting_approval."
        if step.status != StepStatus.WAITING_USER_APPROVAL:
            return f"Step status is {step.status}; expected waiting_user_approval."
        return ""

    def _expire_nonexecutable_step(self, task: Task, plan: Plan, step: PlanStep, approval: Approval, reason: str) -> Task:
        orchestrator = self.orchestrator
        db.expire_approval_if_unconsumed(approval.id, now_iso(), reason)
        if step.status == StepStatus.WAITING_USER_APPROVAL:
            set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
            orchestrator._persist_plan_update(plan, "Approved step expired because task state no longer allows execution.")
        record(
            "approval.state_mismatch",
            orchestrator.name,
            {"approval_id": approval.id, "reason": reason, "tool_name": step.tool_name},
            task_id=task.id,
        )
        return task

    def _deny_approved_step(self, task: Task, plan: Plan, step: PlanStep, approval: Approval, reason: str) -> Task:
        orchestrator = self.orchestrator
        replay = "already been consumed" in reason.casefold()
        set_step_status(step, StepStatus.DENIED, actor="StepExecutionHandler")
        orchestrator._persist_plan_update(
            plan,
            "Approved step denied because approval was already consumed."
            if replay
            else "Approved step denied because approval binding no longer matches.",
        )
        orchestrator._set_status(
            task,
            TaskStatus.PAUSED,
            final_summary="Approval has already been consumed. Please run a fresh preview before retrying."
            if replay
            else "Approval is stale or no longer matches the reviewed dry-run preview. Please run a fresh preview.",
        )
        record(
            "approval.replay_blocked" if replay else "approval.binding_mismatch",
            orchestrator.name,
            {"approval_id": approval.id, "reason": reason, "tool_name": step.tool_name},
            task_id=task.id,
        )
        return task

    def _approval_binding_error(self, approval: Approval, task: Task, step: PlanStep, tool) -> str:
        if approval.status != ApprovalStatus.APPROVED:
            return f"Approval status is {approval.status}; expected approved."
        if approval.consumed_at:
            return "Approval has already been consumed."
        if not all(
            [
                approval.tool_name,
                approval.args_binding_hmac,
                approval.preview_hmac,
                approval.settings_fingerprint,
                approval.permission_policy_version,
                approval.tool_version,
            ]
        ):
            return "Approval lacks binding metadata."
        runtime = self._runtime_context(task)
        if approval.tool_name != step.tool_name:
            return "Approved tool name does not match current plan step."
        if approval.risk_level and approval.risk_level != tool.risk_level.value:
            return "Approved risk level does not match current tool risk."
        if approval.tool_version != getattr(tool, "tool_version", "1"):
            return "Approved tool version does not match current tool definition."
        expected_args = args_binding_hmac(step.tool_name, step.args, task_id=task.id, step_id=step.id)
        if not hmac_compare(approval.args_binding_hmac, expected_args):
            return "Approved arguments do not match current plan step."
        expected_preview = preview_hmac(approval.diff_preview)
        if not hmac_compare(approval.preview_hmac, expected_preview):
            return "Approval preview was modified after review."
        expected_settings = settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories)
        if not hmac_compare(approval.settings_fingerprint, expected_settings):
            return "Runtime settings changed after approval preview."
        expected_policy = permission_policy_version(PermissionStore().updated_at())
        if not hmac_compare(approval.permission_policy_version, expected_policy):
            return "Permission policy changed after approval preview."
        return ""

    async def _approval_resource_state_error(self, approval: Approval, step: PlanStep, tool, runtime) -> str:
        approved_state = (approval.diff_preview or {}).get("_resource_state")
        if not approved_state:
            return ""
        try:
            current_preview = await self.tool_runtime.execute_tool_with_locks(
                tool,
                step,
                {**step.args, "dry_run": True},
                runtime.tool_context(),
                threaded=False,
            )
        except Exception as exc:  # noqa: BLE001
            return f"Could not refresh approved resource state: {exc}"
        current_state = binding_preview(current_preview).get("_resource_state")
        if current_state != approved_state:
            return "Approved resource state no longer matches current dry-run preview."
        return ""


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(str(left or ""), str(right or ""))
