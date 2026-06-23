from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.commerce.entitlements import Plan
from app.commerce.licensing import (
    LicenseError,
    apply_licensed_plan,
    load_license,
    parse_license,
    resolve_licensed_plan,
    sign_license,
    verify_license,
)

SIGNING_KEY = "unit-test-signing-key"


def _make_token(plan: str = "team", expires_at: str | None = None, **extra: object) -> str:
    payload: dict[str, object] = {"plan": plan, "subject": "ACME", **extra}
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return sign_license(payload, SIGNING_KEY)


def test_sign_and_parse_roundtrip() -> None:
    lic = parse_license(_make_token(plan="team", seats=5), SIGNING_KEY)
    assert lic.plan is Plan.TEAM
    assert lic.subject == "ACME"
    assert lic.seats == 5
    assert lic.is_active()


def test_plan_alias_normalized() -> None:
    lic = parse_license(_make_token(plan="self-hosted"), SIGNING_KEY)
    assert lic.plan is Plan.TEAM


def test_tampered_signature_rejected() -> None:
    token = _make_token()
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(LicenseError):
        parse_license(tampered, SIGNING_KEY)


def test_wrong_key_rejected() -> None:
    with pytest.raises(LicenseError):
        parse_license(_make_token(), "a-different-key")


def test_expired_license_inactive() -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    token = _make_token(expires_at=past)
    assert parse_license(token, SIGNING_KEY).is_expired()
    with pytest.raises(LicenseError):
        verify_license(token, SIGNING_KEY)


def test_future_expiry_active() -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert verify_license(_make_token(expires_at=future), SIGNING_KEY).is_active()


def test_load_license_requires_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token())
    monkeypatch.delenv("LENGRVIS_LICENSE_SIGNING_KEY", raising=False)
    assert load_license() is None


def test_load_and_apply_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token(plan="team"))
    monkeypatch.setenv("LENGRVIS_LICENSE_SIGNING_KEY", SIGNING_KEY)
    assert resolve_licensed_plan() is Plan.TEAM

    class _S:
        plan = "free"

    settings = _S()
    applied = apply_licensed_plan(settings)
    assert applied.plan == "team"


def test_apply_plan_noop_without_license(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)

    class _S:
        plan = "free"
        data_dir = ""

    settings = _S()
    assert apply_licensed_plan(settings) is settings
