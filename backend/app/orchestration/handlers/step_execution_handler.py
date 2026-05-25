from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.core import db
from app.core.audit import record
from app.core.schemas import (
    Approval,
    MessageType,
    Plan,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolResult,
)
from app.orchestration.events import ApprovalNeeded, SafetyReviewDone, SubagentResponded, ToolExecuted
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_runtime import ToolRuntime

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
            step.status = StepStatus.FAILED
            orchestrator._set_status(task, TaskStatus.FAILED, final_summary=orchestrator._friendly_tool_error(str(exc)))
            return StepExecutionOutcome("fatal_failed")

        risk = tool.risk_level
        action = await orchestrator._consult_subagent(task, step, observation=observation)
        await self._yield_if_parallel(threaded_tools)
        if action and action.kind == "done":
            step.status = StepStatus.SKIPPED
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
                step.status = StepStatus.DENIED
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after a subagent revision request.",
                )
                return StepExecutionOutcome("fatal_denied", result)
            return StepExecutionOutcome("revision_requested", result)
        if action and action.kind == "propose_tool":
            try:
                tool = orchestrator._apply_subagent_tool_proposal(task, step, action)
            except KeyError as exc:
                step.status = StepStatus.FAILED
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
                step.status = StepStatus.DENIED
                orchestrator._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after applying a subagent proposal.",
                )
                return StepExecutionOutcome("fatal_denied")
            orchestrator._persist_plan_update(plan, "Plan step updated from subagent tool proposal.")
            await self._yield_if_parallel(threaded_tools)
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

        tool = orchestrator.registry.get(step.tool_name)
        action = None if approval.approval_type == "remote_input" else await orchestrator._consult_subagent(task, step, observation=None)
        if action and action.kind == "done":
            step.status = StepStatus.SKIPPED
            orchestrator._persist_plan_update(plan, "Approved step skipped after subagent marked it done.")
            orchestrator._set_status(task, TaskStatus.COMPLETED, final_summary="Approved step was already complete.")
            return task
        if action and action.kind == "request_revision":
            orchestrator._handle_subagent_revision_request(task, step, action)
            step.status = StepStatus.SKIPPED
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
                step.status = StepStatus.SKIPPED
                orchestrator._persist_plan_update(plan, "Approved step paused because subagent proposed a different tool call.")
                orchestrator._set_status(
                    task,
                    TaskStatus.PAUSED,
                    final_summary="A subagent proposed a different tool call after approval; a fresh review is required.",
                )
                return task

        runtime = self._runtime_context(task)
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
            pending_approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task.id, "pending"), limit=100)
            target_status = TaskStatus.WAITING_USER_APPROVAL if pending_approvals else TaskStatus.COMPLETED
            summary = (
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
