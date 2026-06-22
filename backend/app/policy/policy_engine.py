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
from app.policy.permission_modes import (
    is_modifying_risk,
    permission_mode_from_context,
    trusted_reversible_edit_allowed,
)
from app.policy.permissions import PermissionPolicy, PermissionStore
from app.policy.privacy import can_use_browser_writes
from app.policy.risk import RiskLevel, SafetyVerdict, max_risk
from app.policy.sensitive_values import looks_sensitive_value
from app.policy.decision_cache import _INTERNAL_CACHE_SCOPE_MARKER


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


# Words that signal a safety-system boundary notice (a denial being explained,
# an approval being requested, a read-only alternative being offered).
BOUNDARY_TERMS = (
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
)

# A forbidden term is only exempt when a boundary term occurs within this many
# characters of that occurrence (see PolicyEngine._unprotected_forbidden_hits).
BOUNDARY_CONTEXT_WINDOW = 120


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
    "browser.act",
    "browser.click_element",
    "browser.cua",
    "browser.cua_run",
    "browser.fill_form",
    "browser.submit_form",
}
UI_AUTOMATION_WRITE_TOOLS = {
    "ui_automation.click",
    "ui_automation.type_text",
    "ui_automation.click_at",
    "ui_automation.drag",
    "ui_automation.key_press",
    "ui_automation.hotkey",
}
BROWSER_ACTIVITY_READ_KINDS = {
    "open",
    "navigate",
    "wait",
    "screenshot",
    "observe",
}
BROWSER_ACTIVITY_MUTATING_KINDS = {
    "click",
    "fill",
    "submit",
    "scroll",
    "cua",
}
BROWSER_ACTIVITY_TOOL_KIND_MAP = {
    "browser.act": "observe",
    "browser.open_url": "open",
    "browser.navigate": "navigate",
    "browser.read_page": "observe",
    "browser.summarize_page": "observe",
    "browser.extract_links": "observe",
    "browser.search_web_via_provider": "observe",
    "browser.screenshot": "screenshot",
    "browser.wait_for_selector": "wait",
    "browser.click_element": "click",
    "browser.fill_form": "fill",
    "browser.submit_form": "submit",
    "browser.cua": "cua",
    "browser.cua_run": "cua",
}
BROWSER_ACTIVITY_HANDOFF_TERMS = {
    "2fa",
    "authenticator",
    "checkout",
    "cookie",
    "credential",
    "credentials",
    "cvv",
    "delete",
    "download",
    "login",
    "message",
    "order",
    "otp",
    "passcode",
    "password",
    "payment",
    "purchase",
    "send",
    "token",
    "upload",
    "密码",
    "支付",
    "下单",
}
BROWSER_PROMPT_INJECTION_PATTERNS = {
    r"ignore\s+(all\s+)?(previous|prior|system|developer)\s+instructions",
    r"disregard\s+(all\s+)?(previous|prior|system|developer)\s+instructions",
    r"reveal\s+(the\s+)?(system|developer)\s+prompt",
    r"send\s+(your\s+)?(cookies|tokens|credentials|api\s*keys)",
    r"disable\s+(safety|security|policy)",
    r"you\s+are\s+now\s+(in\s+)?developer\s+mode",
}
CLEANUP_READ_TOOLS = {"file.cleanup_scan", "file.cleanup_plan", "file.dedupe_plan"}
CLEANUP_WRITE_TOOLS = {"file.cleanup_execute", "file.cleanup_rollback"}

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

