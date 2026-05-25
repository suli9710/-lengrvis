from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from app.core import db
from app.core.audit import record
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
from app.policy.policy_engine import BROWSER_WRITE_TOOLS
from app.policy.risk import SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.tools.schemas import ToolDefinition


@dataclass(slots=True)
class RuntimeExecutionResult:
    kind: str
    result: ToolResult | None = None


_SHARED_PATH_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


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
            step.status = StepStatus.FAILED
            result = ToolResult(
                tool_call_id=f"{step.id}_validation",
                ok=False,
                error=validation_error,
                observation=f"{step.tool_name} input validation failed.",
            )
            return RuntimeExecutionResult("fatal_failed", result)

        permission_error = self._check_permission(tool, step.args, runtime)
        if permission_error:
            step.status = StepStatus.DENIED
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
                step.status = StepStatus.DENIED
                orchestrator.bus.publish_text(
                    task.id,
                    orchestrator.name,
                    f"Denied browser write {step.tool_name}: {'; '.join(browser_review.reasons)}",
                    step_id=step.id,
                )
                orchestrator._supervise_new_agent_messages(task.id, "browser_write_denied")
                return RuntimeExecutionResult("step_denied")

        review = orchestrator.safety.review_tool_call(task.id, step.id, step.tool_name, step.args, tool.risk_level)
        if review.verdict == SafetyVerdict.DENY:
            step.status = StepStatus.DENIED
            orchestrator.bus.publish_text(task.id, orchestrator.name, f"Denied step: {step.description}", step_id=step.id)
            orchestrator._supervise_new_agent_messages(task.id, "tool_call_denied")
            return RuntimeExecutionResult("step_denied")

        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return await self._prepare_approval(
                task,
                step,
                tool,
                runtime,
                review.user_confirmation_message,
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
                        "arguments": args,
                    },
                }
            ],
            structured_payload=call.model_dump(),
            metadata={"approval_id": approval_id, "approved_by_user": bool(approval_id)} if approval_id else None,
        )
        stage = "approved_tool_call_proposed" if approval_id else "tool_call_proposed"
        if not orchestrator._supervise_new_agent_messages(task.id, stage):
            step.status = StepStatus.DENIED
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task before executing a tool call.",
            )
            return RuntimeExecutionResult("fatal_denied")

        before_phase = "before_approved" if approval_id else "before"
        after_phase = "after_approved" if approval_id else "after"
        before_frame = await orchestrator._capture_step_frame(task, step, before_phase)
        try:
            step.status = StepStatus.RUNNING
            orchestrator._set_status(task, TaskStatus.EXECUTING_TOOL)
            output = await self.execute_tool_with_locks(
                tool,
                step,
                args,
                runtime.tool_context(),
                threaded=threaded_tools,
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
            step.status = StepStatus.DENIED
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
            step.status = StepStatus.DENIED
            orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after observing tool output.",
            )
            return RuntimeExecutionResult("fatal_denied", result)

        step.status = StepStatus.SUCCEEDED if result.ok else StepStatus.FAILED
        await orchestrator._reflect_on_step(task, step, result)
        return RuntimeExecutionResult("succeeded" if result.ok else "failed", result)

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
            step.status = StepStatus.FAILED
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

        post_preview_review = orchestrator.safety.review_tool_result(
            task.id,
            step.id,
            step.tool_name,
            preview_result,
            tool.risk_level,
        )
        if post_preview_review.verdict == SafetyVerdict.DENY:
            step.status = StepStatus.DENIED
            orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_preview_review.safe_alternative)
            return RuntimeExecutionResult("fatal_denied", preview_result)

        approval = Approval(
            task_id=task.id,
            step_id=step.id,
            message=confirmation_message or step.description,
            diff_preview=preview,
        )
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)
        step.status = StepStatus.WAITING_USER_APPROVAL
        orchestrator.bus.publish_text(
            task.id,
            "HumanGateAgent",
            "Waiting for user approval before executing modifying operation.",
            message_type=MessageType.REVIEW,
            step_id=step.id,
        )
        orchestrator._supervise_new_agent_messages(task.id, "approval_gate")
        return RuntimeExecutionResult("waiting_user_approval", preview_result)

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

    async def execute_tool_with_locks(
        self,
        tool: ToolDefinition,
        step: PlanStep,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
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
