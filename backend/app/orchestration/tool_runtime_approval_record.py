from __future__ import annotations

from app.core.schemas import Approval, PlanStep, Task
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_runtime_approval import (
    approval_boundary_facts,
    approval_dry_run_summary,
    runtime_control_fields,
)
from app.policy.approval_binding import (
    args_binding_hmac,
    binding_preview,
    permission_policy_version,
    preview_hmac,
    settings_fingerprint,
)
from app.policy.permission_modes import permission_mode_from_context
from app.policy.permissions import PermissionStore
from app.tools.schemas import ToolDefinition


def build_tool_approval_record(
    *,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
    runtime: TaskRuntimeContext,
    confirmation_message: str,
    preview: dict,
    risk_binding: dict[str, str],
) -> Approval:
    safe_preview = binding_preview(preview)
    return Approval(
        task_id=task.id,
        step_id=step.id,
        message=confirmation_message or step.description,
        diff_preview=safe_preview,
        tool_name=step.tool_name,
        risk_level=risk_binding["effective_risk_level"],
        args_binding_hmac=args_binding_hmac(step.tool_name, step.args, task_id=task.id, step_id=step.id),
        preview_hmac=preview_hmac(safe_preview),
        settings_fingerprint=settings_fingerprint(runtime.settings, allowed_directories=runtime.allowed_directories),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        policy_mode=permission_mode_from_context(runtime.tool_context(), runtime.settings),
        tool_version=getattr(tool, "tool_version", "1"),
        tool_trust_tier=str(getattr(tool, "trust_tier", "") or ""),
        tool_effects=list(getattr(tool, "effects", []) or []),
        resource_kinds=list(getattr(tool, "resource_kinds", []) or []),
        dry_run_summary=approval_dry_run_summary(tool, preview),
        model_action=dict(getattr(step, "model_action", {}) or {}),
        runtime_control_fields=runtime_control_fields(),
        engineering_boundary={
            **approval_boundary_facts(step, tool, runtime, safe_preview),
            "risk_provenance": dict(risk_binding),
        },
    )
