"""Plan-based cloud usage metering and limiting (cloud_quota).

This builds on the existing LLM usage ledger (:mod:`app.llm.usage`) rather than
introducing a second accounting system. Each plan gets rolling token budgets
that are enforced by default; ``LENGRVIS_CLOUD_QUOTA_ENFORCED=false`` is the
explicit local-development escape hatch.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.commerce.entitlements import Plan, active_plan, normalize_plan
from app.core import db
from app.core.errors import AppError
from app.core.schemas import now_iso

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


class QuotaUnavailableError(AppError):
    """Raised when cloud metering cannot safely authorize another call (HTTP 503)."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code="cloud_quota_unavailable", message=message, status_code=503)
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

_METERING_FAULT_LOCK = threading.Lock()
_metering_fault: dict[str, str] | None = None


def mark_cloud_metering_fault(reason: str = "usage_record_failed") -> None:
    global _metering_fault
    safe_reason = (
        reason
        if reason in {"usage_record_failed", "usage_reservation_failed", "usage_unreadable"}
        else "usage_unavailable"
    )
    with _METERING_FAULT_LOCK:
        if _metering_fault is None:
            _metering_fault = {"reason": safe_reason, "detected_at": now_iso()}


def cloud_metering_fault() -> dict[str, str] | None:
    with _METERING_FAULT_LOCK:
        return dict(_metering_fault) if _metering_fault is not None else None


def _clear_cloud_metering_fault_for_tests() -> None:
    global _metering_fault
    with _METERING_FAULT_LOCK:
        _metering_fault = None


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
    db.init_db()
    with db.connect() as conn:
        return _usage_from_connection(conn, window_hours)