# P1-3 fix: MCP tool prefix matching should be case-insensitive.
_MCP_PREFIXES = ("mcp.", "mcp_", "mcp-", "mcp:")


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
        # User goals get no boundary-context exemption: a goal that asks for
        # forbidden material is denied even when it also mentions words like
        # "approval" or "read-only" (prompt-injection hardening).
        inspected_text = goal.lower()
        hits = self._forbidden_hits(inspected_text)
        if hits:
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
        classified_risk = self.classify_tool_call(tool_name, args, tool_definition=tool_definition)
        static_risk = classified_risk if tool_name == "browser.act" else max_risk([risk_level, classified_risk])
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

        cleanup_review = self._review_cleanup_tool_call(task_id, step_id, tool_name, args, static_risk)
        if cleanup_review is not None:
            return cleanup_review

        ui_review = self._review_ui_automation_call(task_id, step_id, tool_name, args, static_risk)
        if ui_review is not None:
            return ui_review

        mode_review = self._review_permission_mode(
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            args=args,
            static_risk=static_risk,
            context=context,
            tool_definition=tool_definition,
        )
        if mode_review is not None:
            return mode_review

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
            # P1-2 fix: dry_run should be explicitly checked, not defaulted to True.
            # The old code used args.get("dry_run", True) which meant any call
            # that forgot to include dry_run was silently treated as a dry-run.
            dry_run = args.get("dry_run")
            if dry_run is None:
                # dry_run not specified - require explicit dry-run first.
                review = SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                    risk_level=effective_risk,
                    reasons=[
                        *dynamic_reasons,
                        "Modifying tools require dry_run=True preview before non-dry-run execution; dry_run was not specified.",
                    ],
                    user_confirmation_message=f"Run {tool_name} with dry_run=True first to generate a preview.",
                )
                tool_decision_cache.put_review(tool_name, args, review, context=cache_context)
                return review
            if dry_run is False:
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
        all_hits = self._forbidden_hits(inspected_text)
        hits = self._unprotected_forbidden_hits(inspected_text) if all_hits else []
        if hits:
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
            if all_hits
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
        hits = self._unprotected_forbidden_hits(inspected_text)
        if risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF or hits:
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
        hits = self._unprotected_forbidden_hits(inspected_text)
        if hits:
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
        return self.classify_tool_call(tool_name, {})

    def classify_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        tool_definition: Any | None = None,
    ) -> RiskLevel:
        args = args or {}
        if tool_name == "browser.act":
            return _browser_activity_risk(args)
        declared = self._declared_tool_risk(tool_name, tool_definition)
        if declared is not None:
            return declared
        return self._legacy_classify_tool_call(tool_name, args)

    def _declared_tool_risk(self, tool_name: str, tool_definition: Any | None) -> RiskLevel | None:
        if tool_definition is not None and getattr(tool_definition, "risk_level", None) is not None:
            return tool_definition.risk_level
        try:
            from app.tools.registry import registry

            return registry.get(tool_name).risk_level
        except KeyError:
            return None

    def _legacy_classify_tool_call(self, tool_name: str, args: dict[str, Any]) -> RiskLevel:
        # P1-3 fix: case-insensitive MCP prefix matching.
        lowered = tool_name.casefold()
        if any(lowered.startswith(p) for p in _MCP_PREFIXES) or lowered == "mcp":
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
        if tool_name in CLEANUP_WRITE_TOOLS:
            return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if tool_name in CLEANUP_READ_TOOLS:
            return RiskLevel.R0_READ_ONLY
        if tool_name in {
            "external.email.send",
            "external.calendar.create_event",
            "external.webhook.post",
        }:
            return RiskLevel.R2_REVERSIBLE_MODIFY
        if tool_name in {
            "remote.click",
            "remote.type_text",
            "remote.key_press",
            "ui_automation.click_at",
            "ui_automation.drag",
            "ui_automation.key_press",
            "ui_automation.hotkey",
        }:
            return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if tool_name in {"remote.view_screen", "ui_automation.focus", "ui_automation.focus_window"}:
            return RiskLevel.R1_OPEN_ONLY
        if tool_name in {
            "browser.cua",
            "browser.cua_run",
        }:
            return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if tool_name in {
            "file.copy",
            "file.move",
            "file.rename",
            "file.write_text",
            "file.edit_text",
            "file.create_folder",
            "browser.click_element",
            "browser.fill_form",
            "ui_automation.click",
            "ui_automation.type_text",
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
        return RiskLevel.R4_FORBIDDEN_OR_HANDOFF

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

    def _review_ui_automation_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        static_risk: RiskLevel,
    ) -> SafetyReview | None:
        if tool_name not in UI_AUTOMATION_WRITE_TOOLS:
            return None
        selector_text = _ui_selector_text(args)
        if selector_text and any(term in selector_text for term in SENSITIVE_FIELD_NAMES):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=["GUI automation target appears to be a sensitive credential, payment, token, or one-time-code field."],
                safe_alternative="Ask the user to handle sensitive UI fields manually.",
            )
        typed_text = str(args.get("text") or args.get("value") or "")
        forbidden_in_text = self._forbidden_hits(typed_text.lower()) if typed_text else []
        if typed_text and (forbidden_in_text or looks_sensitive_value(typed_text)):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=["GUI automation text input looks sensitive and must be entered by the user."],
                safe_alternative="Leave the field focused and ask the user to type the sensitive value manually.",
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
        # P1-1 fix: use internal cache scope marker instead of caller-supplied string.
        fast_cache_context = {"_internal_cache_scope": _INTERNAL_CACHE_SCOPE_MARKER}
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

    def _review_permission_mode(
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
        mode = permission_mode_from_context(context, self.settings)
        if mode == "default":
            return None
        if mode == "plan" and is_modifying_risk(static_risk):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=static_risk,
                reasons=[f"Permission mode 'plan' blocks execution of modifying tool {tool_name}."],
                safe_alternative="Stay in planning mode or switch permission mode before executing changes.",
            )
        if mode == "dont_ask" and is_modifying_risk(static_risk):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=static_risk,
                reasons=[f"Permission mode 'dont_ask' denies {tool_name} because it would require approval."],
                safe_alternative="Add an explicit permission rule or switch to a mode that allows approval prompts.",
            )
        if mode in {"trusted_edits", "auto_review"} and trusted_reversible_edit_allowed(tool_definition, args):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.ALLOW,
                risk_level=static_risk,
                reasons=[f"Permission mode '{mode}' auto-cleared trusted reversible edit {tool_name}."],
            )
        return None

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
                "permission_mode": permission_mode_from_context(context, settings),
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
            "_internal_cache_scope": _INTERNAL_CACHE_SCOPE_MARKER,
            "policy": permission_policy_version(self.permission_store.updated_at()),
            "permission_mode": permission_mode_from_context(context, settings),
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

    def _boundary_spans(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for term in BOUNDARY_TERMS:
            pattern = rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])"
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                spans.append((match.start(), match.end()))
        return spans

    @staticmethod
    def _is_structural_key_occurrence(text: str, start: int, end: int) -> bool:
        """True when the match is a quoted dict/JSON key (e.g. ``'order':``).

        Serialized plan/step payloads carry structural field names such as
        ``order`` that collide with FORBIDDEN_TERMS. A quoted key followed by a
        colon is metadata, not natural-language instruction or disclosure.
        """
        if start == 0 or text[start - 1] not in "'\"":
            return False
        rest = text[end:]
        if not rest or rest[0] != text[start - 1]:
            return False
        return rest[1:].lstrip().startswith(":")

    def _unprotected_forbidden_hits(self, text: str) -> list[str]:
        """Forbidden terms that do not sit inside a boundary-discussion context.

        A forbidden term is only exempt when a boundary word (deny/approval/
        read-only/...) appears within ``BOUNDARY_CONTEXT_WINDOW`` characters of
        that specific occurrence. A boundary word elsewhere in the text no
        longer whitelists the whole message, so adversarial content cannot
        unlock supervision by simply appending words like "denied".
        """
        boundary_spans: list[tuple[int, int]] | None = None
        hits: list[str] = []
        for term in FORBIDDEN_TERMS:
            pattern = rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])"
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                if self._is_structural_key_occurrence(text, match.start(), match.end()):
                    continue
                if boundary_spans is None:
                    boundary_spans = self._boundary_spans(text)
                protected = any(
                    start - BOUNDARY_CONTEXT_WINDOW <= match.start() and match.end() <= end + BOUNDARY_CONTEXT_WINDOW
                    for start, end in boundary_spans
                )
                if not protected:
                    hits.append(term)
                    break
        return hits

    def _review_permission_policy(self, tool_name: str, args: dict[str, Any], context: dict[str, Any] | None = None):
        from app.policy.permissions import evaluate_user_permission_for_tool

        try:
            return evaluate_user_permission_for_tool(
                tool_name=tool_name,
                args=args,
                context=context,
                policy_engine=self,
            )
        except Exception as exc:  # noqa: BLE001
            return _PermissionCheckDenied(str(exc))

    def _review_cleanup_tool_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        static_risk: RiskLevel,
    ) -> SafetyReview | None:
        if tool_name not in CLEANUP_WRITE_TOOLS:
            return None
        if tool_name == "file.cleanup_rollback":
            dry_run = args.get("dry_run")
            if dry_run is not False and dry_run is not None:
                return SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                    risk_level=static_risk,
                    reasons=["Cleanup rollback preview generated; user approval is required for live rollback guidance."],
                    user_confirmation_message="Approve cleanup rollback after reviewing the rollback preview?",
                )
            if args.get("approved") and args.get("approval_id"):
                return SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.ALLOW,
                    risk_level=static_risk,
                    reasons=["Approved cleanup rollback may proceed; rollback tool still cannot restore recycle-bin items automatically."],
                )
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                risk_level=static_risk,
                reasons=["Cleanup rollback live execution requires approved=true and approval_id."],
                user_confirmation_message="Approve cleanup rollback after reviewing the rollback preview?",
            )

        missing = [key for key in ("plan_id", "content_hash", "selected_item_ids") if not args.get(key)]
        if missing:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=[f"cleanup_execute requires a valid cleanup plan binding: missing {', '.join(missing)}."],
                safe_alternative="Run file.cleanup_plan first and pass plan_id, content_hash, and selected_item_ids to cleanup_execute.",
            )

        if _cleanup_args_touch_system_or_sensitive_path(args):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                reasons=["cleanup_execute arguments include a system or sensitive path hint."],
                safe_alternative="Remove system, credential, browser profile, and sensitive paths from cleanup execution.",
            )

        live = args.get("dry_run") is False
        needs_trash_approval = _cleanup_has_trash_with_prompt(args)
        if live and args.get("approved") and args.get("approval_id"):
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.ALLOW,
                risk_level=static_risk,
                reasons=["Approved cleanup_execute may run; service will revalidate plan_id/content_hash and selected item ids."],
            )
        if live or needs_trash_approval:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                risk_level=static_risk,
                reasons=[
                    "cleanup_execute live execution or recycle-bin actions require explicit user approval.",
                    "delete_direct items are executable only when selected from a valid cleanup plan binding.",
                ],
                user_confirmation_message="Approve this cleanup execution after reviewing the exact cleanup plan?",
            )
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_call",
            verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            risk_level=static_risk,
            reasons=[
                "cleanup_execute is a destructive-capable tool; dry-run preview and approval are required before execution.",
                "The cleanup service will reject stale or tampered plan_id/content_hash values.",
            ],
            user_confirmation_message="Review the cleanup preview and approve the selected cleanup items?",
        )


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


