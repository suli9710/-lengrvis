from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agents.base import BaseAgent
from app.core.paths import is_sensitive_path, is_system_path, normalize_path
from app.core.schemas import SafetyReview
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.cleanup_planner_service import is_direct_delete_allowed

CLEANUP_TOOLS = {"file.cleanup_execute", "file.cleanup_rollback"}
SENSITIVE_PATH_TERMS = {
    ".ssh",
    "api_key",
    "apikey",
    "cookie",
    "credential",
    "credentials",
    "id_rsa",
    "key",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
}
WINDOWS_SYSTEM_PATH_MARKERS = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
)


class CleanupReviewAgent(BaseAgent):
    name = "CleanupReviewAgent"
    domain_summary = (
        "Deterministically reviews cleanup plans and executions for direct-delete, approval, and sensitive path safety."
    )
    prompt_file = "safety_review_agent.md"

    def review_tool_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel | None = None,
        context: dict[str, Any] | None = None,
        tool_definition: Any | None = None,  # noqa: ARG002 - kept ToolRuntime-compatible.
    ) -> SafetyReview | None:
        if tool_name not in CLEANUP_TOOLS:
            return None
        if tool_name == "file.cleanup_rollback":
            if args.get("dry_run", True) is not False:
                return SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="cleanup_rollback",
                    verdict=SafetyVerdict.ALLOW,
                    risk_level=risk_level or RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
                    reasons=["Cleanup rollback preview is dry-run only."],
                )
            return self._approval_review(task_id, step_id, "cleanup_rollback", risk_level)

        reasons = self._blocking_reasons(args)
        if reasons:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="cleanup_execute",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=reasons,
                safe_alternative=(
                    "Regenerate the cleanup plan and execute only whitelisted direct-delete items or "
                    "approved recycle-bin items."
                ),
            )

        live = args.get("dry_run") is False
        if live and _has_trash_action_hint(args) and (not args.get("approved") or not args.get("approval_id")):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="cleanup_execute",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=["Recycle-bin cleanup execution cannot bypass approved=true and approval_id."],
                safe_alternative=(
                    "Request user approval for the exact cleanup plan before executing recycle-bin actions."
                ),
            )
        if live and _has_trash_action_hint(args):
            return self._approval_review(task_id, step_id, "cleanup_execute", risk_level)
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="cleanup_execute",
            verdict=SafetyVerdict.ALLOW,
            risk_level=risk_level or RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            reasons=["Cleanup execution shape passed deterministic cleanup review."],
        )

    def review_plan(self, plan: Any) -> SafetyReview:
        reasons: list[str] = []
        task_id = str(getattr(plan, "task_id", "") or "")
        for step in getattr(plan, "steps", []) or []:
            if getattr(step, "tool_name", "") != "file.cleanup_execute":
                continue
            reasons.extend(self._blocking_reasons(dict(getattr(step, "args", {}) or {})))
        if reasons:
            return SafetyReview(
                task_id=task_id,
                target_type="cleanup_plan",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=reasons,
                safe_alternative=(
                    "Revise cleanup steps to use file.cleanup_plan followed by approved file.cleanup_execute."
                ),
            )
        return SafetyReview(
            task_id=task_id,
            target_type="cleanup_plan",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Cleanup plan contains no deterministic cleanup safety blockers."],
        )

    def _blocking_reasons(self, args: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if _approval_bypass_requested(args):
            reasons.append("Cleanup execution attempts to bypass approval controls.")
        direct_paths = _direct_delete_path_hints(args)
        for raw_path in direct_paths:
            path = normalize_path(raw_path)
            if _sensitive_or_system(path):
                reasons.append(f"Cleanup direct-delete target is sensitive or system-owned: {path}")
            elif not is_direct_delete_allowed(path):
                reasons.append(f"Cleanup direct-delete target is outside the direct-delete whitelist: {path}")
        for raw_path in _all_path_hints(args):
            path = normalize_path(raw_path)
            if _sensitive_or_system(path):
                reasons.append(f"Cleanup target touches sensitive or system path: {path}")
        return sorted(set(reasons))

    def _approval_review(
        self,
        task_id: str,
        step_id: str | None,
        target_type: str,
        risk_level: RiskLevel | None,
    ) -> SafetyReview:
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type=target_type,
            verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            risk_level=risk_level or RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            reasons=["Cleanup recycle-bin or rollback execution requires explicit approval."],
            user_confirmation_message="Approve this cleanup execution after reviewing the exact cleanup plan?",
        )


def _approval_bypass_requested(args: dict[str, Any]) -> bool:
    text = str(args).casefold()
    bypass_terms = ("bypass", "skip approval", "auto approve", "auto-approve")
    return any(term in text for term in bypass_terms)


def _has_trash_action_hint(args: dict[str, Any]) -> bool:
    if str(args.get("action") or "").casefold() == "trash_with_prompt":
        return True
    for item in args.get("items") or args.get("selected_items") or []:
        if isinstance(item, dict) and str(item.get("action") or "").casefold() == "trash_with_prompt":
            return True
    return False


def _direct_delete_path_hints(args: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if str(args.get("action") or "").casefold() == "delete_direct":
        for key in ("path", "paths"):
            paths.extend(_string_values(args.get(key)))
    for item in args.get("items") or args.get("selected_items") or []:
        if isinstance(item, dict) and str(item.get("action") or "").casefold() == "delete_direct":
            paths.extend(_string_values(item.get("path")))
    return paths


def _all_path_hints(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in {"path", "paths", "root", "roots", "target", "targets"} or "path" in normalized:
                paths.extend(_string_values(item))
            elif isinstance(item, dict | list | tuple | set):
                paths.extend(_all_path_hints(item))
        return paths
    if isinstance(value, list | tuple | set):
        for item in value:
            paths.extend(_all_path_hints(item))
    return paths


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str | Path) and str(value).strip():
        return [str(value)]
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            result.extend(_string_values(item))
        return result
    return []


def _sensitive_or_system(path: Path) -> bool:
    normalized = str(path).replace("\\", "/").casefold()
    return (
        is_system_path(path)
        or is_sensitive_path(path)
        or any(marker in normalized for marker in WINDOWS_SYSTEM_PATH_MARKERS)
        or any(term in normalized for term in SENSITIVE_PATH_TERMS)
    )
