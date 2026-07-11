"""Plan-based feature gating for commercialization (套餐 feature gating).

This module is intentionally framework-agnostic (no FastAPI / DB imports) so it
can be used from settings resolution, API routes, and tests alike.

Tiers
-----
- ``Plan.FREE`` : 有限官方额度的手动任务
- ``Plan.PLUS`` : 正式自动化与跨网能力
- ``Plan.PRO``  : 与 Plus 相同的安全控制，并增加更强模型与更高额度

Entitlement gates *access* to a capability. It never replaces the per-action
strong-approval flow: high-risk features (e.g. remote control) still require an
explicit user approval even on an entitled plan.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

from app.core.errors import AppError

# Environment variable that selects the active deployment/licensing plan.
PLAN_ENV_VAR = "LENGRVIS_PLAN"
PLAN_CATALOG_CURRENT = "free-plus-pro-v1"
PLAN_CATALOG_LEGACY = "free-pro-max-v1"


class Plan(StrEnum):
    """Commercialization tiers, ordered FREE < PLUS < PRO."""

    FREE = "free"
    PLUS = "plus"
    PRO = "pro"
    # Source compatibility only. Public APIs and newly issued licenses never emit
    # these names; old ``max``/``team`` inputs normalize to the new Pro tier.
    MAX = "pro"
    TEAM = "pro"


class Feature(StrEnum):
    """Gateable product capabilities."""

    # Free tier
    LOCAL_READ_ONLY = "local_read_only"
    BASIC_TASKS = "basic_tasks"
    # Paid tiers. Plus and Pro share the same safety and product controls.
    CLOUD_QUOTA = "cloud_quota"
    DOCUMENT_AI = "document_ai"
    SCHEDULING = "scheduling"
    REMOTE_VIEW = "remote_view"
    REMOTE_CONTROL = "remote_control"
    AUDIT_EXPORT = "audit_export"
    POLICY_MANAGEMENT = "policy_management"
    PRIVATE_DEPLOYMENT = "private_deployment"
    # Pro adds model quality and quota only; it does not weaken safety controls.
    ADVANCED_MODELS = "advanced_models"


_PLAN_RANK: dict[Plan, int] = {Plan.FREE: 0, Plan.PLUS: 1, Plan.PRO: 2}
_PLAN_MONTHLY_PRICE_CNY: dict[Plan, int] = {Plan.FREE: 0, Plan.PLUS: 49, Plan.PRO: 129}

# Tolerant aliases so deployment configs / license strings normalize cleanly.
_PLAN_ALIASES: dict[str, Plan] = {
    "": Plan.FREE,
    "free": Plan.FREE,
    "basic": Plan.FREE,
    "starter": Plan.FREE,
    "community": Plan.FREE,
    "plus": Plan.PLUS,
    "premium": Plan.PLUS,
    "pro": Plan.PRO,
    "professional": Plan.PRO,
    "max": Plan.PRO,
    "maximum": Plan.PRO,
    "team": Plan.PRO,
    "team_self_hosted": Plan.PRO,
    "team-self-hosted": Plan.PRO,
    "self_hosted": Plan.PRO,
    "self-hosted": Plan.PRO,
    "enterprise": Plan.PRO,
}

_LEGACY_PLAN_ALIASES: dict[str, Plan] = {
    "": Plan.FREE,
    "free": Plan.FREE,
    "basic": Plan.FREE,
    "starter": Plan.FREE,
    "community": Plan.FREE,
    # In the former Free/Pro/Max catalog, Pro was the lower paid tier.
    "pro": Plan.PLUS,
    "professional": Plan.PLUS,
    "plus": Plan.PLUS,
    "premium": Plan.PLUS,
    "max": Plan.PRO,
    "maximum": Plan.PRO,
    "team": Plan.PRO,
    "team_self_hosted": Plan.PRO,
    "team-self-hosted": Plan.PRO,
    "self_hosted": Plan.PRO,
    "self-hosted": Plan.PRO,
    "enterprise": Plan.PRO,
}

# Minimum plan required to use each feature.
_FEATURE_MIN_PLAN: dict[Feature, Plan] = {
    Feature.LOCAL_READ_ONLY: Plan.FREE,
    Feature.BASIC_TASKS: Plan.FREE,
    Feature.CLOUD_QUOTA: Plan.PLUS,
    Feature.DOCUMENT_AI: Plan.PLUS,
    Feature.SCHEDULING: Plan.PLUS,
    Feature.REMOTE_VIEW: Plan.PLUS,
    Feature.REMOTE_CONTROL: Plan.PLUS,
    Feature.AUDIT_EXPORT: Plan.PLUS,
    Feature.POLICY_MANAGEMENT: Plan.PLUS,
    Feature.PRIVATE_DEPLOYMENT: Plan.PLUS,
    Feature.ADVANCED_MODELS: Plan.PRO,
}

# High-risk features always require an explicit per-action user approval, even
# when the active plan is entitled to them. Entitlement gates access; it never
# replaces the approval flow.
HIGH_RISK_FEATURES: frozenset[Feature] = frozenset({Feature.REMOTE_CONTROL})


def normalize_plan(value: Any) -> Plan:
    """Coerce arbitrary input into a :class:`Plan`, defaulting to ``FREE``."""
    if isinstance(value, Plan):
        return value
    candidate = str(value or "").strip().lower()
    return _PLAN_ALIASES.get(candidate, Plan.FREE)


def normalize_plan_claim(
    value: Any,
    *,
    catalog: str | None = None,
    schema: int | None = None,
) -> Plan:
    """Normalize a persisted license/subscription claim without silent upgrades.

    Claims from the former Free/Pro/Max catalog map old ``pro`` to Plus and old
    ``max`` to Pro. New claims are explicitly marked with
    :data:`PLAN_CATALOG_CURRENT` (or schema 2+) and use Free/Plus/Pro directly.
    Unknown catalog identifiers fail closed to Free.
    """

    normalized_catalog = str(catalog or "").strip().lower()
    if normalized_catalog == PLAN_CATALOG_CURRENT or (not normalized_catalog and (schema or 0) >= 2):
        return normalize_plan(value)
    if normalized_catalog in {"", PLAN_CATALOG_LEGACY}:
        candidate = str(value or "").strip().lower()
        return _LEGACY_PLAN_ALIASES.get(candidate, Plan.FREE)
    return Plan.FREE


def active_plan(settings: Any | None = None) -> Plan:
    """Resolve the active plan from settings (if it carries one) or the env.

    Plan is a deployment/licensing attribute, so the environment variable
    ``LENGRVIS_PLAN`` is the source of truth unless ``settings`` explicitly
    exposes a ``plan`` attribute.
    """
    raw: Any = getattr(settings, "plan", None) if settings is not None else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = os.getenv(PLAN_ENV_VAR)
    return normalize_plan(raw)


def required_plan(feature: Feature) -> Plan:
    """Return the minimum plan that unlocks ``feature``."""
    return _FEATURE_MIN_PLAN.get(feature, Plan.PRO)


def monthly_price_cny(plan: Any) -> int:
    """Return the client-approved monthly list price for the canonical plan."""

    return _PLAN_MONTHLY_PRICE_CNY[normalize_plan(plan)]


def model_tier(plan: Any) -> str:
    """Return the model quality tier without changing any safety control."""

    return "advanced" if has_feature(plan, Feature.ADVANCED_MODELS) else "standard"


def has_feature(plan: Any, feature: Feature) -> bool:
    """Return ``True`` if ``plan`` is entitled to ``feature``."""
    resolved = normalize_plan(plan)
    return _PLAN_RANK[resolved] >= _PLAN_RANK[required_plan(feature)]


def is_high_risk(feature: Feature) -> bool:
    """Return ``True`` if ``feature`` always needs explicit user approval."""
    return feature in HIGH_RISK_FEATURES


class EntitlementError(AppError):
    """Raised when the active plan is not entitled to a requested feature."""

    def __init__(self, feature: Feature, current: Any, *, required: Plan | None = None) -> None:
        self.feature = feature
        self.current_plan = normalize_plan(current)
        self.required_plan = required or required_plan(feature)
        super().__init__(
            code="entitlement_required",
            message=(
                f"Feature '{feature.value}' requires the '{self.required_plan.value}' plan "
                f"or higher; the active plan is '{self.current_plan.value}'."
            ),
            status_code=402,
        )


def require_feature(plan: Any, feature: Feature) -> Plan:
    """Assert ``plan`` is entitled to ``feature``; raise :class:`EntitlementError` if not.

    Returns the normalized plan on success so callers can chain further checks.
    """
    resolved = normalize_plan(plan)
    if not has_feature(resolved, feature):
        raise EntitlementError(feature, resolved)
    return resolved
