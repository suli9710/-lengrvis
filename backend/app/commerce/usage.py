"""Plan-based cloud usage metering and limiting (cloud_quota).

This builds on the existing LLM usage ledger (:mod:`app.llm.usage`) rather than
introducing a second accounting system. Each plan gets a rolling monthly cloud
budget; paid tiers are unlimited. Enforcement is opt-in via
``LENGRVIS_CLOUD_QUOTA_ENFORCED`` so simply shipping this code never changes
existing free-tier cloud behavior by surprise.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from app.commerce.entitlements import Plan, active_plan, normalize_plan
from app.core.errors import AppError

logger = logging.getLogger(__name__)

CLOUD_QUOTA_ENFORCED_ENV_VAR = "LENGRVIS_CLOUD_QUOTA_ENFORCED"
CLOUD_QUOTA_WINDOW_ENV_VAR = "LENGRVIS_CLOUD_QUOTA_WINDOW_HOURS"
CLOUD_QUOTA_TOKENS_ENV_VAR = "LENGRVIS_CLOUD_QUOTA_MAX_TOKENS"
CLOUD_QUOTA_CALLS_ENV_VAR = "LENGRVIS_CLOUD_QUOTA_MAX_CALLS"
CLOUD_QUOTA_COST_ENV_VAR = "LENGRVIS_CLOUD_QUOTA_MAX_COST_USD"

_DEFAULT_WINDOW_HOURS = 720  # rolling 30-day window
_TRUE_VALUES = {"1", "true", "yes", "on"}


class QuotaExceededError(AppError):
    """Raised when the active plan's cloud usage budget is exhausted (HTTP 429)."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="cloud_quota_exceeded", message=message, status_code=429)
        self.details = details or {}


@dataclass(frozen=True)
class CloudQuota:
    """Monthly cloud LLM budget for a plan. ``None`` means unlimited."""

    max_total_tokens: int | None
    max_calls: int | None
    max_cost_usd: float | None
    window_hours: int = _DEFAULT_WINDOW_HOURS

    @property
    def is_unlimited(self) -> bool:
        return self.max_total_tokens is None and self.max_calls is None and self.max_cost_usd is None


_PLAN_QUOTAS: dict[Plan, CloudQuota] = {
    Plan.FREE: CloudQuota(max_total_tokens=1_000_000, max_calls=2_000, max_cost_usd=5.0),
    Plan.PRO: CloudQuota(max_total_tokens=None, max_calls=None, max_cost_usd=None),
    Plan.TEAM: CloudQuota(max_total_tokens=None, max_calls=None, max_cost_usd=None),
}


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return max(0, int(float(raw.strip())))
    except (TypeError, ValueError):
        return None


def _env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return None


def quota_for_plan(plan: Plan) -> CloudQuota:
    base = _PLAN_QUOTAS.get(plan, _PLAN_QUOTAS[Plan.FREE])
    window = _env_int(CLOUD_QUOTA_WINDOW_ENV_VAR) or base.window_hours or _DEFAULT_WINDOW_HOURS
    tokens = base.max_total_tokens
    calls = base.max_calls
    cost = base.max_cost_usd
    override_tokens = _env_int(CLOUD_QUOTA_TOKENS_ENV_VAR)
    override_calls = _env_int(CLOUD_QUOTA_CALLS_ENV_VAR)
    override_cost = _env_float(CLOUD_QUOTA_COST_ENV_VAR)
    if override_tokens is not None:
        tokens = override_tokens
    if override_calls is not None:
        calls = override_calls
    if override_cost is not None:
        cost = override_cost
    return CloudQuota(max_total_tokens=tokens, max_calls=calls, max_cost_usd=cost, window_hours=max(1, window))


def quota_enforcement_enabled() -> bool:
    return str(os.getenv(CLOUD_QUOTA_ENFORCED_ENV_VAR, "")).strip().lower() in _TRUE_VALUES


def _current_usage(window_hours: int) -> dict[str, Any]:
    # Lazy import avoids any import cycle with app.llm.* at module load time.
    from app.llm.usage import usage_summary

    summary = usage_summary(hours=window_hours)
    return {
        "calls": int(summary.get("calls") or 0),
        "total_tokens": int(summary.get("total_tokens") or 0),
        "total_cost_usd": float(summary.get("total_cost_usd") or 0.0),
        "window_hours": int(summary.get("window_hours") or window_hours),
        "last_event_at": summary.get("last_event_at") or "",
    }


def _exceeded(quota: CloudQuota, usage: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if quota.max_total_tokens is not None and usage["total_tokens"] >= quota.max_total_tokens:
        reasons.append("total_tokens")
    if quota.max_calls is not None and usage["calls"] >= quota.max_calls:
        reasons.append("calls")
    if quota.max_cost_usd is not None and usage["total_cost_usd"] >= quota.max_cost_usd:
        reasons.append("total_cost_usd")
    return reasons


def _empty_usage(window_hours: int) -> dict[str, Any]:
    return {"calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "window_hours": window_hours, "last_event_at": ""}


def quota_status(settings: Any | None = None) -> dict[str, Any]:
    plan = active_plan(settings) if settings is not None else normalize_plan(None)
    quota = quota_for_plan(plan)
    status: dict[str, Any] = {
        "plan": plan.value,
        "enforced": quota_enforcement_enabled(),
        "unlimited": quota.is_unlimited,
        "window_hours": quota.window_hours,
        "limits": {
            "total_tokens": quota.max_total_tokens,
            "calls": quota.max_calls,
            "total_cost_usd": quota.max_cost_usd,
        },
    }
    if quota.is_unlimited:
        status["usage"] = None
        status["exceeded"] = []
        return status
    try:
        usage = _current_usage(quota.window_hours)
    except Exception as exc:  # noqa: BLE001 - usage telemetry must never break the API
        logger.warning("Failed to read cloud usage for quota status: %s", exc)
        usage = _empty_usage(quota.window_hours)
    status["usage"] = usage
    status["exceeded"] = _exceeded(quota, usage)
    return status


def enforce_cloud_quota(settings: Any | None = None) -> None:
    """Raise :class:`QuotaExceededError` when the active plan's cloud budget is spent.

    No-op unless enforcement is explicitly enabled and the plan has a finite
    quota, so enabling metering never silently changes free-tier cloud behavior.
    """
    if not quota_enforcement_enabled():
        return
    plan = active_plan(settings)
    quota = quota_for_plan(plan)
    if quota.is_unlimited:
        return
    try:
        usage = _current_usage(quota.window_hours)
    except Exception as exc:  # noqa: BLE001 - never block an LLM call on telemetry failure
        logger.warning("Skipping cloud quota enforcement; usage read failed: %s", exc)
        return
    reasons = _exceeded(quota, usage)
    if not reasons:
        return
    raise QuotaExceededError(
        f"Cloud usage quota exceeded for plan '{plan.value}': {', '.join(reasons)}.",
        details={
            "plan": plan.value,
            "reasons": reasons,
            "window_hours": quota.window_hours,
            "limits": {
                "total_tokens": quota.max_total_tokens,
                "calls": quota.max_calls,
                "total_cost_usd": quota.max_cost_usd,
            },
            "usage": usage,
        },
    )
