from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlparse

from app.agents.base import BaseAgent
from app.core import db
from app.core.schemas import MessageType, SafetyReview
from app.policy.policy_engine import (
    BROWSER_ACTIVITY_HANDOFF_TERMS,
    BROWSER_ACTIVITY_MUTATING_KINDS,
    BROWSER_ACTIVITY_READ_KINDS,
    BROWSER_ACTIVITY_TOOL_KIND_MAP,
    BROWSER_PROMPT_INJECTION_PATTERNS,
)
from app.policy.privacy import can_use_browser_writes
from app.policy.risk import RiskLevel, SafetyVerdict


class BrowserActivityReviewAgent(BaseAgent):
    name = "BrowserActivityReviewAgent"
    domain_summary = "Reviews every browser activity before global safety review, including reads and screenshots."
    prompt_file = "safety_review_agent.md"

    def review_tool_call(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel | None = None,
        context: dict[str, Any] | None = None,
        tool_definition: Any | None = None,
    ) -> SafetyReview | None:
        if not is_browser_tool(tool_name):
            return None
        kind = browser_activity_kind(tool_name, args)
        review = self.review_activity(
            task_id=task_id,
            step_id=step_id,
            action={"kind": kind, **dict(args or {})},
            tool_name=tool_name,
            declared_risk=risk_level,
            context=context,
            tool_definition=tool_definition,
        )
        return review

    def review_activity(
        self,
        *,
        task_id: str,
        step_id: str | None = None,
        action: Mapping[str, Any] | None = None,
        tool_name: str = "",
        declared_risk: RiskLevel | None = None,
        context: dict[str, Any] | None = None,
        tool_definition: Any | None = None,  # noqa: ARG002 - kept for ToolRuntime-compatible call sites.
    ) -> SafetyReview:
        payload = dict(action or {})
        kind = normalize_browser_activity_kind(payload.get("kind") or browser_activity_kind(tool_name, payload))
        target_type = f"browser_activity:{kind}"
        reasons: list[str] = []

        handoff_hits = _handoff_hits(payload)
        injection_hits = _prompt_injection_hits(payload, context)
        if handoff_hits or injection_hits:
            reasons.extend(_deny_reasons(handoff_hits, injection_hits))
            return self._record_review(
                SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type=target_type,
                    verdict=SafetyVerdict.DENY,
                    risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                    reasons=reasons,
                    safe_alternative=(
                        "Pause browser automation and ask the user to handle credentials, payments, "
                        "orders, messages, destructive actions, downloads/uploads, or page-supplied agent instructions."
                    ),
                ),
                tool_name=tool_name,
            )

        write_permission_review = self._review_browser_write_permission(task_id, step_id, target_type, kind, context)
        if write_permission_review is not None:
            return self._record_review(write_permission_review, tool_name=tool_name)

        if kind in BROWSER_ACTIVITY_READ_KINDS:
            risk = RiskLevel.R1_OPEN_ONLY if kind in {"open", "navigate"} else RiskLevel.R0_READ_ONLY
            return self._record_review(
                SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type=target_type,
                    verdict=SafetyVerdict.ALLOW,
                    risk_level=risk,
                    reasons=[f"Browser {kind} is read/open-only and was recorded for activity supervision."],
                ),
                tool_name=tool_name,
            )

        if kind in BROWSER_ACTIVITY_MUTATING_KINDS:
            risk = RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM if kind in {"submit", "cua"} else RiskLevel.R2_REVERSIBLE_MODIFY
            if declared_risk is not None and _risk_order(declared_risk) > _risk_order(risk):
                risk = declared_risk
            return self._record_review(
                SafetyReview(
                    task_id=task_id,
                    step_id=step_id,
                    target_type=target_type,
                    verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                    risk_level=risk,
                    reasons=[
                        (
                            f"Browser {kind} can change page state; "
                            "dry-run preview and explicit user approval are required."
                        )
                    ],
                    user_confirmation_message=f"Approve browser {kind} after reviewing the browser activity preview?",
                ),
                tool_name=tool_name,
            )

        return self._record_review(
            SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type=target_type,
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
                risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                reasons=[f"Unknown browser activity kind '{kind}' requires explicit review before execution."],
                user_confirmation_message="Approve this browser activity after confirming it is safe?",
            ),
            tool_name=tool_name,
        )

    def _review_browser_write_permission(
        self,
        task_id: str,
        step_id: str | None,
        target_type: str,
        kind: str,
        context: dict[str, Any] | None,
    ) -> SafetyReview | None:
        if kind not in BROWSER_ACTIVITY_MUTATING_KINDS:
            return None
        settings = (context or {}).get("settings")
        if settings is None:
            return None
        decision = can_use_browser_writes(settings)
        if decision.allowed:
            return None
        return SafetyReview(
            task_id=task_id,
            step_id=step_id,
            target_type=target_type,
            verdict=SafetyVerdict.DENY,
            risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            reasons=[decision.reason],
            safe_alternative="Switch to efficiency mode or enable browser network before automating browser writes.",
        )

    def _record_review(self, review: SafetyReview, *, tool_name: str = "") -> SafetyReview:
        db.upsert_model("safety_reviews", review)
        subject = tool_name or review.target_type
        self.bus.publish_text(
            review.task_id,
            self.name,
            f"{subject}: browser activity supervision -> {review.verdict} ({review.risk_level})",
            message_type=MessageType.REVIEW,
            step_id=review.step_id,
            structured_payload=review.model_dump(),
        )
        return review


