from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.api import routes_commerce
from app.commerce.device_identity import DeviceIdentityError, collect_activation_device_identity
from app.commerce.entitlements import Plan
from app.commerce.licensing import (
    LicenseError,
    apply_licensed_plan,
    install_license,
    license_status,
    load_license,
    load_revocation_manifest,
    parse_license,
    parse_revocation_manifest,
    resolve_licensed_plan,
    sign_license,
    sign_revocation_manifest,
    subscription_confirmation_fresh_for_high_risk,
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


def _make_token(plan: str = "max", expires_at: str | None = None, **extra: object) -> str:
    payload: dict[str, object] = {"plan": plan, "subject": "ACME", **extra}
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return sign_license(payload, PRIVATE_KEY)


def _legacy_hmac_token(payload: dict[str, object], signing_key: str) -> str:
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest = hmac.new(signing_key.encode("utf-8"), body.encode("ascii"), sha256).digest()
    return f"{body}.{_b64url(digest)}"


def test_sign_and_parse_roundtrip() -> None:
    lic = parse_license(
        _make_token(
            plan="max",
            seats=5,
            license_id="lic_acme",
            issuer="Lengrvis Sales",
            replaces="lic_old",
        ),
        PUBLIC_KEY,
    )
    assert lic.plan is Plan.MAX
    assert lic.license_id == "lic_acme"
    assert lic.subject == "ACME"
    assert lic.issuer == "Lengrvis Sales"
    assert lic.replaces == "lic_old"
    assert lic.seats == 5
    assert lic.is_active()


def test_plan_alias_normalized() -> None:
    lic = parse_license(_make_token(plan="self-hosted"), PUBLIC_KEY)
    assert lic.plan is Plan.MAX


def test_legacy_team_license_normalizes_to_max() -> None:
    lic = parse_license(_make_token(plan="team"), PUBLIC_KEY)
    assert lic.plan is Plan.MAX
    assert lic.plan.value == "max"


def test_subscription_status_must_be_active_or_trialing() -> None:
    token = _make_token(plan="max", subscription_id="sub_1", subscription_status="past_due")
    parsed = parse_license(token, PUBLIC_KEY)
    assert parsed.subscription_id == "sub_1"
    assert parsed.subscription_status == "past_due"
    with pytest.raises(LicenseError) as excinfo:
        verify_license(token, PUBLIC_KEY)
    assert excinfo.value.code == "subscription_past_due"


def test_license_status_reports_inactive_subscription(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    (tmp_path / "license.key").write_text(
        _make_token(plan="max", subscription_id="sub_1", subscription_status="canceled"),
        encoding="utf-8",
    )

    class _S:
        data_dir = str(tmp_path)

    status = license_status(_S())

    assert status["state"] == "subscription_inactive"
    assert status["active"] is False
    assert status["plan"] == "max"
    assert status["subscription_status"] == "canceled"


def test_subscription_license_requires_recent_online_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.delenv("LENGRVIS_ACTIVATION_BASE_URL", raising=False)
    token = _make_token(
        plan="max",
        license_id="lic_unconfirmed",
        subscription_id="sub_unconfirmed",
        subscription_status="active",
        activation={"source": "activation_server"},
    )

    class _S:
        data_dir = str(tmp_path)

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())
    assert excinfo.value.code == "subscription_confirmation_required"

    (tmp_path / "license.key").write_text(token, encoding="utf-8")
    assert load_license(_S()) is None
    status = license_status(_S())
    assert status["state"] == "subscription_confirmation_failed"
    assert status["active"] is False
    assert status["error_code"] == "activation_unconfigured"
    assert status["subscription_confirmation_required"] is True
    assert status["subscription_confirmation_fresh"] is False


def test_high_risk_subscription_confirmation_uses_shorter_freshness_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.delenv("LENGRVIS_ACTIVATION_BASE_URL", raising=False)
    token = _make_token(
        plan="max",
        license_id="lic_high_risk_stale",
        subscription_id="sub_high_risk",
        subscription_status="active",
        issued_at=(now - timedelta(minutes=20)).isoformat(),
        expires_at=(now + timedelta(days=7)).isoformat(),
        activation={"source": "activation_server"},
    )
    (tmp_path / "license.key").write_text(token, encoding="utf-8")

    class _S:
        data_dir = str(tmp_path)

    ordinary_status = license_status(_S(), now=now)

    assert ordinary_status["state"] == "active"
    assert ordinary_status["subscription_confirmation_fresh"] is True
    assert subscription_confirmation_fresh_for_high_risk(_S(), now=now) is False


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


def test_signed_revocation_manifest_blocks_matching_license() -> None:
    license_token = _make_token(plan="pro", license_id="lic_revoked")
    manifest_token = sign_revocation_manifest(
        {
            "schema": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "issuer": "Lengrvis Sales",
            "revoked": [
                {
                    "license_id": "lic_revoked",
                    "revoked_at": datetime.now(UTC).isoformat(),
                    "reason": "refund",
                }
            ],
        },
        PRIVATE_KEY,
    )
    manifest = parse_revocation_manifest(manifest_token, PUBLIC_KEY)

    assert manifest.is_revoked("lic_revoked")
    with pytest.raises(LicenseError) as excinfo:
        verify_license(license_token, PUBLIC_KEY, revocations=manifest)
    assert excinfo.value.code == "license_revoked"


def test_revocation_manifest_rejects_tampering() -> None:
    token = sign_revocation_manifest(
        {"schema": 1, "generated_at": datetime.now(UTC).isoformat(), "issuer": "issuer", "revoked": []},
        PRIVATE_KEY,
    )
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(LicenseError):
        parse_revocation_manifest(tampered, PUBLIC_KEY)


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
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token(plan="max"))
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    assert resolve_licensed_plan() is Plan.MAX

    class _S:
        plan = "free"

    settings = _S()
    applied = apply_licensed_plan(settings)
    assert applied.plan == "max"


