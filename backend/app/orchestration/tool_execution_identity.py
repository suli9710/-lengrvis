from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.automation.intent_capsule import user_goal_digest
from app.core.schemas import Task, ToolCall
from app.policy.approval_binding import canonical_args, hmac_digest
from app.policy.effective_risk_binding import EFFECTIVE_RISK_BINDING_VERSION
from app.policy.risk import RISK_ORDER, RiskLevel

_EFFECTIVE_RISK_BINDING_FIELDS = frozenset(
    {
        "version",
        "declared_risk_level",
        "effective_risk_level",
        "review_id",
    }
)
_RISK_PROVENANCE_FIELDS = (
    "version",
    "declared_risk_level",
    "effective_risk_level",
)
_REVIEW_ID = re.compile(r"review_[0-9a-f]{32}")


class ToolExecutionJournalError(RuntimeError):
    pass


def normalize_tool_execution_risk_binding(binding: Mapping[str, Any] | None) -> dict[str, str]:
    """Return the canonical four-field risk binding used by execution HMACs."""

    if not isinstance(binding, Mapping) or set(binding) != _EFFECTIVE_RISK_BINDING_FIELDS:
        raise ToolExecutionJournalError("Tool execution requires exactly the effective-risk/v1 binding fields.")
    if binding.get("version") != EFFECTIVE_RISK_BINDING_VERSION:
        raise ToolExecutionJournalError("Tool execution effective risk binding version is invalid.")
    review_id = str(binding.get("review_id") or "").strip()
    if _REVIEW_ID.fullmatch(review_id) is None:
        raise ToolExecutionJournalError("Tool execution effective risk binding review id is invalid.")
    try:
        declared = RiskLevel(str(binding.get("declared_risk_level") or ""))
        effective = RiskLevel(str(binding.get("effective_risk_level") or ""))
    except ValueError as exc:
        raise ToolExecutionJournalError("Tool execution effective risk binding risk level is invalid.") from exc
    if RISK_ORDER[effective] < RISK_ORDER[declared]:
        raise ToolExecutionJournalError("Tool execution effective risk cannot be lower than declared risk.")
    if effective == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
        raise ToolExecutionJournalError("Forbidden effective risk cannot be bound to tool execution.")
    return {
        "version": EFFECTIVE_RISK_BINDING_VERSION,
        "declared_risk_level": declared.value,
        "effective_risk_level": effective.value,
        "review_id": review_id,
    }


def build_tool_execution_intent_key(
    *,
    task: Task,
    step_id: str,
    tool_name: str,
    tool_version: str,
    args: dict[str, Any],
    plan_revision: int,
    approval_id: str | None,
) -> str:
    """Bind stable execution intent independently from a point-in-time review."""

    return hmac_digest(
        {
            "task_id": task.id,
            "user_goal_digest": user_goal_digest(task.user_goal),
            "step_id": step_id,
            "plan_revision": int(plan_revision),
            "tool_name": tool_name,
            "tool_version": tool_version,
            "args": canonical_args(args),
            "approval_id": approval_id or "",
        },
        prefix="execution-intent",
    )


def execution_key_for_intent(intent_key: str, risk_binding: Mapping[str, Any] | None) -> str:
    normalized = normalize_tool_execution_risk_binding(risk_binding)
    if not str(intent_key or "").startswith("execution-intent:"):
        raise ToolExecutionJournalError("Tool execution intent binding is invalid.")
    return hmac_digest(
        {
            "execution_intent_key": intent_key,
            "risk_binding": normalized,
        },
        prefix="execution",
    )


def build_tool_execution_key(
    *,
    task: Task,
    step_id: str,
    tool_name: str,
    tool_version: str,
    args: dict[str, Any],
    plan_revision: int,
    approval_id: str | None,
    risk_binding: Mapping[str, Any] | None,
) -> str:
    intent_key = build_tool_execution_intent_key(
        task=task,
        step_id=step_id,
        tool_name=tool_name,
        tool_version=tool_version,
        args=args,
        plan_revision=plan_revision,
        approval_id=approval_id,
    )
    return execution_key_for_intent(intent_key, risk_binding)


def tool_call_risk_binding(call: ToolCall) -> dict[str, str]:
    return normalize_tool_execution_risk_binding(
        {
            "version": call.risk_binding_version,
            "declared_risk_level": call.declared_risk_level,
            "effective_risk_level": call.risk_level,
            "review_id": call.risk_review_id,
        }
    )


def risk_provenance_matches(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    return all(left[field] == right[field] for field in _RISK_PROVENANCE_FIELDS)
