from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any

from app.config import AppSettings
from app.core.schemas import AgentMessage, Plan, PlanStep, SafetyReview, ToolResult
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, settings_fingerprint, short_digest
from app.policy.decision_cache import tool_decision_cache
from app.policy.dynamic_risk import DynamicRiskAssessor
from app.policy.permissions import PermissionPolicy, PermissionStore
from app.policy.privacy import can_use_browser_writes
from app.policy.risk import RiskLevel, SafetyVerdict, max_risk


FORBIDDEN_TERMS = {
    "password",
    "密码",
    "口令",
    "cookie",
    "token",
    "credential",
    "credentials",
    "private key",
    "密钥",
    "pay",
    "payment",
    "支付",
    "付款",
    "order",
    "下单",
    "bypass",
    "disable security",
}


SENSITIVE_FIELD_NAMES = {
    "password",
    "pwd",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "cvv",
    "cvc",
    "card_number",
    "cardnumber",
    "otp",
    "2fa",
    "passcode",
    "payment",
    "pay",
    "order",
    "ssn",
    "口令",
    "密码",
}


BROWSER_WRITE_TOOLS = {
    "browser.click_element",
    "browser.fill_form",
    "browser.submit_form",
}

FAST_PATH_ALLOWED_EFFECTS = {"read", "observe", "list", "open", "launch", "reveal", "navigate", "search", "inspect"}
FAST_PATH_FORBIDDEN_EFFECTS = {
    "write",
    "delete",
    "move",
    "send",
    "submit",
    "type",
    "shell",
    "credential",
    "payment",
    "external_post",
    "browser_write",
}
FAST_PATH_TRUST_TIERS = {"builtin", "core", "first_party"}
FAST_PATH_BLOCKED_TRUST_TIERS = {"unknown", "advisory", "third_party", "untrusted"}
PATH_ARG_KEYS = {
    "path",
    "paths",
    "source",
    "sources",
    "destination",
    "destinations",
    "target",
    "target_path",
    "target_folder",
    "folder",
    "directory",
    "output_path",
    "file",
    "files",
}
SYSTEM_PATH_PREFIXES = (
    "c:/windows",
    "c:/program files",
    "c:/program files (x86)",
    "c:/programdata",
    "/windows",
    "/program files",
    "/programdata",
    "/etc",
    "/bin",
    "/sbin",
    "/usr",
    "/var",
    "/system",
    "/library",
)


