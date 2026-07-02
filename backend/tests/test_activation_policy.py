from __future__ import annotations

import json

import pytest

from app.commerce.activation import (
    ActivationError as LegacyActivationError,
)
from app.commerce.activation import (
    ActivationRequest as LegacyActivationRequest,
)
from app.commerce.activation_policy import (
    ActivationError,
    ActivationPolicy,
    ActivationRefreshRequest,
    ActivationRequest,
)

VALID_NONCE = "nonce-value-long-enough"


def test_prepare_activation_concentrates_normalization_and_device_derivation() -> None:
    policy = ActivationPolicy(key_pepper="test-pepper", device_secret="test-device-secret")  # noqa: S106

    prepared = policy.prepare_activation(
        ActivationRequest(
            activation_key="  key-one  ",
            device_id="  device-one  ",
            device_fingerprint="  fp_one  ",
            device_profile={
                "fingerprint": "fp_one",
                "binding_strength": "strong",
                "secret_storage": "dpapi",
                "hardware_signal_count": 1,
                "install_hash": "install-hash",
                "machine_id_hash": "machine-hash",
                "signals": ["machine_id_hash"],
                "device_name": "must-not-leak",
            },
            app_version="  desktop  ",
            nonce=f"  {VALID_NONCE}  ",
        )
    )

    assert prepared.activation_key == "key-one"
    assert prepared.device.device_id == "device-one"
    assert prepared.device.fingerprint == "fp_one"
    assert prepared.app_version == "desktop"
    assert prepared.nonce == VALID_NONCE
    assert prepared.server_device_ref.startswith("sdev_")
    assert prepared.license_id.startswith("lic_")
    assert json.loads(prepared.device.profile_json) == {
        "binding_strength": "strong",
        "fingerprint": "fp_one",
        "hardware_signal_count": 1,
        "install_hash": "install-hash",
        "machine_id_hash": "machine-hash",
        "secret_storage": "dpapi",
        "signals": ["machine_id_hash"],
    }
    assert prepared.device.binding_claim() == {
        "strength": "strong",
        "secret_storage": "dpapi",
        "hardware_signal_count": 1,
        "fingerprint": "fp_one",
    }


@pytest.mark.parametrize(
    ("profile", "fingerprint", "expected_code"),
    [
        (
            {"binding_strength": "install_only", "secret_storage": "plaintext"},
            "fp_weak",
            "activation_device_proof_weak",
        ),
        (
            {
                "fingerprint": "fp_other",
                "binding_strength": "strong",
                "secret_storage": "dpapi",
                "hardware_signal_count": 1,
                "install_hash": "install-hash",
                "machine_id_hash": "machine-hash",
                "signals": ["machine_id_hash"],
            },
            "fp_actual",
            "activation_device_profile_mismatch",
        ),
    ],
)
def test_prepare_activation_enforces_strong_device_proof(
    profile: dict[str, object],
    fingerprint: str,
    expected_code: str,
) -> None:
    policy = ActivationPolicy(
        key_pepper="test-pepper",
        device_secret="test-device-secret",  # noqa: S106
        require_strong_device_proof=True,
    )

    with pytest.raises(ActivationError) as excinfo:
        policy.prepare_activation(
            ActivationRequest(
                activation_key="key-one",
                device_id="device-one",
                device_fingerprint=fingerprint,
                device_profile=profile,
                nonce=VALID_NONCE,
            )
        )

    assert excinfo.value.code == expected_code


def test_prepare_refresh_resolves_and_validates_persisted_binding() -> None:
    policy = ActivationPolicy(device_secret="test-device-secret")  # noqa: S106
    prepared = policy.prepare_refresh(
        ActivationRefreshRequest(
            license_token="license-token",  # noqa: S106
            device_id="device-one",
            device_fingerprint="fp_one",
            nonce=VALID_NONCE,
        )
    )

    resolved = prepared.resolve_binding(
        key_hash="a" * 64,
        stored_fingerprint="fp_one",
        stored_server_device_ref="",
        license_fingerprint="",
    )

    assert resolved.fingerprint == "fp_one"
    assert resolved.server_device_ref.startswith("sdev_")

    with pytest.raises(ActivationError) as excinfo:
        prepared.resolve_binding(
            key_hash="a" * 64,
            stored_fingerprint="fp_other",
            stored_server_device_ref="",
            license_fingerprint="",
        )
    assert excinfo.value.code == "activation_device_fingerprint_mismatch"


def test_legacy_activation_imports_reexport_policy_types() -> None:
    assert LegacyActivationError is ActivationError
    assert LegacyActivationRequest is ActivationRequest
