from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core import db
from app.core.schemas import Approval, Plan, SafetyReview, StepStatus
from app.policy.risk import RISK_ORDER, RiskLevel, SafetyVerdict

EFFECTIVE_RISK_BINDING_VERSION = "effective-risk/v1"
_BINDING_FIELDS = {
    "version",
    "declared_risk_level",
    "effective_risk_level",
    "review_id",
}


def build_effective_risk_binding(
    declared_risk_level: RiskLevel | str,
    reviews: Iterable[SafetyReview],
) -> dict[str, str]:
    declared = _risk_level(declared_risk_level)
    candidates = list(reviews)
    if not candidates:
        raise ValueError("Effective risk binding requires at least one safety review.")
    # Bind the review that actually contributed the highest effective tier.
    # Verdict and id are deterministic tie-breakers so caller ordering cannot
    # change the provenance selected for otherwise equivalent reviews.
    verdict_order = {
        SafetyVerdict.ALLOW: 0,
        SafetyVerdict.NEEDS_USER_APPROVAL: 1,
        SafetyVerdict.DENY: 2,
    }
    selected = max(
        candidates,
        key=lambda review: (
            RISK_ORDER[review.risk_level],
            verdict_order[review.verdict],
            review.id,
        ),
    )
    effective = selected.risk_level
    if RISK_ORDER[effective] < RISK_ORDER[declared]:
        effective = declared
    return {
        "version": EFFECTIVE_RISK_BINDING_VERSION,
        "declared_risk_level": declared.value,
        "effective_risk_level": effective.value,
        "review_id": selected.id,
    }


def approval_risk_binding(approval: Approval) -> Mapping[str, Any] | None:
    boundary = approval.engineering_boundary
    if not isinstance(boundary, Mapping):
        return None
    binding = boundary.get("risk_provenance")
    return binding if isinstance(binding, Mapping) else None


def effective_risk_binding_error(
    binding: Mapping[str, Any] | None,
    *,
    current_declared_risk: RiskLevel | str,
    approval_risk_level: RiskLevel | str | None = None,
) -> str:
    if binding is None:
        return "Approval lacks effective risk binding metadata; a fresh preview is required."
    if set(binding) != _BINDING_FIELDS:
        return "Approval effective risk binding fields are invalid; a fresh preview is required."
    if binding.get("version") != EFFECTIVE_RISK_BINDING_VERSION:
        return "Approval effective risk binding version is unsupported; a fresh preview is required."
    review_id = str(binding.get("review_id") or "").strip()
    if len(review_id) != 39 or not review_id.startswith("review_"):
        return "Approval effective risk binding lacks a review id; a fresh preview is required."
    try:
        declared = _risk_level(binding.get("declared_risk_level"))
        effective = _risk_level(binding.get("effective_risk_level"))
        current = _risk_level(current_declared_risk)
    except (TypeError, ValueError):
        return "Approval effective risk binding contains an invalid risk level; a fresh preview is required."
    if declared != current:
        return "Approval declared risk no longer matches the current tool call; a fresh preview is required."
    if RISK_ORDER[effective] < RISK_ORDER[declared]:
        return "Approval effective risk is lower than its declared risk; a fresh preview is required."
    if effective == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
        return "Approval is bound to a forbidden effective risk; execution is denied."
    if approval_risk_level:
        try:
            stored = _risk_level(approval_risk_level)
        except (TypeError, ValueError):
            return "Approval stored risk level is invalid; a fresh preview is required."
        if stored != effective:
            return "Approval stored risk does not match its effective risk binding; a fresh preview is required."
    return ""


def refreshed_effective_risk_error(
    binding: Mapping[str, Any],
    review: SafetyReview,
) -> str:
    approved = _risk_level(binding.get("effective_risk_level"))
    if review.verdict == SafetyVerdict.DENY or review.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
        return "Current safety review denies this tool call; a fresh preview is required."
    if RISK_ORDER[review.risk_level] > RISK_ORDER[approved]:
        return "Current effective risk is higher than the approved risk; a fresh preview is required."
    return ""


def risk_revalidation_context(context: Mapping[str, Any] | None, *, task_id: str) -> dict[str, Any]:
    refreshed = dict(context or {})
    stored_failures = 0
    try:
        rows = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
        if rows:
            plan = Plan.model_validate(rows[0])
            stored_failures = sum(1 for step in plan.steps if step.status == StepStatus.FAILED)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: storage uncertainty raises risk
        stored_failures = 3
    supplied = refreshed.get("recent_failure_count", refreshed.get("recent_failures", 0))
    try:
        supplied_failures = max(0, int(supplied))
    except (TypeError, ValueError):
        supplied_failures = len(supplied) if isinstance(supplied, list | tuple | set) else 0
    refreshed["recent_failure_count"] = max(stored_failures, supplied_failures)
    return refreshed


def _risk_level(value: RiskLevel | str | Any) -> RiskLevel:
    return value if isinstance(value, RiskLevel) else RiskLevel(str(value or ""))