class PolicyEngine:
    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        permission_policy: PermissionPolicy | None = None,
        permission_store: PermissionStore | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.dynamic_risk = DynamicRiskAssessor()
        self.permission_policy = permission_policy
        self.permission_store = permission_store or PermissionStore()
        self.now_provider = now_provider

    def review_goal_text(self, task_id: str, goal: str) -> SafetyReview:
        inspected_text = goal.lower()
        hits = self._forbidden_hits(inspected_text)
        if hits and not self._is_boundary_discussion(inspected_text):
            return SafetyReview(
                task_id=task_id,
                target_type="goal",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"Forbidden intent detected: {', '.join(sorted(hits))}."],
                safe_alternative="I can explain the security boundary or help with a safe read-only alternative.",
            )
        return SafetyReview(
            task_id=task_id,
            target_type="goal",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["No forbidden intent detected."],
        )

    def review_plan(self, plan: Plan) -> SafetyReview:
        risk = max_risk([step.risk_level for step in plan.steps])
        if risk == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            verdict = SafetyVerdict.DENY
            reasons = ["Plan contains forbidden or handoff-only operations."]
        elif risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}:
            verdict = SafetyVerdict.NEEDS_USER_APPROVAL
            reasons = ["Plan contains modifying operations that require dry-run and user approval."]
        else:
            verdict = SafetyVerdict.ALLOW
            reasons = ["Plan is within read/open-only risk bounds."]
        return SafetyReview(
            task_id=plan.task_id,
            target_type="plan",
            verdict=verdict,
            risk_level=risk,
            reasons=reasons,
            user_confirmation_message="Review and approve the proposed modifying steps before execution."
            if verdict == SafetyVerdict.NEEDS_USER_APPROVAL
            else "",
        )

    def review_tool_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
        context: dict[str, Any] | None = None,
        tool_definition: Any | None = None,
    ) -> SafetyReview:
        static_risk = max_risk([risk_level, self.classify_tool_name(tool_name)])
        permission_decision = self._review_permission_policy(tool_name, args, context)
        if not permission_decision.allowed:
            reason = permission_decision.reason or f"Permission policy denied {tool_name}."
            rule_id = getattr(permission_decision, "matched_rule_id", "") or getattr(permission_decision, "rule_id", "")
            if rule_id:
                reason = f"{reason} (rule: {rule_id})"
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=static_risk,
                reasons=[reason],
                safe_alternative="This action is blocked by your permission policy.",
            )

        cache_context = self._cache_context(args, context, tool_definition)
        cached = tool_decision_cache.get(tool_name, args, context=cache_context)
        if cached is not None:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=cached.verdict,
                risk_level=cached.risk_level,
                reasons=[*cached.reasons, "Tool-call decision reused from in-memory cache."],
            )

        fast_review = self._fast_path_tool_call(
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            args=args,
            static_risk=static_risk,
            context=context,
            tool_definition=tool_definition,
        )
        if fast_review is not None:
            tool_decision_cache.put_review(tool_name, args, fast_review, context=cache_context)
            return fast_review

        trust_review = self._review_tool_metadata_trust(task_id, step_id, tool_name, static_risk, tool_definition)
        if trust_review is not None:
            return trust_review

        dynamic = self.dynamic_risk.assess(
            tool_name=tool_name,
            args=args,
            base_risk=static_risk,
            context=context,
            task_id=task_id,
        )
        effective_risk = getattr(dynamic, "risk_level", None) or getattr(dynamic, "adjusted_risk")
        adjustments = getattr(dynamic, "adjustments", None) or getattr(dynamic, "reasons", [])
        dynamic_reasons = [
            f"Dynamic risk adjusted {static_risk} -> {effective_risk}: {reason}"
            for reason in adjustments
            if dynamic.changed
        ]

        if effective_risk == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            review = SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=effective_risk,
                reasons=[*dynamic_reasons, "This tool call is in the forbidden risk tier."],
                safe_alternative="Use a read-only inspection tool instead.",
            )
            tool_decision_cache.put_review(tool_name, args, review, context=cache_context)
            return review
        if effective_risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}:
            if not args.get("dry_run", True):
                review = SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                    risk_level=effective_risk,
                    reasons=[
                        *dynamic_reasons,
                        "Modifying tools require explicit user approval before non-dry-run execution.",
                    ],
                    user_confirmation_message=f"Approve {tool_name} with the shown diff preview?",
                )
                tool_decision_cache.put_review(tool_name, args, review, context=cache_context)
                return review
            review = SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                risk_level=effective_risk,
                reasons=[*dynamic_reasons, "Dry-run preview generated; user approval is required for execution."],
                user_confirmation_message=f"Approve {tool_name} after reviewing the preview?",
            )
            tool_decision_cache.put_review(tool_name, args, review, context=cache_context)
            return review
        review = SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=effective_risk,
            reasons=[*dynamic_reasons, "Read-only or open-only tool call allowed."],
        )
        tool_decision_cache.put_review(tool_name, args, review, context=cache_context)
        return review

    def _review_tool_metadata_trust(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        static_risk: RiskLevel,
        tool_definition: Any | None,
    ) -> SafetyReview | None:
        if static_risk not in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY}:
            return None
        if tool_definition is None:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"Unknown low-risk tool {tool_name} lacks authoritative metadata; fail-closed."],
                safe_alternative="Use a built-in trusted tool or explicitly configure a permission rule.",
            )
        trust_tier = str(getattr(tool_definition, "trust_tier", "unknown") or "unknown").casefold()
        if trust_tier not in FAST_PATH_TRUST_TIERS:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"Low-risk execution for {tool_name} requires authoritative metadata; trust tier is {trust_tier}."],
                safe_alternative="Review and approve the tool through an explicit trusted adapter or built-in tool definition.",
            )
        return None

    def review_agent_message(self, message: AgentMessage, stage: str) -> SafetyReview:
        inspected_text = self._inspectable_text(message.content, message.structured_payload, message.metadata)
        hits = self._forbidden_hits(inspected_text)
        if hits and not self._is_boundary_discussion(inspected_text):
            return SafetyReview(
                task_id=message.task_id,
                step_id=message.step_id,
                target_type=f"agent_message:{stage}",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[
                    f"Runtime supervision detected restricted content from {message.from_agent}: "
                    f"{', '.join(sorted(hits))}."
                ],
                safe_alternative="Stop this agent turn and ask the user for a safe, non-sensitive alternative.",
            )

        reason = (
            "Runtime supervision observed restricted terms only in a deny/read-only/approval boundary context."
            if hits
            else "Runtime supervision found no unsafe agent instruction or disclosure."
        )
        return SafetyReview(
            task_id=message.task_id,
            step_id=message.step_id,
            target_type=f"agent_message:{stage}",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=[reason],
        )

    def review_tool_result(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        result: ToolResult,
        risk_level: RiskLevel,
    ) -> SafetyReview:
        inspected_text = self._inspectable_text(result.output, result.error, result.changed_paths, result.rollback_info)
        hits = self._forbidden_hits(inspected_text)
        if risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF or (hits and not self._is_boundary_discussion(inspected_text)):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_result",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[
                    f"Post-tool supervision blocked {tool_name}; result may expose or act on restricted material."
                ],
                safe_alternative="Tool result was withheld by SafetyReviewAgent.",
            )
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=risk_level,
            reasons=[f"Post-tool supervision cleared {tool_name} result."],
        )

    def final_review(self, plan: Plan, task_status: str, final_summary: str) -> SafetyReview:
        inspected_text = self._inspectable_text(plan.model_dump(), task_status, final_summary)
        hits = self._forbidden_hits(inspected_text)
        if hits and not self._is_boundary_discussion(inspected_text):
            return SafetyReview(
                task_id=plan.task_id,
                target_type="final",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=["Final runtime review detected restricted content before completion."],
                safe_alternative="Final answer blocked; revise the plan toward a safe read-only alternative.",
            )
        return SafetyReview(
            task_id=plan.task_id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=plan.global_risk_level,
            reasons=["Final runtime review cleared the task state and summary."],
        )

    def classify_tool_name(self, tool_name: str) -> RiskLevel:
        if tool_name.startswith("mcp."):
            return RiskLevel.R4_FORBIDDEN_OR_HANDOFF
        if any(term in tool_name for term in ["password", "cookie", "token", "shell"]):
            return RiskLevel.R4_FORBIDDEN_OR_HANDOFF
        if tool_name.startswith("app.excel."):
            if tool_name == "app.excel.write_cell":
                return RiskLevel.R2_REVERSIBLE_MODIFY
            if tool_name in {"app.excel.status", "app.excel.read_workbook_summary"}:
                return RiskLevel.R0_READ_ONLY
            return RiskLevel.R4_FORBIDDEN_OR_HANDOFF
        if tool_name in {"file.trash", "app.uninstall_app", "browser.submit_form"}:
            return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if tool_name in {
            "external.email.send",
            "external.calendar.create_event",
            "external.webhook.post",
        }:
            return RiskLevel.R2_REVERSIBLE_MODIFY
        if tool_name in {"remote.click", "remote.type_text", "remote.key_press"}:
            return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if tool_name == "remote.view_screen":
            return RiskLevel.R1_OPEN_ONLY
        if tool_name in {
            "file.copy",
            "file.move",
            "file.rename",
            "file.write_text",
            "file.create_folder",
            "browser.click_element",
            "browser.fill_form",
        }:
            return RiskLevel.R2_REVERSIBLE_MODIFY
        if tool_name in {
            "app.open_file",
            "app.open_folder",
            "app.launch_allowlisted",
            "app.launch_installed",
            "app.reveal_in_explorer",
            "browser.open_url",
            "browser.navigate",
            "system.open_settings_uri",
        }:
            return RiskLevel.R1_OPEN_ONLY
        return RiskLevel.R0_READ_ONLY

    def review_browser_write_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
    ) -> SafetyReview | None:
        """Extra gate for browser write actions. Returns DENY when forbidden; None when not applicable."""
        if tool_name not in BROWSER_WRITE_TOOLS:
            return None
        if self.settings is not None:
            decision = can_use_browser_writes(self.settings)
            if not decision.allowed:
                return SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.DENY,
                    risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
                    reasons=[decision.reason],
                    safe_alternative="Switch to efficiency mode or enable browser network to use this action.",
                )
        field_name = str(args.get("field_name") or args.get("selector") or "").lower()
        value_text = str(args.get("value") or "").lower()
        if any(term in field_name for term in SENSITIVE_FIELD_NAMES):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"Sensitive form field '{field_name}' is forbidden."],
                safe_alternative="The user must enter credentials or payment data themselves.",
            )
        forbidden_in_value = self._forbidden_hits(value_text)
        if forbidden_in_value:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"Restricted material in form value: {', '.join(sorted(forbidden_in_value))}"],
                safe_alternative="Ask the user to fill sensitive fields manually.",
            )
        return None

    def _fast_path_tool_call(
        self,
        *,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        static_risk: RiskLevel,
        context: dict[str, Any] | None,
        tool_definition: Any | None,
    ) -> SafetyReview | None:
        context = context or {}
        if tool_definition is None or not getattr(tool_definition, "fast_path_eligible", False):
            return None
        trust_tier = str(getattr(tool_definition, "trust_tier", "unknown") or "unknown").casefold()
        if trust_tier in FAST_PATH_BLOCKED_TRUST_TIERS or trust_tier not in FAST_PATH_TRUST_TIERS:
            return None
        if static_risk not in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY}:
            return None
        if getattr(tool_definition, "external_network", False):
            return None
        if tool_name in BROWSER_WRITE_TOOLS or tool_name == "browser.navigate":
            return None
        effects = {str(item).casefold() for item in (getattr(tool_definition, "effects", None) or [])}
        if not effects or effects - FAST_PATH_ALLOWED_EFFECTS or effects & FAST_PATH_FORBIDDEN_EFFECTS:
            return None
        inspected = self._inspectable_text(args)
        if self._forbidden_hits(inspected) or self._sensitive_arg_hit(args, tool_definition):
            return None
        if _contains_system_path(args):
            return None
        dynamic = self.dynamic_risk.assess(
            tool_name=tool_name,
            args=args,
            base_risk=static_risk,
            context=context,
            task_id=task_id,
        )
        if dynamic.risk_level != static_risk:
            return None
        cache_key = self._fast_path_cache_key(tool_name, args, static_risk, context, tool_definition)
        fast_cache_context = {"cache_scope": "deterministic_fast_path"}
        cached = tool_decision_cache.get("fast_path", {"cache_key": cache_key}, context=fast_cache_context)
        cache_id = short_digest(cache_key)
        if cached is not None:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.ALLOW,
                risk_level=static_risk,
                reasons=[f"Deterministic fast path cache hit for {tool_name} ({cache_id})."],
            )
        tool_decision_cache.put(
            "fast_path",
            {"cache_key": cache_key},
            verdict=SafetyVerdict.ALLOW,
            risk_level=static_risk,
            reasons=["deterministic fast path"],
            context=fast_cache_context,
        )
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=static_risk,
            reasons=[f"Deterministic fast path allowed low-risk {tool_name} ({cache_id})."],
        )

    def _fast_path_cache_key(
        self,
        tool_name: str,
        args: dict[str, Any],
        static_risk: RiskLevel,
        context: dict[str, Any],
        tool_definition: Any,
    ) -> str:
        settings = context.get("settings") or self.settings
        allowed_directories = list(context.get("allowed_directories") or getattr(settings, "allowed_directories", []) or [])
        policy_version = permission_policy_version(self.permission_store.updated_at())
        return args_binding_hmac(
            "fast_path",
            {
                "tool_name": tool_name,
                "tool_args": args,
                "risk": static_risk.value,
                "settings": settings_fingerprint(settings, allowed_directories=allowed_directories),
                "permission_policy_version": policy_version,
                "tool_version": getattr(tool_definition, "tool_version", "1"),
                "fast_path_eligible": bool(getattr(tool_definition, "fast_path_eligible", False)),
                "trust_tier": str(getattr(tool_definition, "trust_tier", "unknown") or "unknown").casefold(),
                "external_network": bool(getattr(tool_definition, "external_network", False)),
                "capabilities": sorted(str(item) for item in (getattr(tool_definition, "capabilities", []) or [])),
                "effects": sorted(str(item) for item in (getattr(tool_definition, "effects", None) or [])),
                "resources": sorted(str(item) for item in (getattr(tool_definition, "resource_kinds", None) or [])),
                "sensitive_arg_keys": sorted(str(item) for item in (getattr(tool_definition, "sensitive_arg_keys", []) or [])),
                "dynamic_context": {
                    "recent_failure_count": context.get("recent_failure_count", context.get("recent_failures", 0)),
                    "trust_level": context.get("user_trust_level", context.get("trust_level", context.get("user_trust", "medium"))),
                    "timestamp": str(context.get("timestamp") or context.get("now") or context.get("current_time") or ""),
                },
            },
            task_id=str(context.get("task_id") or ""),
            step_id=str(context.get("step_id") or ""),
        )

    def _cache_context(
        self,
        args: dict[str, Any],
        context: dict[str, Any] | None,
        tool_definition: Any | None,
    ) -> dict[str, Any]:
        context = context or {}
        settings = context.get("settings") or self.settings
        allowed_directories = list(context.get("allowed_directories") or getattr(settings, "allowed_directories", []) or [])
        return {
            "policy": permission_policy_version(self.permission_store.updated_at()),
            "settings": settings_fingerprint(settings, allowed_directories=allowed_directories),
            "tool": {
                "version": getattr(tool_definition, "tool_version", ""),
                "fast_path_eligible": bool(getattr(tool_definition, "fast_path_eligible", False)),
                "trust_tier": str(getattr(tool_definition, "trust_tier", "unknown") or "unknown").casefold(),
                "external_network": bool(getattr(tool_definition, "external_network", False)),
                "capabilities": sorted(str(item) for item in (getattr(tool_definition, "capabilities", []) or [])),
                "effects": sorted(str(item) for item in (getattr(tool_definition, "effects", []) or [])),
                "resource_kinds": sorted(str(item) for item in (getattr(tool_definition, "resource_kinds", []) or [])),
                "sensitive_arg_keys": sorted(str(item) for item in (getattr(tool_definition, "sensitive_arg_keys", []) or [])),
            },
            "dynamic_context": {
                "recent_failure_count": context.get("recent_failure_count", context.get("recent_failures", 0)),
                "trust_level": context.get("user_trust_level", context.get("trust_level", context.get("user_trust", "medium"))),
                "timestamp": str(context.get("timestamp") or context.get("now") or context.get("current_time") or ""),
            },
            "args": args_binding_hmac("cache", args),
        }

    def _sensitive_arg_hit(self, args: dict[str, Any], tool_definition: Any | None = None) -> bool:
        sensitive_keys = set(SENSITIVE_FIELD_NAMES)
        sensitive_keys.update(str(item).casefold() for item in (getattr(tool_definition, "sensitive_arg_keys", None) or []))
        return _contains_sensitive_arg(args, sensitive_keys)

    def _inspectable_text(self, *items: Any) -> str:
        return " ".join(
            json.dumps(item, ensure_ascii=False, default=str) if not isinstance(item, str) else item
            for item in items
        ).lower()

    def _forbidden_hits(self, text: str) -> list[str]:
        hits: list[str] = []
        for term in FORBIDDEN_TERMS:
            pattern = rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                hits.append(term)
        return hits

    def _is_boundary_discussion(self, text: str) -> bool:
        boundary_terms = {
            "approval",
            "approve",
            "blocked",
            "deny",
            "denied",
            "forbidden",
            "handoff",
            "never",
            "read-only",
            "restricted",
            "safe alternative",
            "supervision",
        }
        return any(term in text for term in boundary_terms)

    def _review_permission_policy(self, tool_name: str, args: dict[str, Any], context: dict[str, Any] | None = None):
        policy = self.permission_policy
        if policy is None:
            try:
                now = self.now_provider() if self.now_provider else None
                return self.permission_store.evaluate(tool_name=tool_name, args=args, context=context, now=now)
            except Exception as exc:  # noqa: BLE001
                return _PermissionCheckDenied(str(exc))
        now = self.now_provider() if self.now_provider else None
        from app.policy.permissions import evaluate_permission_policy

        return evaluate_permission_policy(policy, tool_name=tool_name, args=args, context=context, now=now)


