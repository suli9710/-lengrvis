from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import (
    MessageType,
    OpenAIMessageRole,
    PlanStep,
    SafetyReview,
    StepStatus,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from app.orchestration.resource_state import (
    ResourceStateError,
    capture_tool_resource_state,
)
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_runtime_paths import (
    is_write_tool,
)
from app.orchestration.tool_runtime_support import (
    RuntimeExecutionResult,
    _actionable_error_text,
    _exception_error_text,
    _message_safe_text,
    _message_safe_tool_result,
)
from app.policy.execution_marker import mark_execution_approved
from app.policy.redaction import redact_value
from app.tools.schemas import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRuntimeLifecycleMixin:
    def _publish_tool_call_proposal(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        args: dict[str, Any],
        *,
        approval_id: str | None,
    ) -> ToolCall:
        orchestrator = self.orchestrator
        safe_args = self._redact_tool_args(args, tool)
        call = ToolCall(
            task_id=task.id,
            step_id=step.id,
            tool_name=step.tool_name,
            args=safe_args,
            risk_level=tool.risk_level,
            dry_run=False,
        )
        db.upsert_model("tool_calls", call)
        safe_call_payload = call.model_dump()
        orchestrator.bus.publish_text(
            task.id,
            orchestrator.name,
            f"Calling {'approved ' if approval_id else ''}tool {step.tool_name}.",
            message_type=MessageType.PROPOSAL,
            step_id=step.id,
            tool_calls=[
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": step.tool_name,
                        "arguments": safe_args,
                    },
                }
            ],
            structured_payload=safe_call_payload,
            metadata={"approval_id": approval_id, "approved_by_user": bool(approval_id)} if approval_id else None,
        )
        return call

    async def _execute_tool_call(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        call: ToolCall,
        args: dict[str, Any],
        *,
        threaded_tools: bool,
        approval_id: str | None,
    ) -> ToolResult:
        orchestrator = self.orchestrator
        before_phase = "before_approved" if approval_id else "before"
        after_phase = "after_approved" if approval_id else "after"
        before_frame = await orchestrator._capture_step_frame(task, step, before_phase)
        tool_context = runtime.tool_context()
        tool_context.update({"task_id": task.id, "step_id": step.id})
        tool_context["_expected_resource_state"] = self._approved_resource_state(approval_id)
        # This path runs only after PolicyEngine review (auto-clear) or an atomic
        # approval claim, so stamp the context as a validated execution. Tool-layer
        # write gates require this marker in addition to approved/approval_id args.
        mark_execution_approved(tool_context)
        self._publish_tool_progress(task, step, tool, call.id, "started", detail=f"Starting {step.tool_name}.")
        before_resource_state: list[dict[str, Any]] = []
        try:
            set_step_status(step, StepStatus.RUNNING, actor="ToolRuntime")
            orchestrator._set_status(task, TaskStatus.EXECUTING_TOOL)
            self._run_lifecycle_hook(tool.pre_execute, tool, args, tool_context, task_id=task.id, step_id=step.id)
            output = await self.execute_tool_with_locks(
                tool,
                step,
                args,
                tool_context,
                threaded=threaded_tools,
            )
            before_resource_state = list(tool_context.get("_resource_state_before") or [])
            self._attach_execution_resource_state(tool, args, tool_context, output, before_resource_state)
            self._run_lifecycle_hook(tool.post_execute, tool, args, tool_context, task_id=task.id, step_id=step.id)
            self._publish_tool_progress(
                task,
                step,
                tool,
                call.id,
                "completed",
                detail=f"Completed {step.tool_name}.",
                payload={"ok": not bool(output.get("error"))},
            )
            result = ToolResult(
                tool_call_id=call.id,
                ok=not bool(output.get("error")),
                output=output,
                error=_actionable_error_text(str(output.get("error", "")), step) if output.get("error") else "",
                changed_paths=list(output.get("changed_paths", [])),
                rollback_info=dict(output.get("rollback_info", {})),
                observation=self._observation(step, tool, output),
            )
        except ResourceStateError as exc:
            before_resource_state = list(tool_context.get("_resource_state_before") or [])
            output = exc.to_output()
            if before_resource_state:
                output["_resource_state_before"] = before_resource_state
            self._publish_tool_progress(
                task,
                step,
                tool,
                call.id,
                "failed",
                detail=f"{step.tool_name} blocked by resource state guard.",
                payload={"error": str(exc), "error_code": exc.error_code, **exc.details},
            )
            result = ToolResult(
                tool_call_id=call.id,
                ok=False,
                output=output,
                error=str(exc),
                observation=f"{step.tool_name} blocked because resource state changed or was not read.",
            )
        except Exception as exc:  # noqa: BLE001
            error_text = _exception_error_text(exc, step)
            self._publish_tool_progress(
                task,
                step,
                tool,
                call.id,
                "failed",
                detail=f"{step.tool_name} failed.",
                payload={"error": error_text},
            )
            result = ToolResult(
                tool_call_id=call.id,
                ok=False,
                error=error_text,
                observation=(
                    f"{step.tool_name} raised {type(exc).__name__}; check the supplied args "
                    f"({', '.join(sorted((step.args or {}).keys())) or 'none'}) or try an alternative tool."
                ),
            )
        finally:
            after_frame = await orchestrator._capture_step_frame(task, step, after_phase)
            orchestrator._publish_step_recording(
                task,
                step,
                [before_frame, after_frame],
                tool_name=step.tool_name,
                agent=step.agent_name,
                metadata={"approval_id": approval_id, "approved_by_user": True} if approval_id else None,
            )

        return result

    async def _publish_result_and_finish(
        self,
        task: Task,
        step: PlanStep,
        call: ToolCall,
        result: ToolResult,
        *,
        approval_id: str | None,
        post_tool_review: SafetyReview,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        orchestrator.bus.publish_text(
            task.id,
            step.agent_name,
            _message_safe_text(result.observation if result.ok else orchestrator._friendly_tool_error(result.error)),
            role=OpenAIMessageRole.TOOL,
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            tool_call_id=call.id,
            structured_payload=_message_safe_tool_result(result).model_dump(),
            metadata={
                "post_tool_review_id": post_tool_review.id,
                "post_tool_review_verdict": post_tool_review.verdict.value,
                "post_tool_review_target": post_tool_review.target_type,
            },
        )
        stage = "approved_tool_observation" if approval_id else "tool_observation"
        if not orchestrator._supervise_new_agent_messages(task.id, stage):
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after observing tool output.",
            )
            return RuntimeExecutionResult("fatal_denied", result)

        set_step_status(step, StepStatus.SUCCEEDED if result.ok else StepStatus.FAILED, actor="ToolRuntime")
        await orchestrator._reflect_on_step(task, step, result)
        return RuntimeExecutionResult("succeeded" if result.ok else "failed", result)

    def _approved_resource_state(self, approval_id: str | None) -> list[dict[str, Any]]:
        if not approval_id:
            return []
        approval_data = db.fetch_one("approvals", approval_id)
        if not approval_data:
            return []
        diff_preview = approval_data.get("diff_preview") if isinstance(approval_data, dict) else {}
        if not isinstance(diff_preview, dict):
            return []
        state = diff_preview.get("_resource_state")
        return state if isinstance(state, list) else []

    def _attach_execution_resource_state(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        output: dict[str, Any],
        before_state: list[dict[str, Any]],
    ) -> None:
        if args.get("dry_run") is True or not is_write_tool(tool):
            return
        output.setdefault("_resource_state_before", before_state)
        output["_resource_state_after"] = capture_tool_resource_state(tool, args, context, preview=output)

    def _redact_tool_args(self, args: dict[str, Any], tool: ToolDefinition) -> dict[str, Any]:
        redacted = redact_value(args)
        safe_args = dict(redacted) if isinstance(redacted, dict) else {"args": redacted}
        for key in getattr(tool, "sensitive_arg_keys", []) or []:
            if str(key) in safe_args:
                safe_args[str(key)] = "***"
        return safe_args

    def _publish_tool_progress(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        tool_call_id: str,
        status: str,
        *,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.orchestrator.bus.publish_text(
                task.id,
                "ToolRuntime",
                detail or f"{tool.name} {status}.",
                message_type=MessageType.NOTIFICATION,
                step_id=step.id,
                tool_call_id=tool_call_id,
                structured_payload=tool.progress_event(
                    status,
                    task_id=task.id,
                    step_id=step.id,
                    tool_call_id=tool_call_id,
                    detail=detail,
                    payload=payload,
                ),
                metadata={"event_type": "tool.progress", "tool_name": tool.name, "tool_status": status},
            )
        except Exception as exc:  # noqa: BLE001
            record(
                "tool.progress_publish_failed",
                "ToolRuntime",
                {"tool": tool.name, "status": status, "error": str(exc), "step_id": step.id},
                task_id=task.id,
            )

    def _run_lifecycle_hook(
        self,
        hook: Any,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        task_id: str,
        step_id: str | None,
    ) -> None:
        if hook is None:
            return
        try:
            hook(self._hook_snapshot(args), self._hook_snapshot(context))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            record(
                "tool.lifecycle_hook_failed",
                "ToolRuntime",
                {"tool": tool.name, "error": str(exc), "step_id": step_id},
                task_id=task_id,
            )

    def _hook_snapshot(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({key: self._hook_snapshot(child) for key, child in value.items()})
        if isinstance(value, list | tuple | set | frozenset):
            return tuple(self._hook_snapshot(child) for child in value)
        try:
            return copy.deepcopy(value)
        except (copy.Error, AttributeError, RecursionError, TypeError, ValueError):
            return repr(value)
