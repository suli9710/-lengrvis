from __future__ import annotations

from typing import Any

from app.core.schemas import PlanStep
from app.orchestration.runtime_context import TaskRuntimeContext
from app.policy.permission_modes import permission_mode_from_context, trusted_reversible_edit_allowed
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def dry_run_preview_contract_error(preview: dict[str, Any]) -> str:
    if preview.get("dry_run") is not True:
        return "Dry-run preview must declare dry_run=True."
    if preview.get("changed_paths"):
        return "Dry-run preview must not report changed_paths."
    return ""


def requires_runtime_approval(step: PlanStep, tool: ToolDefinition, runtime: TaskRuntimeContext) -> bool:
    mode = permission_mode_from_context(runtime.tool_context(), runtime.settings)
    if mode in {"trusted_edits", "auto_review"} and trusted_reversible_edit_allowed(tool, step.args):
        return False
    if bool(getattr(step, "requires_approval", False)):
        return True
    return tool.risk_level in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}


def auto_approved_args(
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


def approval_dry_run_summary(tool: ToolDefinition, preview: dict[str, Any]) -> str:
    if preview.get("error"):
        return f"{tool.name} dry-run failed: {preview.get('error')}"
    changed = (
        preview.get("would_change") or preview.get("changes") or preview.get("items") or preview.get("changed_paths")
    )
    if isinstance(changed, list):
        return f"{tool.name} dry-run preview contains {len(changed)} item(s)."
    if isinstance(changed, dict):
        return f"{tool.name} dry-run preview contains {len(changed)} field(s)."
    return f"{tool.name} dry-run preview generated."


def runtime_control_fields() -> dict[str, Any]:
    return {
        "approved": "runtime_only",
        "approval_id": "runtime_only",
        "policy_decision": "runtime_only",
        "risk_level": "registry_policy_only",
        "trust_tier": "registry_only",
    }


def approval_boundary_facts(
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
        "runtime_fields": runtime_control_fields(),
        "binding": {
            "args_bound": True,
            "preview_bound": True,
            "settings_bound": True,
            "permission_policy_bound": True,
        },
        "dry_run": {
            "summary": approval_dry_run_summary(tool, safe_preview),
            "preview_keys": sorted(str(key) for key in safe_preview.keys() if not str(key).startswith("_"))[:20],
        },
    }
