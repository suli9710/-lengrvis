from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.security.relay_types import RelayEnvelope, TlsPinRecord, validate_tls_pin_set


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _relay(**overrides: object) -> RelayEnvelope:
    ciphertext = b"opaque encrypted preview task"
    values: dict[str, object] = {
        "envelope_id": "relay_12345678",
        "account_id": "account_12345678",
        "sender_device_id": "device_mobile_1",
        "recipient_device_id": "device_desktop_1",
        "payload_type": "template_start",
        "key_id": "device-key-1",
        "sequence": 1,
        "idempotency_key": "idem_1234567890123456",
        "nonce": _encoded(b"0123456789ab"),
        "ciphertext": _encoded(ciphertext),
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "aad_sha256": "a" * 64,
        "created_at": "2026-07-11T00:00:00Z",
        "expires_at": "2026-07-12T00:00:00Z",
    }
    values.update(overrides)
    return RelayEnvelope.model_validate(values)


def _pin(pin_id: str, fingerprint: str, **overrides: object) -> TlsPinRecord:
    values: dict[str, object] = {
        "pin_id": pin_id,
        "origin": "https://EXAMPLE.TEST:8443/",
        "host": "example.test.",
        "fingerprint_sha256": fingerprint,
        "status": "active",
        "created_at": "2026-07-11T00:00:00Z",
        "expires_at": "2026-08-11T00:00:00Z",
        "source_device_id": "device_desktop_1",
    }
    values.update(overrides)
    return TlsPinRecord.model_validate(values)


def test_relay_envelope_is_ciphertext_only_and_limited_to_preview_actions() -> None:
    envelope = _relay()

    assert envelope.schema_version == "relay-envelope-v1"
    assert envelope.payload_type == "template_start"
    assert envelope.model_dump()["ciphertext"] != "opaque encrypted preview task"

    with pytest.raises(ValidationError):
        _relay(payload_type="approval_decision")
    with pytest.raises(ValidationError):
        RelayEnvelope.model_validate({**envelope.model_dump(), "plaintext": "must never reach relay"})


def test_relay_envelope_rejects_long_queue_ttl_and_tampered_ciphertext() -> None:
    with pytest.raises(ValidationError, match="24 hours"):
        _relay(expires_at="2026-07-12T00:00:01Z")

    with pytest.raises(ValidationError, match="digest does not match"):
        _relay(ciphertext=_encoded(b"tampered ciphertext"))

    with pytest.raises(ValidationError, match="more than 5 minutes in the future"):
        _relay(
            created_at="2099-01-01T00:00:00Z",
            expires_at="2099-01-02T00:00:00Z",
        )


def test_relay_envelope_expiration_is_deterministic() -> None:
    envelope = _relay()

    assert not envelope.is_expired(at=datetime(2026, 7, 11, 23, 59, tzinfo=UTC))
    assert envelope.is_expired(at=datetime(2026, 7, 12, 0, 0, tzinfo=UTC))


def test_tls_pin_record_normalizes_exact_https_origin_and_fingerprint() -> None:
    fingerprint = ":".join(["AB"] * 32)
    pin = _pin("pin_12345678", fingerprint)

    assert pin.origin == "https://example.test:8443"
    assert pin.host == "example.test"
    assert pin.fingerprint_sha256 == "ab" * 32
    assert pin.is_usable(at=datetime(2026, 7, 12, tzinfo=UTC))


def test_tls_pin_record_supports_canonical_ipv6_origins() -> None:
    pin = _pin(
        "pin_ipv6_001",
        "c" * 64,
        origin="https://[2001:0db8::1]:8443/",
        host="2001:0db8::1",
    )

    assert pin.origin == "https://[2001:db8::1]:8443"
    assert pin.host == "2001:db8::1"


def test_tls_pin_record_rejects_cross_host_and_incomplete_revocation() -> None:
    with pytest.raises(ValidationError, match="origin and host"):
        _pin("pin_12345678", "a" * 64, host="other.test")
    with pytest.raises(ValidationError, match="require revoked_at"):
        _pin("pin_12345678", "a" * 64, status="revoked")


def test_tls_pin_set_supports_one_overlapping_next_pin_and_fails_closed_on_ambiguity() -> None:
    now = datetime(2026, 7, 11, 12, tzinfo=UTC)
    active = _pin("pin_active_1", "a" * 64)
    next_pin = _pin("pin_next_001", "b" * 64, status="next", expires_at="2026-07-12T00:00:00Z")

    selected = validate_tls_pin_set([next_pin, active], origin="https://example.test:8443", at=now)

    assert [record.status for record in selected] == ["active", "next"]

    third = _pin("pin_next_002", "c" * 64, status="next", expires_at="2026-07-12T00:00:00Z")
    with pytest.raises(ValueError, match="unexpected number"):
        validate_tls_pin_set([active, next_pin, third], origin=active.origin, at=now)

    expired_active = _pin("pin_expired_1", "d" * 64, expires_at="2026-07-11T12:00:00Z")
    with pytest.raises(ValueError, match="no active pin"):
        validate_tls_pin_set([expired_active, next_pin], origin=active.origin, at=now)


def test_tls_pin_record_rejects_long_lived_rotation_pin() -> None:
    with pytest.raises(ValidationError, match="status limit"):
        _pin("pin_next_long", "e" * 64, status="next")


def test_tls_pin_record_rejects_far_future_creation() -> None:
    with pytest.raises(ValidationError, match="more than 5 minutes in the future"):
        _pin(
            "pin_future_01",
            "f" * 64,
            created_at="2099-01-01T00:00:00Z",
            expires_at="2099-01-02T00:00:00Z",
        )
