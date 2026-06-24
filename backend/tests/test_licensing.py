from __future__ import annotations

import base64
import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api import routes_commerce
from app.commerce.entitlements import Plan
from app.commerce.licensing import (
    LicenseError,
    apply_licensed_plan,
    install_license,
    license_status,
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


def test_install_license_verifies_and_persists_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)

    token = _make_token(plan="pro", seats=1)
    installed = install_license(token, _S())

    assert installed.plan is Plan.PRO
    assert (tmp_path / "license.key").read_text(encoding="utf-8").strip() == token
    status = license_status(_S())
    assert status["state"] == "active"
    assert status["managed_by"] == "file"
    assert status["plan"] == "pro"
    assert status["seats"] == 1


def test_install_license_rejects_invalid_token_without_overwriting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    existing = tmp_path / "license.key"
    existing.write_text(_make_token(plan="pro"), encoding="utf-8")

    class _S:
        data_dir = str(tmp_path)

    with pytest.raises(LicenseError):
        install_license("invalid.token", _S())

    assert license_status(_S())["state"] == "active"
    assert existing.read_text(encoding="utf-8").strip() != "invalid.token"


def test_install_license_refuses_environment_managed_license(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token(plan="team"))
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)

    with pytest.raises(LicenseError) as excinfo:
        install_license(_make_token(plan="pro"), _S())

    assert excinfo.value.code == "license_managed_externally"
    assert license_status(_S())["managed_by"] == "environment"


def test_commerce_api_import_records_audit_and_returns_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)
        plan = "free"

    audit_events: list[tuple[str, str, dict[str, object]]] = []
    invalidations: list[bool] = []
    monkeypatch.setattr(routes_commerce, "get_effective_settings", lambda: _S())
    monkeypatch.setattr(
        routes_commerce.audit_core,
        "record",
        lambda event_type, actor, payload: audit_events.append((event_type, actor, payload)),
    )
    monkeypatch.setattr(routes_commerce, "invalidate_settings_cache", lambda: invalidations.append(True))

    response = routes_commerce.commerce_license_install(
        routes_commerce.LicenseInstallRequest(token=_make_token(plan="pro", seats=1))
    )

    assert response["state"] == "active"
    assert response["plan"] == "pro"
    assert audit_events == [
        (
            "commerce.license.installed",
            "desktop",
            {"plan": "pro", "subject": "ACME", "seats": 1, "expires_at": None},
        )
    ]
    assert invalidations == [True]
