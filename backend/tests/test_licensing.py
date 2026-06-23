from __future__ import annotations

import base64
import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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

PRIVATE_KEY_BYTES = bytes(range(1, 33))
PRIVATE_KEY = base64.urlsafe_b64encode(PRIVATE_KEY_BYTES).rstrip(b"=").decode("ascii")
_PRIVATE = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY_BYTES)
PUBLIC_KEY = (
    base64.urlsafe_b64encode(
        _PRIVATE.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    .rstrip(b"=")
    .decode("ascii")
)
WRONG_PUBLIC_KEY = (
    base64.urlsafe_b64encode(
        Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    .rstrip(b"=")
    .decode("ascii")
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_token(plan: str = "team", expires_at: str | None = None, **extra: object) -> str:
    payload: dict[str, object] = {"plan": plan, "subject": "ACME", **extra}
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return sign_license(payload, PRIVATE_KEY)


def _legacy_hmac_token(payload: dict[str, object], signing_key: str) -> str:
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest = hmac.new(signing_key.encode("utf-8"), body.encode("ascii"), sha256).digest()
    return f"{body}.{_b64url(digest)}"


def test_sign_and_parse_roundtrip() -> None:
    lic = parse_license(_make_token(plan="team", seats=5), PUBLIC_KEY)
    assert lic.plan is Plan.TEAM
    assert lic.subject == "ACME"
    assert lic.seats == 5
    assert lic.is_active()


def test_plan_alias_normalized() -> None:
    lic = parse_license(_make_token(plan="self-hosted"), PUBLIC_KEY)
    assert lic.plan is Plan.TEAM


def test_tampered_signature_rejected() -> None:
    token = _make_token()
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(LicenseError):
        parse_license(tampered, PUBLIC_KEY)


def test_wrong_public_key_rejected() -> None:
    with pytest.raises(LicenseError):
        parse_license(_make_token(), WRONG_PUBLIC_KEY)


def test_expired_license_inactive() -> None:
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    token = _make_token(expires_at=past)
    assert parse_license(token, PUBLIC_KEY).is_expired()
    with pytest.raises(LicenseError):
        verify_license(token, PUBLIC_KEY)


def test_future_expiry_active() -> None:
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    assert verify_license(_make_token(expires_at=future), PUBLIC_KEY).is_active()


def test_load_license_requires_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token())
    monkeypatch.delenv("LENGRVIS_LICENSE_PUBLIC_KEY", raising=False)
    assert load_license() is None


def test_deprecated_hmac_signing_key_is_not_a_runtime_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _legacy_hmac_token({"plan": "team", "subject": "ACME"}, "legacy-shared-secret")
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", token)
    monkeypatch.setenv("LENGRVIS_LICENSE_SIGNING_KEY", "legacy-shared-secret")
    monkeypatch.delenv("LENGRVIS_LICENSE_PUBLIC_KEY", raising=False)

    assert load_license() is None


def test_load_and_apply_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token(plan="team"))
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
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
