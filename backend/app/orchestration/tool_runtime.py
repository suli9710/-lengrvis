from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from weakref import WeakKeyDictionary

from app.core import db
from app.core.audit import record
from app.core.errors import SecurityError
from app.core.paths import resolve_task_path
from app.core.schemas import (
    Approval,
    MessageType,
    OpenAIMessageRole,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
)
from app.llm.registry import get_effective_settings
from app.orchestration.resource_state import (
    ResourceStateError,
    attach_dry_run_resource_state,
    capture_tool_resource_state,
    remember_read_states_for_tool,
    validate_write_preconditions,
)
from app.orchestration.result_budget import apply_result_budget
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    settings_fingerprint,
)
from app.policy.execution_marker import mark_execution_approved
from app.policy.model_boundary import model_control_arg_error
from app.policy.permission_modes import permission_mode_from_context, trusted_reversible_edit_allowed
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import BROWSER_WRITE_TOOLS
from app.policy.redaction import redact_value
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.tools.schemas import ToolDefinition

logger = logging.getLogger(__name__)

_DEFAULT_TOOL_TIMEOUT_SECONDS = 300.0
_MAX_DAEMON_TOOL_THREADS = 32
_TOOL_THREAD_SLOTS = threading.BoundedSemaphore(_MAX_DAEMON_TOOL_THREADS)


# Failure strings that give the reflection layer nothing to reason about
# (see os_reflection._is_low_information_failure). Tool failures passing
# through the runtime are enriched so automated recovery stays possible
# instead of degrading to ask_user.
_LOW_INFORMATION_ERRORS = {"", "planned failure", "tool failed.", "failed", "unknown error"}


def _actionable_error_text(raw_error: str, step: PlanStep) -> str:
    """Ensure a tool-declared error string carries actionable context."""
    text = str(raw_error or "").strip()
    if text.casefold() not in _LOW_INFORMATION_ERRORS:
        return text
    args_hint = ", ".join(sorted((step.args or {}).keys())) or "none"
    base = text or "Tool reported a failure without details"
    return f"{base} (tool={step.tool_name}, args keys: {args_hint}). Verify the arguments or choose another tool."


def _exception_error_text(exc: BaseException, step: PlanStep) -> str:
    """Build a non-empty, typed error string for unexpected tool exceptions."""
    detail = str(exc).strip() or "no exception message"
    return f"{type(exc).__name__}: {detail} (tool={step.tool_name})"


@dataclass(slots=True)
class RuntimeExecutionResult:
    kind: str
    result: ToolResult | None = None


@dataclass(slots=True)
class _ToolWorkerHandle:
    future: asyncio.Future[Any]
    abort_event: threading.Event
    abandoned: bool = False


_SHARED_PATH_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()
_SHARED_PENDING_TOOL_COMPLETIONS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Future[Any]]] = (
    WeakKeyDictionary()
)
AUTHORIZED_PATH_ARG_KEYS = {
    "path",
    "paths",
    "source",
    "sources",
    "source_path",
    "source_paths",
    "destination",
    "destinations",
    "destination_path",
    "destination_paths",
    "dest",
    "dst",
    "target",
    "targets",
    "target_path",
    "target_paths",
    "target_folder",
    "target_folders",
    "folder",
    "folders",
    "directory",
    "directories",
    "dir",
    "dirs",
    "file",
    "files",
    "file_path",
    "file_paths",
    "input_path",
    "input_paths",
    "output_file",
    "output_files",
    "output_path",
    "output_paths",
    "output_zip",
    "root",
    "roots",
    "workspace_path",
    "working_directory",
}