def _ui_selector_text(args: dict[str, Any]) -> str:
    selector = args.get("selector")
    parts: list[Any] = []
    if isinstance(selector, dict):
        parts.extend(selector.values())
    elif selector:
        parts.append(selector)
    for key in (
        "name",
        "name_contains",
        "nameContains",
        "text_contains",
        "textContains",
        "automation_id",
        "automationId",
        "class_name",
        "className",
        "control_type",
        "controlType",
    ):
        if key in args:
            parts.append(args.get(key))
    return " ".join(str(part) for part in parts if part is not None).casefold()


def _contains_system_path(args: dict[str, Any]) -> bool:
    return any(_is_system_path(path) for path in _candidate_paths(args))


def _cleanup_args_touch_system_or_sensitive_path(args: dict[str, Any]) -> bool:
    sensitive_terms = {
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
    for path in _candidate_paths(args):
        normalized = _normalized_path(path)
        if _is_system_path(path) or any(term in normalized for term in sensitive_terms):
            return True
    return False


def _cleanup_has_trash_with_prompt(args: dict[str, Any]) -> bool:
    if str(args.get("action") or "").casefold() == "trash_with_prompt":
        return True
    for item in args.get("items") or args.get("selected_items") or []:
        if isinstance(item, dict) and str(item.get("action") or "").casefold() == "trash_with_prompt":
            return True
    return False


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
        return text.rstrip("/").casefold()
    return text.rstrip("/").casefold()


def _browser_activity_risk(args: dict[str, Any] | None) -> RiskLevel:
    payload = args or {}
    kind = str(payload.get("kind") or "").strip().casefold().replace("_", "-")
    action = payload.get("action")
    if not kind and isinstance(action, dict):
        kind = str(action.get("kind") or "").strip().casefold().replace("_", "-")
    if kind in {"open", "navigate"}:
        return RiskLevel.R1_OPEN_ONLY
    if kind in {"observe", "screenshot", "wait"}:
        return RiskLevel.R0_READ_ONLY
    if kind in {"click", "fill", "scroll"}:
        return RiskLevel.R2_REVERSIBLE_MODIFY
    if kind in {"submit", "cua", "computer-use"}:
        return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    return RiskLevel.R2_REVERSIBLE_MODIFY
