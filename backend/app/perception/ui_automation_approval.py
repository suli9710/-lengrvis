from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview, now_iso
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.effective_risk_binding import (
    approval_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
    risk_revalidation_context,
)
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel, SafetyVerdict

ApprovalBindingValidator = Callable[..., str]
ApprovalGate = Callable[[str, str | None, str, dict[str, Any]], str]


def review_action(
    policy_engine: Any,
    approval_gate: ApprovalGate,
    task_id: str,
    step_id: str | None,
    tool_name: str,
    args: dict[str, Any],
    risk_level: RiskLevel,
) -> SafetyReview:
    if args.get("approved") and args.get("approval_id"):
        review = policy_engine.review_tool_call(task_id or "ui_automation", step_id, tool_name, args, risk_level)
        if review.verdict == SafetyVerdict.DENY:
            return review
        approval_error = approval_gate(task_id, step_id, tool_name, args)
        if approval_error:
            return SafetyReview(
                task_id=task_id or "ui_automation",
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=risk_level,
                reasons=[approval_error],
            )
        return SafetyReview(
            task_id=task_id or "ui_automation",
            step_id=step_id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=review.risk_level,
            declared_risk_level=review.declared_risk_level or risk_level,
            reasons=["Approved UIAutomation action may proceed."],
        )
    return policy_engine.review_tool_call(task_id or "ui_automation", step_id, tool_name, args, risk_level)


def approval_gate_error(
    *,
    policy_engine: Any,
    approval_context: dict[str, Any],
    binding_validator: ApprovalBindingValidator,
    task_id: str,
    step_id: str | None,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    if execution_is_marked_approved(approval_context):
        return ""
    approval_id = str(args.get("approval_id") or "").strip()
    if not approval_id:
        return "UIAutomation live execution requires a valid approved approval_id."
    try:
        data = db.fetch_one("approvals", approval_id)
    except (sqlite3.Error, db.SensitiveRecordIntegrityError) as exc:
        return f"UIAutomation approval storage lookup failed: {exc}"
    if not data:
        return "UIAutomation approval id was not found in the approval database."
    try:
        approval = Approval.model_validate(data)
    except ValidationError as exc:
        return f"UIAutomation approval record is invalid: {exc}"
    binding_error = binding_validator(
        approval,
        tool_name,
        args,
        context=approval_context,
        settings=getattr(policy_engine, "settings", None),
        now_provider=getattr(policy_engine, "now_provider", None),
        task_id=task_id,
        step_id=step_id,
        allow_consumed=False,
    )
    if binding_error:
        db.expire_approval_if_unconsumed(approval.id, now_iso(), binding_error)
        return binding_error
    try:
        claimed = db.claim_approval_for_execution(approval.id, now_iso())
    except (sqlite3.Error, db.SensitiveRecordIntegrityError) as exc:
        return f"UIAutomation approval claim failed: {exc}"
    if not claimed:
        return "UIAutomation approval has already been consumed or is no longer approved."
    try:
        claimed_approval = Approval.model_validate(claimed)
    except ValidationError as exc:
        return f"UIAutomation claimed approval record is invalid: {exc}"
    claimed_error = binding_validator(
        claimed_approval,
        tool_name,
        args,
        context=approval_context,
        settings=getattr(policy_engine, "settings", None),
        now_provider=getattr(policy_engine, "now_provider", None),
        task_id=task_id,
        step_id=step_id,
        allow_consumed=True,
    )
    if not claimed_error:
        approval_context["effective_risk_binding"] = dict(approval_risk_binding(claimed_approval) or {})
    return claimed_error


def approval_binding_error(
    approval: Approval,
    tool_name: str,
    args: dict[str, Any],
    *,
    context: dict[str, Any] | None,
    settings: Any,
    now_provider: Callable[[], Any] | None,
    task_id: str,
    step_id: str | None,
    allow_consumed: bool,
    approval_args: Callable[[dict[str, Any]], dict[str, Any]],
    tool_definition: Callable[[str], Any],
) -> str:
    if approval.approval_type != "tool_call":
        return "UIAutomation approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"UIAutomation approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "UIAutomation approval has already been consumed."
    tool = tool_definition(tool_name)
    if approval.tool_name != tool_name:
        return "UIAutomation approval tool name does not match this action."
    if task_id and approval.task_id != task_id:
        return "UIAutomation approval task does not match this action."
    if step_id and approval.step_id != step_id:
        return "UIAutomation approval step does not match this action."
    missing = [
        key
        for key, value in {
            "tool_name": approval.tool_name,
            "args_binding_hmac": approval.args_binding_hmac,
            "preview_hmac": approval.preview_hmac,
            "settings_fingerprint": approval.settings_fingerprint,
            "permission_policy_version": approval.permission_policy_version,
            "tool_version": approval.tool_version,
        }.items()
        if not value
    ]
    if missing:
        return f"UIAutomation approval lacks binding metadata: {', '.join(missing)}."
    runtime_context = context or {}
    runtime_settings = runtime_context.get("settings") or settings
    from app.policy.policy_engine import PolicyEngine

    current_review = PolicyEngine(settings=runtime_settings, now_provider=now_provider).review_tool_call(
        approval.task_id,
        approval.step_id,
        tool_name,
        approval_args(args),
        tool.risk_level,
        context=risk_revalidation_context(runtime_context, task_id=approval.task_id),
        tool_definition=tool,
    )
    risk_binding = approval_risk_binding(approval)
    risk_error = effective_risk_binding_error(
        risk_binding,
        current_declared_risk=current_review.declared_risk_level or tool.risk_level,
        approval_risk_level=approval.risk_level,
    )
    if risk_error:
        return risk_error
    refreshed_error = refreshed_effective_risk_error(risk_binding, current_review)
    if refreshed_error:
        return refreshed_error
    if approval.tool_version != getattr(tool, "tool_version", "1"):
        return "UIAutomation approval tool version does not match this tool."
    expected_args = args_binding_hmac(
        tool_name,
        approval_args(args),
        task_id=approval.task_id,
        step_id=approval.step_id,
    )
    if not hmac.compare_digest(str(approval.args_binding_hmac or ""), str(expected_args or "")):
        return "UIAutomation approval arguments do not match this action."
    expected_preview = preview_hmac(approval.diff_preview)
    if not hmac.compare_digest(str(approval.preview_hmac or ""), str(expected_preview or "")):
        return "UIAutomation approval preview was modified after review."
    allowed_directories = list(
        runtime_context.get("allowed_directories") or getattr(runtime_settings, "allowed_directories", []) or []
    )
    expected_settings = settings_fingerprint(runtime_settings, allowed_directories=allowed_directories)
    if not hmac.compare_digest(str(approval.settings_fingerprint or ""), str(expected_settings or "")):
        return "UIAutomation runtime settings changed after approval preview."
    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not hmac.compare_digest(str(approval.permission_policy_version or ""), str(expected_policy or "")):
        return "UIAutomation permission policy changed after approval preview."
    return ""


def approval_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key not in {"approved", "approval_id", "dry_run"}}


def tool_definition(tool_name: str) -> Any:
    from app.tools.registry import register_all_tools, registry

    if not registry.list():
        register_all_tools()
    return registry.get(tool_name)
