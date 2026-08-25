from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.release_owner_signature import (
    canonical_payload_bytes,
    create_signoff_payload,
    verify_release_owner_signature,
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signing_material() -> tuple[str, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"ed25519:{_b64url(public_key)}", private_key


def _payload(**overrides: str) -> dict[str, str]:
    values = {
        "repository": "example/lengrvis",
        "release_tag": "v0.1.2",
        "candidate_commit": "a" * 40,
        "candidate_run_id": "123",
        "candidate_run_attempt": "2",
        "reviewed_evidence_run_id": "456",
        "reviewed_evidence_run_attempt": "1",
        "build_identifier": f"rc-123-2-{'a' * 40}",
        "release_owner": "release-owner",
        "manual_signoff_status": "release_signoff_recorded",
    }
    values.update(overrides)
    return create_signoff_payload(**values)


def test_release_owner_signature_verifies_exact_candidate_binding() -> None:
    public_key, private_key = _signing_material()
    payload = _payload()
    signature = private_key.sign(canonical_payload_bytes(payload))

    evidence = verify_release_owner_signature(
        public_key_text=public_key,
        signature_text=f"ed25519:{_b64url(signature)}",
        payload=payload,
    )

    assert evidence["verified"] is True
    assert evidence["payload"] == payload
    assert evidence["payload_sha256"].startswith("sha256:")
    assert evidence["public_key_fingerprint"].startswith("sha256:")
    assert evidence["signature_sha256"].startswith("sha256:")


def test_release_owner_signature_rejects_changed_candidate_commit() -> None:
    public_key, private_key = _signing_material()
    original = _payload()
    signature = private_key.sign(canonical_payload_bytes(original))

    with pytest.raises(ValueError, match="verification failed"):
        verify_release_owner_signature(
            public_key_text=public_key,
            signature_text=f"ed25519:{_b64url(signature)}",
            payload=_payload(
                candidate_commit="b" * 40,
                build_identifier=f"rc-123-2-{'b' * 40}",
            ),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "../escape"),
        ("release_tag", "latest"),
        ("candidate_commit", "short"),
        ("candidate_run_id", "0"),
        ("candidate_run_attempt", "-1"),
        ("reviewed_evidence_run_id", "abc"),
        ("manual_signoff_status", "approved"),
        ("build_identifier", "rc-for-another-candidate"),
    ],
)
def test_release_owner_payload_rejects_unbound_or_malformed_identity(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        _payload(**{field: value})
