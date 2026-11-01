"""Regression evidence for commerce/config correctness audit (local-only)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.commerce import licensing

_PRIVATE_BYTES = bytes(range(1, 33))
_PRIVATE = base64.urlsafe_b64encode(_PRIVATE_BYTES).decode()
_PUBLIC = base64.urlsafe_b64encode(
    Ed25519PrivateKey.from_private_bytes(_PRIVATE_BYTES)
    .public_key()
    .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
).decode()


def _token(**claims):
    return licensing.sign_license({"schema": 2, "plan_catalog": "free-plus-pro-v1", "plan": "pro", **claims}, _PRIVATE)


@pytest.mark.parametrize("field", ["expires_at", "issued_at", "renews_at"])
@pytest.mark.parametrize("value", [1e100, float("inf"), float("nan")])
def test_invalid_numeric_dates_raise_license_error(field, value):
    with pytest.raises(licensing.LicenseError):
        licensing.parse_license(_token(**{field: value}), _PUBLIC)


@pytest.mark.parametrize("operation", ["load", "status", "install"])
def test_clock_rollback_cannot_revive_paid_license(monkeypatch, tmp_path, operation):
    now = datetime.now(UTC)
    settings = SimpleNamespace(data_dir=str(tmp_path))
    licensing._write_clock_watermark(settings, now + timedelta(days=3))
    token = _token(expires_at=(now + timedelta(days=1)).isoformat())
    monkeypatch.setenv(licensing.LICENSE_PUBLIC_KEY_ENV_VAR, _PUBLIC)
    monkeypatch.delenv(licensing.LICENSE_KEY_ENV_VAR, raising=False)
    monkeypatch.setattr(licensing, "_expected_device_binding", lambda _: (None, None))
    (tmp_path / licensing.LICENSE_FILE_NAME).write_text(token)
    if operation == "load":
        assert licensing.load_license(settings) is None
    elif operation == "status":
        assert licensing.license_status(settings)["active"] is False
    else:
        with pytest.raises(licensing.LicenseError, match="已过期"):
            licensing.install_license(token, settings)


def test_corrupt_license_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv(licensing.LICENSE_KEY_ENV_VAR, raising=False)
    monkeypatch.setenv(licensing.LICENSE_PUBLIC_KEY_ENV_VAR, _PUBLIC)
    (tmp_path / licensing.LICENSE_FILE_NAME).write_bytes(b"\xff")
    assert licensing.load_license(SimpleNamespace(data_dir=str(tmp_path))) is None


@pytest.mark.parametrize("value", ["false", "off", "0", False])
def test_disabled_mcp_servers_remain_disabled(value):
    from app.config_normalization import normalize_mcp_servers

    assert normalize_mcp_servers([{"command": "example", "enabled": value}])[0]["enabled"] is False


@pytest.mark.parametrize("settings", [SimpleNamespace(), SimpleNamespace(data_dir=""), SimpleNamespace(data_dir=" ")])
def test_missing_device_storage_is_not_current_directory(settings):
    from app.commerce.device_identity import DeviceIdentityError, activation_install_secret_path

    with pytest.raises(DeviceIdentityError):
        activation_install_secret_path(settings)


@pytest.mark.parametrize("value", ["inf", "-inf", "nan"])
def test_nonfinite_quota_overrides_are_ignored(monkeypatch, value):
    from app.commerce import usage

    monkeypatch.setenv(usage.CLOUD_QUOTA_TOKENS_ENV_VAR, value)
    assert usage._env_int(usage.CLOUD_QUOTA_TOKENS_ENV_VAR) is None
    monkeypatch.setenv(usage.CLOUD_QUOTA_COST_ENV_VAR, value)
    assert usage._env_float(usage.CLOUD_QUOTA_COST_ENV_VAR) is None


@pytest.mark.parametrize(
    "mutation", ["DELETE FROM activation_devices", "UPDATE subscription_keys SET status = 'revoked'"]
)
def test_refresh_serializes_against_unbind_and_revoke(monkeypatch, tmp_path, mutation):
    import sqlite3

    from app.commerce import activation

    path = tmp_path / "activation.sqlite"
    monkeypatch.setenv(activation.ACTIVATION_KEY_PEPPER_ENV_VAR, "local-review-pepper")
    monkeypatch.setenv(activation.ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, "local-review-device")
    monkeypatch.setenv(activation.ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR, _PRIVATE)
    monkeypatch.setenv(licensing.LICENSE_PUBLIC_KEY_ENV_VAR, _PUBLIC)
    activation.upsert_subscription_key(
        activation_key="review-key", plan="pro", subscription_id="review-sub", status="active", db_path=path
    )
    issued = activation.activate_subscription_key(
        activation.ActivationRequest(
            activation_key="review-key",
            device_id="review-device",
            device_fingerprint="review-fingerprint",
            nonce="review-nonce-123456",
        ),
        db_path=path,
    )
    original = activation._load_subscription_record
    blocked = []

    def concurrent_mutation(conn, key_hash):
        record = original(conn, key_hash)
        # An independent connection models an admin action arriving after the
        # refresh's authorization reads but before its UPDATE.
        with sqlite3.connect(path, timeout=0) as writer:
            try:
                writer.execute(mutation)
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc)
                blocked.append(True)
        return record

    monkeypatch.setattr(activation, "_load_subscription_record", concurrent_mutation)
    activation.refresh_subscription_license(
        activation.ActivationRefreshRequest(
            license_token=issued.license_token,
            device_id="review-device",
            device_fingerprint="review-fingerprint",
            nonce="review-nonce-123456",
        ),
        db_path=path,
    )
    assert blocked, "Admin mutation committed between authorization and issuance"


def test_failed_signing_does_not_consume_activation_seat(monkeypatch, tmp_path):
    import sqlite3

    from app.commerce import activation

    path = tmp_path / "activation.sqlite"
    monkeypatch.setenv(activation.ACTIVATION_KEY_PEPPER_ENV_VAR, "local-review-pepper")
    monkeypatch.setenv(activation.ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, "local-review-device")
    activation.upsert_subscription_key(
        activation_key="review-key", plan="pro", subscription_id="review-sub", status="active", db_path=path
    )
    with pytest.raises(licensing.LicenseError):
        activation.activate_subscription_key(
            activation.ActivationRequest(
                activation_key="review-key",
                device_id="review-device",
                device_fingerprint="review-fingerprint",
                nonce="review-nonce-123456",
            ),
            db_path=path,
            private_key="invalid-key",
        )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM activation_devices").fetchone()[0] == 0


def test_activation_key_handoff_never_overwrites_racing_writer(monkeypatch, tmp_path):
    import os

    from app.commerce.activation import write_activation_key_once

    target = tmp_path / "key.txt"
    original_replace, original_link = os.replace, os.link

    def race(publication):
        def publish(source, destination, *args, **kwargs):
            if destination == target:
                target.write_text("other-key")
            return publication(source, destination, *args, **kwargs)

        return publish

    monkeypatch.setattr(os, "replace", race(original_replace))
    monkeypatch.setattr(os, "link", race(original_link))
    with pytest.raises(FileExistsError):
        write_activation_key_once(target, "new-key")
    assert target.read_text() == "other-key"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("value", ["inf", "nan", "not-a-number"])
def test_invalid_quota_override_preserves_weekly_free_limit(monkeypatch, value):
    from app.commerce import usage
    from app.commerce.entitlements import Plan

    for name in (
        usage.CLOUD_QUOTA_TOKENS_ENV_VAR,
        usage.CLOUD_QUOTA_CALLS_ENV_VAR,
        usage.CLOUD_QUOTA_COST_ENV_VAR,
        usage.CLOUD_QUOTA_WINDOW_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(usage.CLOUD_QUOTA_TOKENS_ENV_VAR, value)
    assert [quota.key for quota in usage.quota_windows_for_plan(Plan.FREE)] == ["5h", "7d"]


def test_zero_expiry_is_not_treated_as_perpetual():
    with pytest.raises(licensing.LicenseError, match="已过期"):
        licensing.verify_license(_token(expires_at=0), _PUBLIC)


def test_out_of_range_datetime_offset_is_license_error():
    with pytest.raises(licensing.LicenseError):
        licensing.parse_license(_token(expires_at="0001-01-01T00:00:00+01:00"), _PUBLIC)


def test_confirmation_time_overflow_is_not_fresh():
    license_ = licensing.parse_license(_token(issued_at="9999-12-31T23:59:59Z", subscription_id="sub"), _PUBLIC)
    assert licensing._subscription_confirmation_status(license_)["fresh"] is False


def test_future_issued_subscription_is_not_confirmed_for_a_month():
    now = datetime.now(UTC)
    license_ = licensing.parse_license(
        _token(issued_at=(now + timedelta(days=30)).isoformat(), subscription_id="sub"), _PUBLIC
    )
    assert licensing._subscription_confirmation_status(license_, now=now)["fresh"] is False


def test_explicit_replay_time_still_honored(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    settings = SimpleNamespace(data_dir=str(tmp_path))
    licensing._write_clock_watermark(settings, now + timedelta(days=3))
    monkeypatch.setenv(licensing.LICENSE_PUBLIC_KEY_ENV_VAR, _PUBLIC)
    monkeypatch.setenv(licensing.LICENSE_KEY_ENV_VAR, _token(expires_at=(now + timedelta(days=1)).isoformat()))
    monkeypatch.setattr(licensing, "_expected_device_binding", lambda _: (None, None))
    assert licensing.load_license(settings, now=now) is not None


def test_corrupt_revocation_file_cannot_be_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(licensing.LICENSE_PUBLIC_KEY_ENV_VAR, _PUBLIC)
    monkeypatch.setenv(licensing.LICENSE_KEY_ENV_VAR, _token())
    monkeypatch.delenv(licensing.LICENSE_REVOCATIONS_ENV_VAR, raising=False)
    (tmp_path / licensing.LICENSE_REVOCATIONS_FILE_NAME).write_bytes(b"\xff")
    assert licensing.load_license(SimpleNamespace(data_dir=str(tmp_path))) is None


def test_nonfinite_schema_is_license_error():
    with pytest.raises(licensing.LicenseError):
        licensing.parse_license(_token(schema=float("inf")), _PUBLIC)


def test_nonfinite_signed_device_proof_is_rejected(monkeypatch):
    monkeypatch.setenv(licensing.COMMERCIAL_RELEASE_ENV_VAR, "true")
    token = _token(
        device_id="dev",
        device_fingerprint="fp",
        activation={
            "source": "activation_server",
            "device_binding": {
                "fingerprint": "fp",
                "strength": "strong",
                "secret_storage": "dpapi",
                "hardware_signal_count": float("inf"),
            },
        },
    )
    with pytest.raises(licensing.LicenseError):
        licensing.verify_license(token, _PUBLIC)


def test_nonfinite_activation_device_proof_is_rejected():
    from app.commerce.activation_policy import ActivationError, ActivationPolicy, ActivationRequest

    request = ActivationRequest(
        activation_key="review-key",
        device_id="dev",
        device_fingerprint="fp_valid",
        nonce="review-nonce-123456",
        device_profile={
            "binding_strength": "strong",
            "secret_storage": "dpapi",
            "fingerprint": "fp_valid",
            "install_hash": "hash",
            "hardware_signal_count": float("inf"),
        },
    )
    with pytest.raises(ActivationError):
        ActivationPolicy(
            key_pepper="local",
            device_secret="local",  # noqa: S106 - local test fixture.
            require_strong_device_proof=True,  # noqa: S106 - local test fixture.
        ).prepare_activation(request)


def test_unicode_device_ids_do_not_crash_refresh_policy():
    from app.commerce.activation_policy import ActivationPolicy, ActivationRefreshRequest

    request = ActivationRefreshRequest(
        license_token="token",  # noqa: S106 - local test fixture.
        device_id="设备",
        device_fingerprint="fp_valid",
        nonce="review-nonce-123456",  # noqa: S106 - local test fixture.
    )
    prepared = ActivationPolicy().prepare_refresh(request, expected_device_id="设备")
    assert prepared.device.device_id == "设备"


def test_unicode_signed_device_mismatch_is_license_error():
    with pytest.raises(licensing.LicenseError) as error:
        licensing.verify_license(_token(device_id="设备"), _PUBLIC, expected_device_id="different")
    assert error.value.code == "license_device_mismatch"


def test_unicode_profile_fingerprint_is_validation_error():
    from app.commerce.activation_policy import ActivationError, ActivationPolicy, ActivationRequest

    with pytest.raises(ActivationError):
        ActivationPolicy().prepare_activation(
            ActivationRequest(
                activation_key="key",
                device_id="dev",
                device_fingerprint="fp_valid",
                nonce="review-nonce-123456",
                device_profile={"binding_strength": "strong", "fingerprint": "设备", "install_hash": "hash"},
            )
        )