class _PermissionCheckAllowed:
    allowed = True
    matched_rule_id = ""
    reason = "Permission policy unavailable; falling back to built-in risk checks."


class _PermissionCheckDenied:
    allowed = False
    matched_rule_id = "permission_policy_unavailable"

    def __init__(self, error: str = "") -> None:
        self.reason = "Permission policy unavailable; fail-closed."
        if error:
            self.reason = f"{self.reason} {error}"


def _contains_sensitive_arg(value: Any, sensitive_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(term in normalized for term in sensitive_keys):
                return True
            if _contains_sensitive_arg(item, sensitive_keys):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_arg(item, sensitive_keys) for item in value)
    if isinstance(value, str):
        text = value.casefold()
        return any(term in text for term in {"password", "token", "cookie", "credential", "private key", "payment", "otp", "2fa"})
    return False


def _contains_system_path(args: dict[str, Any]) -> bool:
    return any(_is_system_path(path) for path in _candidate_paths(args))


def _candidate_paths(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in PATH_ARG_KEYS or "path" in normalized_key:
                result.extend(_candidate_paths(item))
            elif isinstance(item, (dict, list, tuple, set)):
                result.extend(_candidate_paths(item))
        return result
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_candidate_paths(item))
        return result
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(text)
    return result


def _is_system_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in SYSTEM_PATH_PREFIXES)


def _normalized_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    if not text:
        return ""
    try:
        pure = PureWindowsPath(text)
        if pure.drive:
            text = pure.as_posix()
    except (TypeError, ValueError):
        pass
    return text.rstrip("/").casefold()
