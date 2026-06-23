from __future__ import annotations

import pytest

from app.commerce import usage as commerce_usage
from app.commerce.entitlements import Plan
from app.commerce.usage import (
    QuotaExceededError,
    enforce_cloud_quota,
    quota_for_plan,
    quota_status,
)


class _S:
    def __init__(self, plan: str) -> None:
        self.plan = plan


def _over_limit(window_hours: int) -> dict[str, object]:
    return {
        "calls": 10 ** 9,
        "total_tokens": 10 ** 12,
        "total_cost_usd": 10 ** 6,
        "window_hours": window_hours,
        "last_event_at": "",
    }


def test_paid_plans_unlimited() -> None:
    assert quota_for_plan(Plan.PRO).is_unlimited
    assert quota_for_plan(Plan.TEAM).is_unlimited
    assert not quota_for_plan(Plan.FREE).is_unlimited


def test_enforce_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", raising=False)
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    enforce_cloud_quota(_S("free"))  # no raise: enforcement off


def test_enforce_blocks_when_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    with pytest.raises(QuotaExceededError):
        enforce_cloud_quota(_S("free"))


def test_enforce_allows_paid_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    enforce_cloud_quota(_S("team"))  # unlimited -> usage never read


def test_quota_status_free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")
    monkeypatch.setattr(
        commerce_usage,
        "_current_usage",
        lambda window_hours: {
            "calls": 1,
            "total_tokens": 2,
            "total_cost_usd": 0.0,
            "window_hours": window_hours,
            "last_event_at": "",
        },
    )
    status = quota_status(_S("free"))
    assert status["plan"] == "free"
    assert status["enforced"] is True
    assert status["unlimited"] is False
    assert status["exceeded"] == []
    assert status["usage"]["total_tokens"] == 2
