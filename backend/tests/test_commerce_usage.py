from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.commerce import usage as commerce_usage
from app.commerce.entitlements import Plan
from app.commerce.usage import (
    QuotaExceededError,
    QuotaUnavailableError,
    enforce_cloud_quota,
    quota_for_plan,
    quota_status,
    quota_windows_for_plan,
    reserve_cloud_quota_call,
)


class _S:
    def __init__(self, plan: str) -> None:
        self.plan = plan


@pytest.fixture(autouse=True)
def _clear_quota_overrides(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    commerce_usage._clear_cloud_metering_fault_for_tests()
    for name in (
        commerce_usage.CLOUD_QUOTA_WINDOW_ENV_VAR,
        commerce_usage.CLOUD_QUOTA_TOKENS_ENV_VAR,
        commerce_usage.CLOUD_QUOTA_CALLS_ENV_VAR,
        commerce_usage.CLOUD_QUOTA_COST_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    commerce_usage._clear_cloud_metering_fault_for_tests()


def _over_limit(window_hours: int) -> dict[str, object]:
    return {
        "calls": 10**9,
        "total_tokens": 10**12,
        "total_cost_usd": 10**6,
        "window_hours": window_hours,
        "last_event_at": "",
    }


def test_plan_token_quota_windows() -> None:
    free_windows = quota_windows_for_plan(Plan.FREE)
    assert [(quota.window_hours, quota.max_total_tokens) for quota in free_windows] == [
        (5, 5_000_000),
        (168, 20_000_000),
    ]
    assert [(quota.window_hours, quota.max_total_tokens) for quota in quota_windows_for_plan(Plan.PLUS)] == [
        (24, 10_000_000),
    ]
    assert [(quota.window_hours, quota.max_total_tokens) for quota in quota_windows_for_plan(Plan.PRO)] == [
        (24, 100_000_000),
    ]
    assert not quota_for_plan(Plan.FREE).is_unlimited
    assert not quota_for_plan(Plan.PLUS).is_unlimited
    assert not quota_for_plan(Plan.PRO).is_unlimited


def test_enforce_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", raising=False)
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    with pytest.raises(QuotaExceededError):
        enforce_cloud_quota(_S("free"))


def test_enforce_can_be_disabled_for_local_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "false")
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    enforce_cloud_quota(_S("free"))


def test_enforce_blocks_when_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    with pytest.raises(QuotaExceededError):
        enforce_cloud_quota(_S("free"))


def test_enforce_blocks_paid_plans_when_over(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")
    monkeypatch.setattr(commerce_usage, "_current_usage", _over_limit)
    for plan in ("plus", "pro", "max", "team"):
        with pytest.raises(QuotaExceededError):
            enforce_cloud_quota(_S(plan))


def test_enforce_checks_weekly_free_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")

    def usage(window_hours: int) -> dict[str, object]:
        tokens = 1_000_000 if window_hours == 5 else 20_000_000
        return {
            "calls": 1,
            "total_tokens": tokens,
            "total_cost_usd": 0.0,
            "window_hours": window_hours,
            "last_event_at": "",
        }

    monkeypatch.setattr(commerce_usage, "_current_usage", usage)
    with pytest.raises(QuotaExceededError) as exc_info:
        enforce_cloud_quota(_S("free"))
    assert exc_info.value.details["window_hours"] == 168
    assert exc_info.value.details["windows"][0]["key"] == "7d"


def test_enforce_fails_closed_when_usage_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_CLOUD_QUOTA_ENFORCED", "true")

    def unavailable(window_hours: int) -> dict[str, object]:
        raise RuntimeError(f"usage ledger unavailable for {window_hours}h")

    monkeypatch.setattr(commerce_usage, "_current_usage", unavailable)
    with pytest.raises(QuotaUnavailableError) as exc_info:
        enforce_cloud_quota(_S("pro"))
    assert exc_info.value.details["reasons"] == ["usage_unavailable"]
    assert exc_info.value.details["windows"] == [{"key": "24h", "window_hours": 24}]


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
    assert [(window["key"], window["limits"]["total_tokens"]) for window in status["windows"]] == [
        ("5h", 5_000_000),
        ("7d", 20_000_000),
    ]


def test_quota_status_does_not_report_fake_zero_usage_when_ledger_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        commerce_usage,
        "_current_usage",
        lambda _window_hours: (_ for _ in ()).throw(OSError("storage unavailable")),
    )

    status = quota_status(_S("pro"))

    assert status["available"] is False
    assert status["state"] == "metering_unavailable"
    assert status["usage"] is None
    assert status["exceeded"] == ["usage_unavailable"]
    assert status["windows"][0]["available"] is False


def test_quota_reservation_is_atomic_at_call_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(commerce_usage.CLOUD_QUOTA_CALLS_ENV_VAR, "1")
    monkeypatch.setenv(commerce_usage.CLOUD_QUOTA_TOKENS_ENV_VAR, "100000")

    def reserve() -> str:
        return (
            reserve_cloud_quota_call(
                _S("pro"),
                provider="openai",
                model="gpt-4o-mini",
                task="planner",
                purpose="chat",
                prompt_tokens=10,
                max_completion_tokens=100,
                max_cost_usd=0.01,
            )
            or ""
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(("ok", future.result()))
            except QuotaExceededError as exc:
                outcomes.append(("quota", exc.code))

    assert [kind for kind, _value in outcomes].count("ok") == 1
    assert [kind for kind, _value in outcomes].count("quota") == 1


def test_reservation_storage_failure_latches_subsequent_cloud_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commerce_usage.db.init_db()

    def unavailable_connect():  # noqa: ANN202
        raise OSError("disk unavailable")

    monkeypatch.setattr(commerce_usage.db, "connect", unavailable_connect)

    with pytest.raises(QuotaUnavailableError) as exc_info:
        reserve_cloud_quota_call(
            _S("pro"),
            provider="openai",
            model="gpt-4o-mini",
            task="planner",
            purpose="chat",
            prompt_tokens=10,
            max_completion_tokens=100,
            max_cost_usd=0.01,
        )

    assert exc_info.value.details["reasons"] == ["usage_reservation_failed"]
    with pytest.raises(QuotaUnavailableError):
        enforce_cloud_quota(_S("pro"))