class ToolRuntime:
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
                threaded_tools=threaded_tools,
            )
        return RuntimeExecutionResult("allowed")

    def _review_tool_call(
        self,
        safety: Any,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: Any,
        *,
        context: dict[str, Any],
        tool_definition: ToolDefinition,
    ):
        review_tool_call = safety.review_tool_call
        kwargs: dict[str, Any] = {}
        accepted_keywords = self._accepted_review_tool_call_keywords(review_tool_call)
        if accepted_keywords is None or "context" in accepted_keywords:
            kwargs["context"] = context
        if accepted_keywords is None or "tool_definition" in accepted_keywords:
            kwargs["tool_definition"] = tool_definition
        return review_tool_call(task_id, step_id, tool_name, args, risk_level, **kwargs)

    def _accepted_review_tool_call_keywords(self, review_tool_call: Any) -> set[str] | None:
        try:
            signature = inspect.signature(review_tool_call)
        except (TypeError, ValueError):
            return None
        accepted: set[str] = set()
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return None
            if parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}:
                accepted.add(parameter.name)
        return accepted

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
        orchestrator = self.orchestrator
        args = approved_args or step.args
        call = self._publish_tool_call_proposal(task, step, tool, args, approval_id=approval_id)
        stage = "approved_tool_call_proposed" if approval_id else "tool_call_proposed"
        if not orchestrator._supervise_new_agent_messages(task.id, stage):
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task before executing a tool call.",
            )
            return RuntimeExecutionResult("fatal_denied")

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

        result = apply_result_budget(
            result,
            tool_name=step.tool_name,
            max_result_size=tool.max_result_size,
            runtime=runtime,
        )
        db.upsert_model("tool_results", result)
        denial = self._post_result_review_denial(task, step, tool, result)
        if denial is not None:
            return denial

        return await self._publish_result_and_finish(task, step, call, result, approval_id=approval_id)

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

    def _post_result_review_denial(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        result: ToolResult,
    ) -> RuntimeExecutionResult | None:
        orchestrator = self.orchestrator
        post_tool_review = orchestrator.safety.review_tool_result(
            task.id, step.id, step.tool_name, result, tool.risk_level
        )
        if post_tool_review.verdict == SafetyVerdict.DENY:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            return RuntimeExecutionResult("fatal_denied", result)
        return None

    async def _publish_result_and_finish(
        self,
        task: Task,
        step: PlanStep,
        call: ToolCall,
        result: ToolResult,
        *,
        approval_id: str | None,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        orchestrator.bus.publish_text(
            task.id,
            step.agent_name,
            result.observation if result.ok else orchestrator._friendly_tool_error(result.error),
            role=OpenAIMessageRole.TOOL,
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            tool_call_id=call.id,
            structured_payload=result.model_dump(),
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
        if args.get("dry_run") is True or not self._is_write_tool(tool):
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
        except Exception:  # noqa: BLE001
            return repr(value)

    async def _prepare_approval(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        confirmation_message: str,
        *,
        threaded_tools: bool,
    ) -> RuntimeExecutionResult:
        orchestrator = self.orchestrator
        before_frame = await orchestrator._capture_step_frame(task, step, "before_dry_run")
        dry_run_context = runtime.tool_context()
        dry_run_context.update({"task_id": task.id, "step_id": step.id})
        try:
            preview = await self.execute_tool_with_locks(
                tool,
                step,
                {**step.args, "dry_run": True},
                dry_run_context,
                threaded=threaded_tools,
            )
        except Exception as exc:  # noqa: BLE001
            preview = {"error": str(exc)}
        finally:
            after_frame = await orchestrator._capture_step_frame(task, step, "after_dry_run")
            orchestrator._publish_step_recording(
                task,
                step,
                [before_frame, after_frame],
                tool_name=step.tool_name,
                agent=step.agent_name,
            )

        preview_result = ToolResult(
            tool_call_id=f"{step.id}_dry_run",
            ok=not bool(preview.get("error")),
            output=preview,
            error=str(preview.get("error", "")),
            observation=f"{step.tool_name} dry-run preview generated.",
        )
        if not preview_result.ok:
            set_step_status(step, StepStatus.FAILED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.FAILED,
                final_summary=orchestrator._friendly_tool_error(preview_result.error),
            )
            orchestrator.bus.publish_text(
                task.id,
                step.agent_name,
                task.final_summary,
                role=OpenAIMessageRole.TOOL,
                message_type=MessageType.OBSERVATION,
                step_id=step.id,
                structured_payload=preview_result.model_dump(),
            )
            return RuntimeExecutionResult("fatal_failed", preview_result)
        preview_contract_error = self._dry_run_preview_contract_error(preview)
        if preview_contract_error:
            preview_result.ok = False
            preview_result.error = preview_contract_error
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="Tool dry-run preview did not satisfy the approval safety contract.",
            )
            record(
                "tool.dry_run_contract_failed",
                "ToolRuntime",
                {"tool": tool.name, "reason": preview_contract_error, "step_id": step.id},
                task_id=task.id,
            )
            return RuntimeExecutionResult("fatal_denied", preview_result)

        post_preview_review = orchestrator.safety.review_tool_result(
            task.id,
            step.id,
            step.tool_name,
            preview_result,
            tool.risk_level,
        )
        if post_preview_review.verdict == SafetyVerdict.DENY:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_preview_review.safe_alternative)
            return RuntimeExecutionResult("fatal_denied", preview_result)

        safe_preview = binding_preview(preview)
        approval = Approval(
            task_id=task.id,
            step_id=step.id,
            message=confirmation_message or step.description,
            diff_preview=safe_preview,
            tool_name=step.tool_name,
            risk_level=tool.risk_level.value,
            args_binding_hmac=args_binding_hmac(step.tool_name, step.args, task_id=task.id, step_id=step.id),
            preview_hmac=preview_hmac(safe_preview),
            settings_fingerprint=settings_fingerprint(
                runtime.settings, allowed_directories=runtime.allowed_directories
            ),
            permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
            policy_mode=permission_mode_from_context(runtime.tool_context(), runtime.settings),
            tool_version=getattr(tool, "tool_version", "1"),
            tool_trust_tier=str(getattr(tool, "trust_tier", "") or ""),
            tool_effects=list(getattr(tool, "effects", []) or []),
            resource_kinds=list(getattr(tool, "resource_kinds", []) or []),
            dry_run_summary=self._approval_dry_run_summary(tool, preview),
            model_action=dict(getattr(step, "model_action", {}) or {}),
            runtime_control_fields=self._runtime_control_fields(),
            engineering_boundary=self._approval_boundary_facts(step, tool, runtime, safe_preview),
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
        set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
        self.orchestrator._set_status(
            task,
            TaskStatus.DENIED,
            final_summary="Tool requires approval but does not support a safe dry-run preview.",
        )
        record(
            "tool.approval_requires_dry_run",
            "ToolRuntime",
            {"tool": tool.name, "step_id": step.id, "risk_level": tool.risk_level.value},
            task_id=task.id,
        )
        result = ToolResult(
            tool_call_id=f"{step.id}_dry_run_required",
            ok=False,
            error="Tool requires approval but does not support dry-run.",
            observation=f"{step.tool_name} cannot be approved without dry-run support.",
        )
        return RuntimeExecutionResult("fatal_denied", result)

    def _dry_run_preview_contract_error(self, preview: dict[str, Any]) -> str:
        if preview.get("dry_run") is not True:
            return "Dry-run preview must declare dry_run=True."
        if preview.get("changed_paths"):
            return "Dry-run preview must not report changed_paths."
        return ""

    def _requires_runtime_approval(self, step: PlanStep, tool: ToolDefinition, runtime: TaskRuntimeContext) -> bool:
        mode = permission_mode_from_context(runtime.tool_context(), runtime.settings)
        if mode in {"trusted_edits", "auto_review"} and trusted_reversible_edit_allowed(tool, step.args):
            return False
        if bool(getattr(step, "requires_approval", False)):
            return True
        return tool.risk_level in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}

    def auto_approved_args(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        runtime: TaskRuntimeContext,
    ) -> dict[str, Any] | None:
        mode = permission_mode_from_context(runtime.tool_context(), runtime.settings)
        if mode not in {"trusted_edits", "auto_review"}:
            return None
        if not trusted_reversible_edit_allowed(tool, args):
            return None
        return {**dict(args or {}), "dry_run": False, "auto_approved": True}

    def _approval_dry_run_summary(self, tool: ToolDefinition, preview: dict[str, Any]) -> str:
        if preview.get("error"):
            return f"{tool.name} dry-run failed: {preview.get('error')}"
        changed = (
            preview.get("would_change")
            or preview.get("changes")
            or preview.get("items")
            or preview.get("changed_paths")
        )
        if isinstance(changed, list):
            return f"{tool.name} dry-run preview contains {len(changed)} item(s)."
        if isinstance(changed, dict):
            return f"{tool.name} dry-run preview contains {len(changed)} field(s)."
        return f"{tool.name} dry-run preview generated."

    def _runtime_control_fields(self) -> dict[str, Any]:
        return {
            "approved": "runtime_only",
            "approval_id": "runtime_only",
            "policy_decision": "runtime_only",
            "risk_level": "registry_policy_only",
            "trust_tier": "registry_only",
        }

    def _approval_boundary_facts(
        self,
        step: PlanStep,
        tool: ToolDefinition,
        runtime: TaskRuntimeContext,
        safe_preview: dict[str, Any],
    ) -> dict[str, Any]:
        policy_mode = permission_mode_from_context(runtime.tool_context(), runtime.settings)
        return {
            "policy_mode": policy_mode,
            "tool": {
                "name": tool.name,
                "risk_level": tool.risk_level.value,
                "trust_tier": str(getattr(tool, "trust_tier", "") or ""),
                "effects": list(getattr(tool, "effects", []) or []),
                "resource_kinds": list(getattr(tool, "resource_kinds", []) or []),
                "read_only": tool.is_read_only(),
                "destructive": bool(getattr(tool, "destructive", False)),
                "supports_dry_run": bool(getattr(tool, "supports_dry_run", False)),
                "tool_version": str(getattr(tool, "tool_version", "1") or "1"),
            },
            "model_action": dict(getattr(step, "model_action", {}) or {}),
            "runtime_fields": self._runtime_control_fields(),
            "binding": {
                "args_bound": True,
                "preview_bound": True,
                "settings_bound": True,
                "permission_policy_bound": True,
            },
            "dry_run": {
                "summary": self._approval_dry_run_summary(tool, safe_preview),
                "preview_keys": sorted(str(key) for key in safe_preview.keys() if not str(key).startswith("_"))[:20],
            },
        }

    def _validate_input(self, tool: ToolDefinition, args: dict[str, Any], runtime: TaskRuntimeContext) -> str:
        if not tool.validate_input:
            return ""
        try:
            tool.validate_input(args, runtime.tool_context())
        except Exception as exc:  # noqa: BLE001
            record(
                "tool.validation_failed", "ToolRuntime", {"tool": tool.name, "error": str(exc)}, task_id=runtime.task.id
            )
            return str(exc)
        return ""

    def _check_permission(self, tool: ToolDefinition, args: dict[str, Any], runtime: TaskRuntimeContext) -> str:
        path_error = self._authorized_path_error(tool, args, runtime.tool_context())
        if path_error:
            return path_error
        if not tool.permission_policy:
            return ""
        try:
            allowed = tool.permission_policy(args, runtime.tool_context())
        except Exception as exc:  # noqa: BLE001
            record(
                "tool.permission_failed", "ToolRuntime", {"tool": tool.name, "error": str(exc)}, task_id=runtime.task.id
            )
            return str(exc)
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
        except Exception as exc:  # noqa: BLE001
            record(
                "tool.permission_store_failed",
                "ToolRuntime",
                {"tool": tool.name, "error": str(exc)},
                task_id=runtime.task.id,
            )
            return f"Permission policy evaluation failed for {tool.name}: {exc}"
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
            except Exception as exc:  # noqa: BLE001 - result summarizers are best-effort diagnostics.
                logger.debug("tool result summary failed for %s: %s", tool.name, exc, exc_info=True)
        return step.expected_observation or f"{step.tool_name} completed."

    def _authorized_path_error(self, tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> str:
        try:
            self._ensure_authorized_paths(tool, args, context)
        except SecurityError as exc:
            record("tool.path_authorization_failed", "ToolRuntime", {"tool": tool.name, "error": str(exc)})
            return str(exc)
        return ""

    def _ensure_authorized_paths(self, tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> None:
        if not tool.requires_authorized_path:
            return
        allowed_directories = [str(path) for path in context.get("allowed_directories") or []]
        explicit_scope = context.get("explicit_path_scope")
        explicit_scope_text = str(explicit_scope) if explicit_scope else None
        for arg_name, value in self._candidate_authorized_paths(args):
            try:
                resolve_task_path(
                    value,
                    allowed_directories,
                    explicit_scope_text=explicit_scope_text,
                )
            except SecurityError as exc:
                raise SecurityError(f"{tool.name} path argument '{arg_name}' is not authorized: {exc}") from exc
            except OSError as exc:
                raise SecurityError(f"{tool.name} path argument '{arg_name}' could not be resolved: {exc}") from exc

    def _candidate_authorized_paths(self, args: dict[str, Any]) -> list[tuple[str, str | Path]]:
        candidates: list[tuple[str, str | Path]] = []
        self._collect_candidate_authorized_paths(args, "", candidates, top_level=True)
        return candidates

    def _collect_candidate_authorized_paths(
        self,
        value: Any,
        arg_name: str,
        candidates: list[tuple[str, str | Path]],
        *,
        top_level: bool,
    ) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                child_name = f"{arg_name}.{key}" if arg_name else key
                if self._is_authorized_path_arg_key(key, top_level=top_level):
                    self._append_authorized_path_values(child, child_name, candidates)
                elif isinstance(child, dict | list | tuple | set):
                    self._collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)
            return
        if isinstance(value, list | tuple | set):
            for index, child in enumerate(value):
                child_name = f"{arg_name}[{index}]" if arg_name else f"[{index}]"
                self._collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)

    def _append_authorized_path_values(
        self,
        value: Any,
        arg_name: str,
        candidates: list[tuple[str, str | Path]],
    ) -> None:
        if isinstance(value, str | Path) and str(value).strip():
            candidates.append((arg_name, value))
            return
        if isinstance(value, list | tuple | set):
            for index, child in enumerate(value):
                child_name = f"{arg_name}[{index}]"
                self._append_authorized_path_values(child, child_name, candidates)
            return
        if isinstance(value, dict):
            self._collect_candidate_authorized_paths(value, arg_name, candidates, top_level=False)

    def _is_authorized_path_arg_key(self, key: str, *, top_level: bool) -> bool:
        normalized = key.replace("-", "_").casefold()
        return (
            normalized in AUTHORIZED_PATH_ARG_KEYS
            or normalized.endswith("_path")
            or normalized.endswith("_paths")
            or normalized.endswith("_directory")
            or normalized.endswith("_directories")
            or normalized.endswith("_folder")
            or normalized.endswith("_folders")
            or normalized.endswith("_dir")
            or normalized.endswith("_dirs")
            or normalized.endswith("_file")
            or normalized.endswith("_files")
            or (
                top_level
                and normalized
                in {"source", "sources", "destination", "destinations", "dest", "dst", "target", "targets"}
            )
        )

    async def execute_tool_with_locks(
        self,
        tool: ToolDefinition,
        step: PlanStep,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
        self._ensure_authorized_paths(tool, args, context)
        lock_keys = self._write_lock_keys(tool, args)
        if not lock_keys:
            output = await self._execute_tool_body(tool, args, context, threaded=threaded, lock_keys=())
            return self._normalize_tool_output(tool, args, context, output)
        await self._await_pending_tool_completions(lock_keys, tool=tool)
        path_locks = self._locks_for_current_loop()
        locks = [path_locks.setdefault(key, asyncio.Lock()) for key in lock_keys]
        output = await self._execute_tool_under_locks(
            tool,
            args,
            context,
            locks,
            lock_keys=lock_keys,
            threaded=threaded,
        )
        return self._normalize_tool_output(tool, args, context, output)

    def _normalize_tool_output(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(output, dict):
            output = {"result": output}
        attach_dry_run_resource_state(output, tool, args, context)
        remember_read_states_for_tool(tool, args, output, context)
        return output

    async def _execute_tool_under_locks(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        locks: list[asyncio.Lock],
        *,
        lock_keys: list[str],
        threaded: bool = False,
    ) -> dict[str, Any]:
        if not locks:
            await self._await_pending_tool_completions(lock_keys, tool=tool)
            return await self._execute_tool_body(tool, args, context, threaded=threaded, lock_keys=lock_keys)
        async with locks[0]:
            return await self._execute_tool_under_locks(
                tool,
                args,
                context,
                locks[1:],
                lock_keys=lock_keys,
                threaded=threaded,
            )

    async def _execute_tool_body(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool,
        lock_keys: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        current_state = capture_tool_resource_state(tool, args, context)
        if current_state:
            context["_resource_state_before"] = current_state
        validate_write_preconditions(
            tool=tool,
            args=args,
            context=context,
            current_state=current_state,
            expected_approval_state=context.get("_expected_resource_state"),
        )
        # Tool implementations are synchronous (file IO, COM automation, OCR,
        # subprocess, HTTP). Always run them off the event loop thread so a
        # slow tool cannot freeze every concurrent request and WebSocket.
        timeout = self._tool_execution_timeout(context)
        try:
            worker = self._start_daemon_tool_worker(tool, args, context)
        except RuntimeError as exc:
            return {"error": str(exc), "resource_exhausted": True, "retry_after_pending_completion": True}
        try:
            return await asyncio.wait_for(asyncio.shield(worker.future), timeout=timeout)
        except TimeoutError:
            self._remember_pending_tool_completion(lock_keys, worker.future, tool=tool, reason="timeout")
            pending_completion = bool(lock_keys and not worker.future.done())
            error = f"{tool.name} timed out after {timeout:.0f}s"
            if pending_completion:
                error = (
                    f"{error}; execution is still finishing in the background and follow-up "
                    "calls for the same resource/tool will wait before running."
                )
            return {
                "error": error,
                "timed_out": True,
                "pending_completion": pending_completion,
                "retry_after_pending_completion": pending_completion,
            }
        except asyncio.CancelledError:
            self._abort_tool_worker(worker, tool=tool, context=context)
            raise

    async def _await_pending_tool_completions(
        self,
        lock_keys: list[str] | tuple[str, ...],
        *,
        tool: ToolDefinition,
    ) -> None:
        while True:
            pending = self._pending_tool_completions_for_current_loop()
            waits = {future for key in lock_keys if (future := pending.get(key)) is not None and not future.done()}
            if not waits:
                return
            record(
                "tool.waiting_for_pending_completion",
                "ToolRuntime",
                {"tool": tool.name, "lock_keys": lock_keys, "pending": len(waits)},
            )
            await asyncio.gather(*(asyncio.shield(future) for future in waits), return_exceptions=True)

    def _remember_pending_tool_completion(
        self,
        lock_keys: list[str] | tuple[str, ...],
        worker: asyncio.Future[Any],
        *,
        tool: ToolDefinition,
        reason: str,
    ) -> None:
        if not lock_keys or worker.done():
            return
        pending = self._pending_tool_completions_for_current_loop()
        keys = tuple(lock_keys)
        for key in keys:
            pending[key] = worker
        record(
            "tool.pending_completion_registered",
            "ToolRuntime",
            {"tool": tool.name, "reason": reason, "lock_keys": list(keys)},
        )

        def release_completion(done: asyncio.Future[Any]) -> None:
            try:
                done.result()
            except BaseException as exc:  # noqa: BLE001
                logger.debug("timed-out tool %s finished with %s", tool.name, type(exc).__name__, exc_info=True)
            for key in keys:
                if pending.get(key) is done:
                    pending.pop(key, None)

        worker.add_done_callback(release_completion)

    def _abort_tool_worker(
        self,
        worker: _ToolWorkerHandle,
        *,
        tool: ToolDefinition,
        context: dict[str, Any],
    ) -> None:
        worker.abandoned = True
        worker.abort_event.set()
        runtime = context.get("runtime")
        if runtime is not None and hasattr(runtime, "abort_requested"):
            runtime.abort_requested = True
        record(
            "tool.worker_abort_requested",
            "ToolRuntime",
            {"tool": tool.name, "future_done": worker.future.done()},
        )

    def _start_daemon_tool_worker(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> _ToolWorkerHandle:
        if not _TOOL_THREAD_SLOTS.acquire(blocking=False):
            raise RuntimeError(
                f"Tool worker capacity exhausted ({_MAX_DAEMON_TOOL_THREADS} in-flight sync tools); "
                "retry after pending tool executions finish."
            )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        abort_event = threading.Event()
        context["_tool_abort_event"] = abort_event
        handle = _ToolWorkerHandle(future=future, abort_event=abort_event)

        def complete_with_result(result: Any) -> None:
            if handle.abandoned or abort_event.is_set():
                return
            if not future.done():
                future.set_result(result)

        def complete_with_error(exc: BaseException) -> None:
            if handle.abandoned or abort_event.is_set():
                return
            if not future.done():
                future.set_exception(exc)

        def finish(callback: Any, *callback_args: Any) -> None:
            try:
                loop.call_soon_threadsafe(callback, *callback_args)
            except RuntimeError:
                logger.debug("tool worker %s finished after its event loop closed", tool.name, exc_info=True)

        def run_tool() -> None:
            try:
                if handle.abandoned or abort_event.is_set():
                    return
                result = tool.execute(args, context)
                if handle.abandoned or abort_event.is_set():
                    record(
                        "tool.worker_result_discarded",
                        "ToolRuntime",
                        {"tool": tool.name},
                    )
                    return
                finish(complete_with_result, result)
            except BaseException as exc:  # noqa: BLE001 - propagate tool crashes to the awaiting task.
                if handle.abandoned or abort_event.is_set():
                    return
                finish(complete_with_error, exc)
            finally:
                _TOOL_THREAD_SLOTS.release()

        thread = threading.Thread(target=run_tool, name=f"tool-{tool.name}", daemon=True)
        thread.start()
        return handle

    def _tool_execution_timeout(self, context: dict[str, Any]) -> float:
        settings = context.get("settings")
        if settings is not None:
            configured = getattr(settings, "tool_timeout_seconds", None)
            if configured is not None:
                return max(1.0, float(configured))
        configured = getattr(get_effective_settings(), "tool_timeout_seconds", None)
        if configured is not None:
            return max(1.0, float(configured))
        return _DEFAULT_TOOL_TIMEOUT_SECONDS

    def _write_lock_keys(self, tool: ToolDefinition, args: dict[str, Any]) -> list[str]:
        if not self._needs_completion_barrier(tool, args):
            return []

        keys: set[str] = set()
        if tool.concurrency_key:
            keys.add(f"tool:{tool.concurrency_key.casefold()}")
        for value in self._candidate_write_paths(args):
            path = self._normalize_lock_path(value)
            if not path:
                continue
            keys.add(path)
            parent = str(Path(path).parent)
            if parent and parent != path:
                keys.add(parent)
        if not keys and self._is_write_tool(tool):
            keys.add(f"tool:{tool.name.casefold()}")
        if not keys:
            keys.add(f"tool:{tool.name.casefold()}")
        return sorted(keys)

    def _needs_completion_barrier(self, tool: ToolDefinition, args: dict[str, Any]) -> bool:
        if tool.concurrency_key or self._is_write_tool(tool):
            return True
        return not tool.is_concurrency_safe(args)

    def _is_write_tool(self, tool: ToolDefinition) -> bool:
        risk = getattr(tool, "risk_level", None)
        risk_value = getattr(risk, "value", str(risk or ""))
        if risk and risk_value.startswith(("R2", "R3")):
            return True
        if getattr(tool, "supports_dry_run", False):
            return True
        name = getattr(tool, "name", "")
        return name in BROWSER_WRITE_TOOLS or any(
            token in name
            for token in (".copy", ".move", ".rename", ".trash", ".write", ".create", ".delete", ".uninstall")
        )

    def _candidate_write_paths(self, args: dict[str, Any]) -> list[Any]:
        result: list[Any] = []
        for key in (
            "path",
            "source",
            "destination",
            "target",
            "target_path",
            "target_folder",
            "folder",
            "directory",
            "output_path",
        ):
            value = args.get(key)
            if value:
                result.append(value)
        return result

    def _normalize_lock_path(self, value: Any) -> str:
        if not isinstance(value, str | Path):
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            return str(Path(text).expanduser().resolve(strict=False)).casefold()
        except OSError:
            return text.casefold()

    def _locks_for_current_loop(self) -> dict[str, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        locks = _SHARED_PATH_LOCKS.get(loop)
        if locks is None:
            locks = {}
            _SHARED_PATH_LOCKS[loop] = locks
        return locks

    def _pending_tool_completions_for_current_loop(self) -> dict[str, asyncio.Future[Any]]:
        loop = asyncio.get_running_loop()
        pending = _SHARED_PENDING_TOOL_COMPLETIONS.get(loop)
        if pending is None:
            pending = {}
            _SHARED_PENDING_TOOL_COMPLETIONS[loop] = pending
        return pending