def _exceeded(quota: CloudQuota, usage: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if quota.max_total_tokens is not None and usage["total_tokens"] >= quota.max_total_tokens:
        reasons.append("total_tokens")
    if quota.max_calls is not None and usage["calls"] >= quota.max_calls:
        reasons.append("calls")
    if quota.max_cost_usd is not None and usage["total_cost_usd"] >= quota.max_cost_usd:
        reasons.append("total_cost_usd")
    return reasons


def _limits(quota: CloudQuota) -> dict[str, Any]:
    return {
        "total_tokens": quota.max_total_tokens,
        "calls": quota.max_calls,
        "total_cost_usd": quota.max_cost_usd,
    }


def _usage_from_connection(conn: Any, window_hours: int) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=max(1, int(window_hours)))
    row = conn.execute(
        """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(total_cost_usd), 0.0) AS total_cost_usd,
               COALESCE(MAX(created_at), '') AS last_event_at
        FROM llm_usage_events
        WHERE created_at >= ?
          AND (
              json_extract(data, '$.metered_cloud') = 1
              OR json_extract(data, '$.reservation.state') IN ('reserved', 'settled')
              OR (
                  json_type(data, '$.metered_cloud') IS NULL
                  AND json_extract(data, '$.profile.capabilities.cloud') = 1
              )
          )
        """,
        (since.isoformat(),),
    ).fetchone()
    return {
        "calls": int(row["calls"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "total_cost_usd": float(row["total_cost_usd"] or 0.0),
        "window_hours": max(1, int(window_hours)),
        "last_event_at": str(row["last_event_at"] or ""),
    }


def _prospective_exceeded(quota: CloudQuota, usage: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if quota.max_total_tokens is not None and usage["total_tokens"] > quota.max_total_tokens:
        reasons.append("total_tokens")
    if quota.max_calls is not None and usage["calls"] > quota.max_calls:
        reasons.append("calls")
    if quota.max_cost_usd is not None and usage["total_cost_usd"] > quota.max_cost_usd:
        reasons.append("total_cost_usd")
    return reasons


def reserve_cloud_quota_call(
    settings: Any,
    *,
    provider: str,
    model: str,
    task: str,
    purpose: str,
    prompt_tokens: int,
    max_completion_tokens: int,
    max_cost_usd: float | None,
) -> str | None:
    """Atomically authorize and reserve the conservative upper bound for one cloud call."""
    if not quota_enforcement_enabled():
        return None
    plan = active_plan(settings)
    quotas = tuple(quota for quota in quota_windows_for_plan(plan) if not quota.is_unlimited)
    if not quotas:
        return None
    fault = cloud_metering_fault()
    if fault is not None:
        raise QuotaUnavailableError(
            f"Cloud usage metering is unavailable for plan '{plan.value}'; restart after fixing local storage.",
            details={"plan": plan.value, "reasons": [fault["reason"]]},
        )
    if max_cost_usd is None and any(quota.max_cost_usd is not None for quota in quotas):
        mark_cloud_metering_fault("usage_reservation_failed")
        raise QuotaUnavailableError(
            f"Cloud cost cannot be reserved safely for plan '{plan.value}'.",
            details={"plan": plan.value, "reasons": ["cost_estimate_unavailable"]},
        )

    reserved_prompt = max(0, int(prompt_tokens))
    reserved_completion = max(0, int(max_completion_tokens))
    reserved_total = reserved_prompt + reserved_completion
    reserved_cost = max(0.0, float(max_cost_usd or 0.0))
    created_at = now_iso()
    event_id = f"llm_usage_{uuid4().hex}"
    data = {
        "id": event_id,
        "provider": str(provider or "unknown"),
        "model": str(model or "unknown"),
        "mode": str(getattr(settings, "mode", "efficiency") or "efficiency"),
        "task": str(task or "default"),
        "purpose": str(purpose or "chat"),
        "usage": {
            "prompt_tokens": reserved_prompt,
            "completion_tokens": reserved_completion,
            "total_tokens": reserved_total,
            "estimated": True,
        },
        "cost": {"total_cost_usd": reserved_cost, "estimated": True},
        "reservation": {"state": "reserved", "conservative": True},
        "metered_cloud": True,
        "created_at": created_at,
    }
    try:
        db.init_db()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exceeded_windows: list[dict[str, Any]] = []
            for quota in quotas:
                current = _usage_from_connection(conn, quota.window_hours)
                prospective = {
                    **current,
                    "calls": current["calls"] + 1,
                    "total_tokens": current["total_tokens"] + reserved_total,
                    "total_cost_usd": current["total_cost_usd"] + reserved_cost,
                }
                reasons = _prospective_exceeded(quota, prospective)
                if reasons:
                    exceeded_windows.append(
                        {
                            "key": quota.key,
                            "window_hours": quota.window_hours,
                            "limits": _limits(quota),
                            "usage": current,
                            "prospective_usage": prospective,
                            "exceeded": reasons,
                        }
                    )
            if exceeded_windows:
                reasons = _aggregate_exceeded(exceeded_windows)
                raise QuotaExceededError(
                    f"Cloud usage quota would be exceeded for plan '{plan.value}'.",
                    details={
                        "plan": plan.value,
                        "reasons": reasons,
                        "window_hours": exceeded_windows[0]["window_hours"],
                        "limits": exceeded_windows[0]["limits"],
                        "usage": exceeded_windows[0]["usage"],
                        "windows": exceeded_windows,
                    },
                )
            conn.execute(
                """
                INSERT INTO llm_usage_events (
                    id, provider, model, mode, task, purpose,
                    prompt_tokens, completion_tokens, total_tokens,
                    total_cost_usd, estimated, data, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    event_id,
                    data["provider"],
                    data["model"],
                    data["mode"],
                    data["task"],
                    data["purpose"],
                    reserved_prompt,
                    reserved_completion,
                    reserved_total,
                    reserved_cost,
                    json.dumps(data, ensure_ascii=False),
                    created_at,
                ),
            )
    except (QuotaExceededError, QuotaUnavailableError):
        raise
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: block before network egress.
        mark_cloud_metering_fault("usage_reservation_failed")
        logger.warning("Cloud quota reservation failed closed: error_type=%s", type(exc).__name__)
        raise QuotaUnavailableError(
            f"Cloud usage metering is unavailable for plan '{plan.value}'; refusing cloud call.",
            details={"plan": plan.value, "reasons": ["usage_reservation_failed"]},
        ) from None
    return event_id


def _status_for_window(quota: CloudQuota) -> dict[str, Any]:
    try:
        usage = _current_usage(quota.window_hours)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: usage telemetry must never break the API
        mark_cloud_metering_fault("usage_unreadable")
        logger.warning("Failed to read cloud usage for quota status: error_type=%s", type(exc).__name__)
        return {
            "key": quota.key,
            "window_hours": quota.window_hours,
            "limits": _limits(quota),
            "available": False,
            "usage": None,
            "exceeded": ["usage_unavailable"],
        }
    return {
        "key": quota.key,
        "window_hours": quota.window_hours,
        "limits": _limits(quota),
        "available": True,
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
        "available": cloud_metering_fault() is None,
        "state": "available" if cloud_metering_fault() is None else "metering_unavailable",
        "window_hours": quotas[0].window_hours,
        "limits": _limits(quotas[0]),
    }
    if unlimited:
        status["usage"] = None
        status["exceeded"] = []
        status["windows"] = []
        return status
    windows = [_status_for_window(quota) for quota in quotas if not quota.is_unlimited]
    available = all(bool(window["available"]) for window in windows) and cloud_metering_fault() is None
    primary = next((window for window in windows if not window["available"] or window["exceeded"]), windows[0])
    status["available"] = available
    status["state"] = "available" if available else "metering_unavailable"
    status["window_hours"] = primary["window_hours"]
    status["limits"] = primary["limits"]
    status["usage"] = primary["usage"] if available else None
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
    fault = cloud_metering_fault()
    if fault is not None:
        raise QuotaUnavailableError(
            f"Cloud usage metering is unavailable for plan '{plan.value}'; refusing cloud call.",
            details={"plan": plan.value, "reasons": [fault["reason"]]},
        )
    exceeded_windows: list[dict[str, Any]] = []
    usage_errors: list[dict[str, Any]] = []
    for quota in quotas:
        try:
            usage = _current_usage(quota.window_hours)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: fail closed when quota usage is unreadable
            mark_cloud_metering_fault("usage_unreadable")
            logger.warning(
                "Cloud quota enforcement failed closed for %sh window: error_type=%s",
                quota.window_hours,
                type(exc).__name__,
            )
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
        raise QuotaUnavailableError(
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
