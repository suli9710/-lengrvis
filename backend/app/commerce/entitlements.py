"""Plan-based feature gating for commercialization (套餐 feature gating).

This module is intentionally framework-agnostic (no FastAPI / DB imports) so it
can be used from settings resolution, API routes, and tests alike.

Tiers
-----
- ``Plan.FREE``  : 本机只读 + 基础任务
- ``Plan.PRO``   : 云端额度 + 文档AI + 调度 + 手机远控(高风险)
- ``Plan.MAX``   : 审计导出 + 策略管控 + 私有部署

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


class Plan(StrEnum):
    """Commercialization tiers, ordered FREE < PRO < MAX."""

    FREE = "free"
    PRO = "pro"
    MAX = "max"
    # Legacy alias: old licenses/configs used "team"; public APIs now emit "max".
    TEAM = "max"


class Feature(StrEnum):
    """Gateable product capabilities."""

    # Free tier
    LOCAL_READ_ONLY = "local_read_only"
    BASIC_TASKS = "basic_tasks"
    # Pro tier
    CLOUD_QUOTA = "cloud_quota"
    DOCUMENT_AI = "document_ai"
    SCHEDULING = "scheduling"
    REMOTE_VIEW = "remote_view"
    REMOTE_CONTROL = "remote_control"
    # Max tier
    AUDIT_EXPORT = "audit_export"
    POLICY_MANAGEMENT = "policy_management"
    PRIVATE_DEPLOYMENT = "private_deployment"


_PLAN_RANK: dict[Plan, int] = {Plan.FREE: 0, Plan.PRO: 1, Plan.MAX: 2}

# Tolerant aliases so deployment configs / license strings normalize cleanly.
_PLAN_ALIASES: dict[str, Plan] = {
    "": Plan.FREE,
    "free": Plan.FREE,
    "basic": Plan.FREE,
    "starter": Plan.FREE,
    "community": Plan.FREE,
    "pro": Plan.PRO,
    "professional": Plan.PRO,
    "plus": Plan.PRO,
    "premium": Plan.PRO,
    "max": Plan.MAX,
    "maximum": Plan.MAX,
    "team": Plan.MAX,
    "team_self_hosted": Plan.MAX,
    "team-self-hosted": Plan.MAX,
    "self_hosted": Plan.MAX,
    "self-hosted": Plan.MAX,
    "enterprise": Plan.MAX,
}

# Minimum plan required to use each feature.
_FEATURE_MIN_PLAN: dict[Feature, Plan] = {
    Feature.LOCAL_READ_ONLY: Plan.FREE,
    Feature.BASIC_TASKS: Plan.FREE,
    Feature.CLOUD_QUOTA: Plan.PRO,
    Feature.DOCUMENT_AI: Plan.PRO,
    Feature.SCHEDULING: Plan.PRO,
    Feature.REMOTE_VIEW: Plan.PRO,
    Feature.REMOTE_CONTROL: Plan.PRO,
    Feature.AUDIT_EXPORT: Plan.MAX,
    Feature.POLICY_MANAGEMENT: Plan.MAX,
    Feature.PRIVATE_DEPLOYMENT: Plan.MAX,
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
    return _FEATURE_MIN_PLAN.get(feature, Plan.MAX)


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