def is_browser_tool(tool_name: str) -> bool:
    return str(tool_name or "").startswith("browser.")


def browser_activity_kind(tool_name: str, args: Mapping[str, Any] | None = None) -> str:
    args = args or {}
    explicit = args.get("kind")
    if explicit:
        return normalize_browser_activity_kind(explicit)
    nested_action = args.get("action")
    if isinstance(nested_action, Mapping) and nested_action.get("kind"):
        return normalize_browser_activity_kind(nested_action.get("kind"))
    return BROWSER_ACTIVITY_TOOL_KIND_MAP.get(str(tool_name or ""), "observe")


def normalize_browser_activity_kind(value: Any) -> str:
    text = str(value or "observe").strip().casefold().replace("_", "-")
    aliases = {
        "open-url": "open",
        "read": "observe",
        "read-page": "observe",
        "summarize-page": "observe",
        "extract-links": "observe",
        "links": "observe",
        "click-element": "click",
        "fill-form": "fill",
        "submit-form": "submit",
        "wait-for-selector": "wait",
        "computer-use": "cua",
        "computer-use-preview": "cua",
    }
    return aliases.get(text, text)


def _handoff_hits(payload: Mapping[str, Any]) -> list[str]:
    inspected = _inspectable_text(payload)
    hits: set[str] = set()
    for term in BROWSER_ACTIVITY_HANDOFF_TERMS:
        pattern = rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])"
        if re.search(pattern, inspected, flags=re.IGNORECASE):
            hits.add(term)

    for url in _candidate_urls(payload):
        parsed = urlparse(url)
        url_text = " ".join(
            [
                parsed.netloc,
                parsed.path,
                parsed.fragment,
                " ".join(key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)),
            ]
        ).casefold()
        for term in BROWSER_ACTIVITY_HANDOFF_TERMS:
            if re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", url_text):
                hits.add(term)
    return sorted(hits)


def _prompt_injection_hits(payload: Mapping[str, Any], context: dict[str, Any] | None) -> list[str]:
    inspected = _inspectable_text({"action": payload, "context": context or {}})
    hits = []
    for pattern in BROWSER_PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, inspected, flags=re.IGNORECASE):
            hits.append(pattern)
    return hits


def _deny_reasons(handoff_hits: list[str], injection_hits: list[str]) -> list[str]:
    reasons = []
    if handoff_hits:
        reasons.append(f"Browser activity touches handoff-only material: {', '.join(handoff_hits)}.")
    if injection_hits:
        reasons.append(
            "Browser activity appears to contain webpage instructions aimed at overriding the agent or policy."
        )
    return reasons


def _inspectable_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str).casefold()
    except TypeError:
        return str(value).casefold()


def _candidate_urls(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            if str(key).casefold() in {"url", "current_url", "href"} and isinstance(item, str):
                result.append(item)
            elif isinstance(item, Mapping | list | tuple | set):
                result.extend(_candidate_urls(item))
        return result
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            result.extend(_candidate_urls(item))
        return result
    return []


def _risk_order(level: Any) -> int:
    value = getattr(level, "value", str(level))
    order = {
        RiskLevel.R0_READ_ONLY.value: 0,
        RiskLevel.R1_OPEN_ONLY.value: 1,
        RiskLevel.R2_REVERSIBLE_MODIFY.value: 2,
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value: 3,
        RiskLevel.R4_FORBIDDEN_OR_HANDOFF.value: 4,
    }
    return order.get(value, 4)
