"""Plan-based cloud usage metering and limiting (cloud_quota).

This builds on the existing LLM usage ledger (:mod:`app.llm.usage`) rather than
introducing a second accounting system. Each plan gets rolling token budgets
that are enforced by default; ``LENGRVIS_CLOUD_QUOTA_ENFORCED=false`` is the
explicit local-development escape hatch.
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

_DEFAULT_WINDOW_HOURS = 24
_FREE_BURST_WINDOW_HOURS = 5
_WEEKLY_WINDOW_HOURS = 24 * 7
_FALSE_VALUES = {"0", "false", "no", "off"}


class QuotaExceededError(AppError):
    """Raised when the active plan's cloud usage budget is exhausted (HTTP 429)."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="cloud_quota_exceeded", message=message, status_code=429)
        self.details = details or {}


@dataclass(frozen=True)
class CloudQuota:
    """Rolling cloud LLM budget for a plan. ``None`` means unlimited."""

    max_total_tokens: int | None
    max_calls: int | None
    max_cost_usd: float | None
    window_hours: int = _DEFAULT_WINDOW_HOURS
    key: str = "rolling"

    @property
    def is_unlimited(self) -> bool:
        return self.max_total_tokens is None and self.max_calls is None and self.max_cost_usd is None


_PLAN_QUOTAS: dict[Plan, tuple[CloudQuota, ...]] = {
    Plan.FREE: (
        CloudQuota(
            max_total_tokens=5_000_000,
            max_calls=None,
            max_cost_usd=None,
            window_hours=_FREE_BURST_WINDOW_HOURS,
            key="5h",
        ),
        CloudQuota(
            max_total_tokens=20_000_000,
            max_calls=None,
            max_cost_usd=None,
            window_hours=_WEEKLY_WINDOW_HOURS,
            key="7d",
        ),
    ),
    Plan.PLUS: (
        CloudQuota(max_total_tokens=10_000_000, max_calls=None, max_cost_usd=None, window_hours=24, key="24h"),
    ),
    Plan.PRO: (
        CloudQuota(max_total_tokens=100_000_000, max_calls=None, max_cost_usd=None, window_hours=24, key="24h"),
    ),
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


def _env_present(name: str) -> bool:
    raw = os.getenv(name)
    return raw is not None and bool(raw.strip())


def _quota_override_present() -> bool:
    return any(
        _env_present(name)
        for name in (
            CLOUD_QUOTA_WINDOW_ENV_VAR,
            CLOUD_QUOTA_TOKENS_ENV_VAR,
            CLOUD_QUOTA_CALLS_ENV_VAR,
            CLOUD_QUOTA_COST_ENV_VAR,
        )
    )


def _with_env_overrides(base: CloudQuota) -> CloudQuota:
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
    return CloudQuota(
        max_total_tokens=tokens,
        max_calls=calls,
        max_cost_usd=cost,
        window_hours=max(1, window),
        key="env",
    )


def quota_windows_for_plan(plan: Plan) -> tuple[CloudQuota, ...]:
    base = _PLAN_QUOTAS.get(plan, _PLAN_QUOTAS[Plan.FREE])
    if _quota_override_present():
        # Legacy env overrides are a local-dev escape hatch and collapse to one explicit window.
        return (_with_env_overrides(base[0]),)
    return base


def quota_for_plan(plan: Plan) -> CloudQuota:
    return quota_windows_for_plan(plan)[0]


def quota_enforcement_enabled() -> bool:
    raw = os.getenv(CLOUD_QUOTA_ENFORCED_ENV_VAR)
    if raw is None or not raw.strip():
        return True
    normalized = raw.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    return True


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


def _limits(quota: CloudQuota) -> dict[str, Any]:
    return {
        "total_tokens": quota.max_total_tokens,
        "calls": quota.max_calls,
        "total_cost_usd": quota.max_cost_usd,
    }


def _status_for_window(quota: CloudQuota) -> dict[str, Any]:
    try:
        usage = _current_usage(quota.window_hours)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: usage telemetry must never break the API
        logger.warning("Failed to read cloud usage for quota status: %s", exc)
        usage = _empty_usage(quota.window_hours)
    return {
        "key": quota.key,
        "window_hours": quota.window_hours,
        "limits": _limits(quota),
        "usage": usage,
        "exceeded": _exceeded(quota, usage),
    }


def _aggregate_exceeded(windows: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for window in windows:
        for reason in window["exceeded"]:
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def quota_status(settings: Any | None = None) -> dict[str, Any]:
    plan = active_plan(settings) if settings is not None else normalize_plan(None)
    quotas = quota_windows_for_plan(plan)
    unlimited = all(quota.is_unlimited for quota in quotas)
    status: dict[str, Any] = {
        "plan": plan.value,
        "enforced": quota_enforcement_enabled(),
        "unlimited": unlimited,
        "window_hours": quotas[0].window_hours,
        "limits": _limits(quotas[0]),
    }
    if unlimited:
        status["usage"] = None
        status["exceeded"] = []
        status["windows"] = []
        return status
    windows = [_status_for_window(quota) for quota in quotas if not quota.is_unlimited]
    primary = next((window for window in windows if window["exceeded"]), windows[0])
    status["window_hours"] = primary["window_hours"]
    status["limits"] = primary["limits"]
    status["usage"] = primary["usage"]
    status["exceeded"] = _aggregate_exceeded(windows)
    status["windows"] = windows
    return status


def enforce_cloud_quota(settings: Any | None = None) -> None:
    """Raise :class:`QuotaExceededError` when the active plan's cloud budget is spent.

    No-op only when enforcement is explicitly disabled or the plan has no finite
    quota.
    """
    if not quota_enforcement_enabled():
        return
    plan = active_plan(settings)
    quotas = tuple(quota for quota in quota_windows_for_plan(plan) if not quota.is_unlimited)
    if not quotas:
        return
    exceeded_windows: list[dict[str, Any]] = []
    usage_errors: list[dict[str, Any]] = []
    for quota in quotas:
        try:
            usage = _current_usage(quota.window_hours)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: fail closed when quota usage is unreadable
            logger.warning("Cloud quota enforcement failed closed for %sh window: %s", quota.window_hours, exc)
            usage_errors.append({"key": quota.key, "window_hours": quota.window_hours})
            continue
        reasons = _exceeded(quota, usage)
        if reasons:
            exceeded_windows.append(
                {
                    "key": quota.key,
                    "window_hours": quota.window_hours,
                    "limits": _limits(quota),
                    "usage": usage,
                    "exceeded": reasons,
                }
            )
    if usage_errors:
        raise QuotaExceededError(
            f"Cloud usage quota unavailable for plan '{plan.value}'; refusing cloud call until usage is readable.",
            details={
                "plan": plan.value,
                "reasons": ["usage_unavailable"],
                "windows": usage_errors,
            },
        )
    if not exceeded_windows:
        return
    reasons = _aggregate_exceeded(exceeded_windows)
    reason_labels = [
        f"{reason} in {window['window_hours']}h" for window in exceeded_windows for reason in window["exceeded"]
    ]
    raise QuotaExceededError(
        f"Cloud usage quota exceeded for plan '{plan.value}': {', '.join(reason_labels)}.",
        details={
            "plan": plan.value,
            "reasons": reasons,
            "window_hours": exceeded_windows[0]["window_hours"],
            "limits": exceeded_windows[0]["limits"],
            "usage": exceeded_windows[0]["usage"],
            "windows": exceeded_windows,
        },
    )
