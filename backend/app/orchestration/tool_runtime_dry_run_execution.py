from __future__ import annotations

from typing import Any

from app.core.schemas import PlanStep, Task, ToolResult
from app.orchestration.automation_runtime_guard import (
    AutomationExecutionDenied,
    authorize_automation_execution,
)
from app.orchestration.runtime_context import TaskRuntimeContext
from app.tools.schemas import ToolDefinition


async def build_approval_dry_run_preview_result(
    runtime_host: Any,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
    runtime: TaskRuntimeContext,
    *,
    threaded_tools: bool,
) -> ToolResult:
    orchestrator = runtime_host.orchestrator
    dry_run_args = {**step.args, "dry_run": True}
    try:
        authorization = authorize_automation_execution(
            task=task,
            step=step,
            tool=tool,
            runtime=runtime,
            args=dry_run_args,
            threaded_tools=threaded_tools,
        )
    except AutomationExecutionDenied as exc:
        runtime.abort_requested = True
        return ToolResult(
            tool_call_id=f"{step.id}_automation_guard_dry_run",
            ok=False,
            error=exc.reason,
            observation=f"{step.tool_name} dry-run was blocked by the automation execution guard.",
        )
    if authorization is not None:
        runtime.extra_context["automation_action_fingerprint"] = authorization.action_fingerprint
        runtime.extra_context["automation_intent_capsule_id"] = authorization.capsule_id
        runtime.extra_context["automation_budget_version"] = authorization.budget_version
        runtime.extra_context["automation_budget_soft_exceeded"] = authorization.soft_exceeded
    before_frame = await orchestrator._capture_step_frame(task, step, "before_dry_run")
    dry_run_context = runtime.tool_context()
    dry_run_context.update({"task_id": task.id, "step_id": step.id})
    try:
        preview = await runtime_host.execute_tool_with_locks(
            tool,
            step,
            dry_run_args,
            dry_run_context,
            threaded=threaded_tools,
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
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

    return ToolResult(
        tool_call_id=f"{step.id}_dry_run",
        ok=not bool(preview.get("error")),
        output=preview,
        error=str(preview.get("error", "")),
        observation=f"{step.tool_name} dry-run preview generated.",
    )
