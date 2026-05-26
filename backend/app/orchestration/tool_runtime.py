from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from weakref import WeakKeyDictionary

from app.core import db
from app.core.audit import record
from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
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
from app.orchestration.result_budget import apply_result_budget
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    redacted_preview,
    settings_fingerprint,
)
from app.policy.permissions import PermissionStore
from app.policy.policy_engine import BROWSER_WRITE_TOOLS
from app.policy.redaction import redact_value
from app.policy.risk import SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.tools.schemas import ToolDefinition


@dataclass(slots=True)
class RuntimeExecutionResult:
    kind: str
    result: ToolResult | None = None


_SHARED_PATH_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()
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

        if step.tool_name in BROWSER_WRITE_TOOLS:
            browser_review = orchestrator.safety.review_browser_write(task.id, step.id, step.tool_name, step.args)
            if browser_review and browser_review.verdict == SafetyVerdict.DENY:
                set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
                orchestrator.bus.publish_text(
                    task.id,
                    orchestrator.name,
                    f"Denied browser write {step.tool_name}: {'; '.join(browser_review.reasons)}",
                    step_id=step.id,
                )
                orchestrator._supervise_new_agent_messages(task.id, "browser_write_denied")
                return RuntimeExecutionResult("step_denied")

        review_context = runtime.tool_context()
        review_context.update({"task_id": task.id, "step_id": step.id})
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
            orchestrator.bus.publish_text(task.id, orchestrator.name, f"Denied step: {step.description}", step_id=step.id)
            orchestrator._supervise_new_agent_messages(task.id, "tool_call_denied")
            return RuntimeExecutionResult("step_denied")

        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            if not tool.supports_dry_run:
                return self._deny_approval_without_dry_run(task, step, tool)
            return await self._prepare_approval(
                task,
                step,
                tool,
                runtime,
                review.user_confirmation_message,
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
        call = ToolCall(
            task_id=task.id,
            step_id=step.id,
            tool_name=step.tool_name,
            args=args,
            risk_level=tool.risk_level,
            dry_run=False,
        )
        db.upsert_model("tool_calls", call)
        safe_args = self._redact_tool_args(args, tool)
        safe_call_payload = call.model_copy(update={"args": safe_args}).model_dump()
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
        stage = "approved_tool_call_proposed" if approval_id else "tool_call_proposed"
        if not orchestrator._supervise_new_agent_messages(task.id, stage):
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task before executing a tool call.",
            )
            return RuntimeExecutionResult("fatal_denied")

        before_phase = "before_approved" if approval_id else "before"
        after_phase = "after_approved" if approval_id else "after"
        before_frame = await orchestrator._capture_step_frame(task, step, before_phase)
        tool_context = runtime.tool_context()
        self._publish_tool_progress(task, step, tool, call.id, "started", detail=f"Starting {step.tool_name}.")
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
                error=str(output.get("error", "")),
                changed_paths=list(output.get("changed_paths", [])),
                rollback_info=dict(output.get("rollback_info", {})),
                observation=self._observation(step, tool, output),
            )
        except Exception as exc:  # noqa: BLE001
            self._publish_tool_progress(
                task,
                step,
                tool,
                call.id,
                "failed",
                detail=f"{step.tool_name} failed.",
                payload={"error": str(exc)},
            )
            result = ToolResult(tool_call_id=call.id, ok=False, error=str(exc), observation=f"{step.tool_name} failed.")
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

        result = apply_result_budget(
            result,
            tool_name=step.tool_name,
            max_result_size=tool.max_result_size,
            runtime=runtime,
        )
        db.upsert_model("tool_results", result)
        post_tool_review = orchestrator.safety.review_tool_result(task.id, step.id, step.tool_name, result, tool.risk_level)
        if post_tool_review.verdict == SafetyVerdict.DENY:
            set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            return RuntimeExecutionResult("fatal_denied", result)

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
        if isinstance(value, (list, tuple, set, frozenset)):
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
        try:
            preview = await self.execute_tool_with_locks(
                tool,
                step,
                {**step.args, "dry_run": True},
                runtime.tool_context(),
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
            settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
            permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
            tool_version=getattr(tool, "tool_version", "1"),
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

    def _deny_approval_without_dry_run(self, task: Task, step: PlanStep, tool: ToolDefinition) -> RuntimeExecutionResult:
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

    def _validate_input(self, tool: ToolDefinition, args: dict[str, Any], runtime: TaskRuntimeContext) -> str:
        if not tool.validate_input:
            return ""
        try:
            tool.validate_input(args, runtime.tool_context())
        except Exception as exc:  # noqa: BLE001
            record("tool.validation_failed", "ToolRuntime", {"tool": tool.name, "error": str(exc)}, task_id=runtime.task.id)
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
            record("tool.permission_failed", "ToolRuntime", {"tool": tool.name, "error": str(exc)}, task_id=runtime.task.id)
            return str(exc)
        return "" if allowed else f"Tool permission policy denied {tool.name}."

    def _observation(self, step: PlanStep, tool: ToolDefinition, output: dict[str, Any]) -> str:
        if tool.result_summary:
            try:
                summary = tool.result_summary(output)
                if summary:
                    return summary
            except Exception:
                pass
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
        if tool.name == "file.trash" and not allowed_directories:
            return
        for arg_name, value in self._candidate_authorized_paths(args):
            try:
                resolve_authorized(value, allowed_directories)
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
                elif isinstance(child, (dict, list, tuple, set)):
                    self._collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)
            return
        if isinstance(value, (list, tuple, set)):
            for index, child in enumerate(value):
                child_name = f"{arg_name}[{index}]" if arg_name else f"[{index}]"
                self._collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)

    def _append_authorized_path_values(
        self,
        value: Any,
        arg_name: str,
        candidates: list[tuple[str, str | Path]],
    ) -> None:
        if isinstance(value, (str, Path)) and str(value).strip():
            candidates.append((arg_name, value))
            return
        if isinstance(value, (list, tuple, set)):
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
            or (top_level and normalized in {"source", "sources", "destination", "destinations", "dest", "dst", "target", "targets"})
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
            if threaded:
                return await asyncio.to_thread(tool.execute, args, context)
            return tool.execute(args, context)
        path_locks = self._locks_for_current_loop()
        locks = [path_locks.setdefault(key, asyncio.Lock()) for key in lock_keys]
        return await self._execute_tool_under_locks(tool, args, context, locks, threaded=threaded)

    async def _execute_tool_under_locks(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        locks: list[asyncio.Lock],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
        if not locks:
            if threaded:
                return await asyncio.to_thread(tool.execute, args, context)
            return tool.execute(args, context)
        async with locks[0]:
            return await self._execute_tool_under_locks(tool, args, context, locks[1:], threaded=threaded)

    def _write_lock_keys(self, tool: ToolDefinition, args: dict[str, Any]) -> list[str]:
        if not self._is_write_tool(tool) and not tool.concurrency_key:
            return []
        if args.get("dry_run") is True:
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
        return sorted(keys)

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
        if not isinstance(value, (str, Path)):
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
