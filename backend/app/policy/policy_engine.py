from __future__ import annotations

import json
import re
from typing import Any

from app.config import AppSettings
from app.core.schemas import AgentMessage, Plan, PlanStep, SafetyReview, ToolResult
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
    "ssn",
    "口令",
    "密码",
}


BROWSER_WRITE_TOOLS = {
    "browser.click_element",
    "browser.fill_form",
    "browser.submit_form",
    "browser.navigate",
}


class PolicyEngine:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings

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
    ) -> SafetyReview:
        if risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.DENY,
                risk_level=risk_level,
                reasons=["This tool call is in the forbidden risk tier."],
                safe_alternative="Use a read-only inspection tool instead.",
            )
        if risk_level in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}:
            if not args.get("dry_run", True):
                return SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                    risk_level=risk_level,
                    reasons=["Modifying tools require explicit user approval before non-dry-run execution."],
                    user_confirmation_message=f"Approve {tool_name} with the shown diff preview?",
                )
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                risk_level=risk_level,
                reasons=["Dry-run preview generated; user approval is required for execution."],
                user_confirmation_message=f"Approve {tool_name} after reviewing the preview?",
            )
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=risk_level,
            reasons=["Read-only or open-only tool call allowed."],
        )

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