def test_apply_plan_noop_without_license(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_COMMERCIAL_RELEASE", raising=False)

    class _S:
        plan = "free"
        data_dir = ""

    settings = _S()
    assert apply_licensed_plan(settings) is settings


def test_commercial_release_ignores_paid_plan_override_without_license(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")

    class _S:
        plan = "max"
        data_dir = ""

    settings = apply_licensed_plan(_S())
    assert settings.plan == "free"


def test_non_commercial_ignores_paid_plan_override_without_license(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_COMMERCIAL_RELEASE", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    class _S:
        plan = "max"
        data_dir = ""

    settings = apply_licensed_plan(_S())
    assert settings.plan == "free"


def test_license_status_reports_ignored_paid_plan_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.setenv("LENGRVIS_PLAN", "max")
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")

    class _S:
        plan = "max"
        data_dir = ""

    status = license_status(_S())

    assert status["state"] == "absent"
    assert status["requested_env_plan"] == "max"
    assert status["plan_env_ignored"] is True


def test_commercial_release_uses_verified_license_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setenv(
        "LENGRVIS_LICENSE_KEY",
        _make_token(plan="pro", license_id="lic_commercial", issuer="Lengrvis Sales"),
    )
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        plan = "max"
        data_dir = ""

    settings = apply_licensed_plan(_S())
    assert settings.plan == "pro"


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
    monkeypatch.setenv("LENGRVIS_LICENSE_KEY", _make_token(plan="max"))
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)

    with pytest.raises(LicenseError) as excinfo:
        install_license(_make_token(plan="pro"), _S())

    assert excinfo.value.code == "license_managed_externally"
    assert license_status(_S())["managed_by"] == "environment"


def test_device_bound_license_rejects_other_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)

    token = _make_token(plan="pro", license_id="lic_bound", device_id="dev_other_machine")

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())

    assert excinfo.value.code == "license_device_mismatch"
    (tmp_path / "license.key").write_text(token, encoding="utf-8")
    status = license_status(_S())
    assert status["state"] == "device_mismatch"
    assert status["active"] is False
    assert status["error_code"] == "license_device_mismatch"


