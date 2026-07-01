from __future__ import annotations

from typing import Any

from app.core.audit import record
from app.core.schemas import MessageType, OpenAIMessageRole, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.step_phase import set_step_status
from app.orchestration.tool_runtime_support import (
    RuntimeExecutionResult,
    _message_safe_text,
    _message_safe_tool_result,
    _withheld_result_stub,
    _withheld_tool_result,
)
from app.tools.schemas import ToolDefinition


def fail_dry_run_preview(
    orchestrator: Any, task: Task, step: PlanStep, preview_result: ToolResult
) -> RuntimeExecutionResult:
    safe_summary = _message_safe_text(orchestrator._friendly_tool_error(preview_result.error))
    set_step_status(step, StepStatus.FAILED, actor="ToolRuntime")
    orchestrator._set_status(
        task,
        TaskStatus.FAILED,
        final_summary=safe_summary,
    )
    orchestrator.bus.publish_text(
        task.id,
        step.agent_name,
        safe_summary,
        role=OpenAIMessageRole.TOOL,
        message_type=MessageType.OBSERVATION,
        step_id=step.id,
        structured_payload=_message_safe_tool_result(preview_result).model_dump(),
    )
    return RuntimeExecutionResult("fatal_failed", preview_result)


def deny_dry_run_contract(
    orchestrator: Any,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
    preview_result: ToolResult,
    preview_contract_error: str,
) -> RuntimeExecutionResult:
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
    preview_result = _withheld_result_stub(
        preview_result,
        reason="Tool dry-run preview did not satisfy the approval safety contract.",
    )
    return RuntimeExecutionResult("fatal_denied", preview_result)


def deny_post_preview_review(
    orchestrator: Any,
    task: Task,
    step: PlanStep,
    preview_result: ToolResult,
    post_preview_review: Any,
    runtime: TaskRuntimeContext,
) -> RuntimeExecutionResult:
    preview_result = _withheld_tool_result(preview_result, post_preview_review, runtime)
    set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
    orchestrator._set_status(task, TaskStatus.DENIED, final_summary=post_preview_review.safe_alternative)
    return RuntimeExecutionResult("fatal_denied", preview_result)


def deny_approval_without_dry_run(
    orchestrator: Any,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
) -> RuntimeExecutionResult:
    set_step_status(step, StepStatus.DENIED, actor="ToolRuntime")
    orchestrator._set_status(
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