def test_device_bound_license_fails_closed_when_device_id_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setattr(
        "app.commerce.licensing.collect_activation_device_identity",
        lambda settings: (_ for _ in ()).throw(RuntimeError("device unavailable")),
    )

    class _S:
        data_dir = str(tmp_path)

    token = _make_token(plan="pro", license_id="lic_bound_unverified", device_id="dev_bound")

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())

    assert excinfo.value.code == "license_device_unverified"
    (tmp_path / "license.key").write_text(token, encoding="utf-8")
    assert load_license(_S()) is None
    status = license_status(_S())
    assert status["state"] == "device_unverified"
    assert status["active"] is False
    assert status["error_code"] == "license_device_unverified"


def test_device_bound_license_with_fingerprint_accepts_local_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _S:
        data_dir = str(tmp_path)

    identity = collect_activation_device_identity(_S())
    token = _make_token(
        plan="pro",
        license_id="lic_fp_ok",
        device_id=identity.device_id,
        device_fingerprint=identity.fingerprint,
    )

    installed = install_license(token, _S())

    assert installed.plan is Plan.PRO
    assert load_license(_S()) is not None
    status = license_status(_S())
    assert status["state"] == "active"
    assert status["device_fingerprint"] == identity.fingerprint


def test_cloned_license_and_secret_rejected_on_different_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Copying license.key + activation_install.secret still fails when hardware differs."""
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _S:
        data_dir = str(tmp_path)

    monkeypatch.setattr(
        "app.commerce.device_identity._read_machine_id",
        lambda: "source-machine-guid",
    )
    source_identity = collect_activation_device_identity(_S())
    token = _make_token(
        plan="pro",
        license_id="lic_clone",
        device_id=source_identity.device_id,
        device_fingerprint=source_identity.fingerprint,
    )

    monkeypatch.setattr(
        "app.commerce.device_identity._read_machine_id",
        lambda: "cloned-host-machine-guid",
    )

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())

    assert excinfo.value.code == "license_device_fingerprint_mismatch"
    (tmp_path / "license.key").write_text(token, encoding="utf-8")
    assert load_license(_S()) is None
    status = license_status(_S())
    assert status["state"] == "device_fingerprint_mismatch"
    assert status["active"] is False
    assert status["error_code"] == "license_device_fingerprint_mismatch"


def test_device_bound_license_without_fingerprint_skips_fingerprint_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _S:
        data_dir = str(tmp_path)

    identity = collect_activation_device_identity(_S())
    token = _make_token(plan="pro", license_id="lic_legacy_bind", device_id=identity.device_id)

    installed = install_license(token, _S())

    assert installed.plan is Plan.PRO
    assert license_status(_S())["state"] == "active"


def test_commercial_release_rejects_license_without_device_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _S:
        data_dir = str(tmp_path)

    identity = collect_activation_device_identity(_S())
    token = _make_token(plan="pro", license_id="lic_commercial_legacy", device_id=identity.device_id)

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())

    assert excinfo.value.code == "license_device_fingerprint_missing"


def test_commercial_release_license_status_reports_missing_device_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _S:
        data_dir = str(tmp_path)

    identity = collect_activation_device_identity(_S())
    token = _make_token(plan="pro", license_id="lic_commercial_status", device_id=identity.device_id)
    (tmp_path / "license.key").write_text(token, encoding="utf-8")

    status = license_status(_S())

    assert status["state"] == "device_fingerprint_missing"
    assert status["active"] is False
    assert status["error_code"] == "license_device_fingerprint_missing"


def test_commercial_release_rejects_install_hash_only_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
    monkeypatch.setattr("app.commerce.device_identity._read_machine_id", lambda: "")
    monkeypatch.setattr("app.commerce.device_identity._safe_hostname", lambda: "")
    monkeypatch.setattr("app.commerce.device_identity._safe_node_id", lambda: "")

    class _S:
        data_dir = str(tmp_path)

    with pytest.raises(DeviceIdentityError) as excinfo:
        collect_activation_device_identity(_S())

    assert excinfo.value.code == "activation_device_fingerprint_weak"


def test_commercial_release_rejects_plaintext_activation_secret_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    secret_path = tmp_path / "activation_install.secret"
    secret_path.write_text("plaintext-secret-value", encoding="utf-8")

    monkeypatch.setattr("app.commerce.device_identity.os.name", "nt")

    with pytest.raises(DeviceIdentityError) as excinfo:
        from app.commerce import device_identity

        device_identity._assert_restrictive_secret_file_permissions(secret_path)

    assert excinfo.value.code == "activation_secret_insecure_permissions"


@pytest.mark.skipif(os.name == "nt", reason="Unix permission model")
def test_world_readable_activation_secret_rejects_license(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    secret_path = tmp_path / "activation_install.secret"
    secret_path.write_text("copied-secret-value", encoding="utf-8")
    secret_path.chmod(0o644)

    class _S:
        data_dir = str(tmp_path)

    device_id = f"dev_{sha256(b'copied-secret-value').hexdigest()[:32]}"
    token = _make_token(plan="pro", license_id="lic_insecure_secret", device_id=device_id)

    with pytest.raises(LicenseError) as excinfo:
        install_license(token, _S())

    assert excinfo.value.code == "activation_secret_insecure_permissions"


def test_runtime_revocation_file_disables_installed_license(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_LICENSE_REVOCATIONS", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    (tmp_path / "license.key").write_text(
        _make_token(plan="pro", license_id="lic_refunded"),
        encoding="utf-8",
    )
    (tmp_path / "license-revocations.key").write_text(
        sign_revocation_manifest(
            {
                "schema": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "issuer": "Lengrvis Sales",
                "revoked": [{"license_id": "lic_refunded", "reason": "refund"}],
            },
            PRIVATE_KEY,
        ),
        encoding="utf-8",
    )

    class _S:
        data_dir = str(tmp_path)

    manifest, source = load_revocation_manifest(_S())
    assert source == "file"
    assert manifest is not None and manifest.is_revoked("lic_refunded")
    assert load_license(_S()) is None
    status = license_status(_S())
    assert status["state"] == "revoked"
    assert status["active"] is False
    assert status["license_id"] == "lic_refunded"
    assert status["revocation_source"] == "file"


def test_invalid_revocation_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_LICENSE_REVOCATIONS", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)
    (tmp_path / "license.key").write_text(
        _make_token(plan="max", license_id="lic_max"),
        encoding="utf-8",
    )
    (tmp_path / "license-revocations.key").write_text("invalid.manifest", encoding="utf-8")

    class _S:
        data_dir = str(tmp_path)

    assert load_license(_S()) is None
    status = license_status(_S())
    assert status["state"] == "revocation_data_invalid"
    assert status["active"] is False


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
            {
                "license_id": "",
                "plan": "pro",
                "subject": "ACME",
                "issuer": "",
                "seats": 1,
                "expires_at": None,
            },
        )
    ]
    assert invalidations == [True]


def test_commerce_api_activation_records_safe_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv("LENGRVIS_LICENSE_PUBLIC_KEY", PUBLIC_KEY)

    class _S:
        data_dir = str(tmp_path)
        plan = "free"

    token = _make_token(
        plan="max",
        seats=1,
        license_id="lic_activated",
        subscription_id="sub_activated",
        subscription_status="active",
        issued_at=datetime.now(UTC).isoformat(),
        activation={"source": "activation_server"},
    )
    audit_events: list[tuple[str, str, dict[str, object]]] = []
    invalidations: list[bool] = []

    def _fake_activate(activation_key: str, settings: object, *, app_version: str = ""):
        assert activation_key == "activation-key-redacted"
        assert app_version == "desktop-test"
        return install_license(token, settings)

    monkeypatch.setattr(routes_commerce, "get_effective_settings", lambda: _S())
    monkeypatch.setattr(routes_commerce, "activate_license_with_server", _fake_activate)
    monkeypatch.setattr(
        routes_commerce.audit_core,
        "record",
        lambda event_type, actor, payload: audit_events.append((event_type, actor, payload)),
    )
    monkeypatch.setattr(routes_commerce, "invalidate_settings_cache", lambda: invalidations.append(True))

    response = routes_commerce.commerce_license_activate(
        routes_commerce.LicenseActivationRequest(
            activation_key="activation-key-redacted",
            app_version="desktop-test",
        )
    )

    assert response["state"] == "active"
    assert response["plan"] == "max"
    assert response["subscription_id"] == "sub_activated"
    assert audit_events[0][0] == "commerce.license.activated"
    assert audit_events[0][2]["subscription_id"] == "sub_activated"
    assert "activation-key-redacted" not in str(audit_events)
    assert invalidations == [True]
