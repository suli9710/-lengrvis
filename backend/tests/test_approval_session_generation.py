from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.security.approval_session import (
    APPROVAL_SESSION_GENERATION_FILE,
    approval_session_generation_fingerprint,
    bind_approval_session_generation,
    current_approval_session_generation,
)
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_PUBLIC_KEY_ENV,
    create_native_confirmation_challenge,
    native_confirmation_public_key_fingerprint,
    require_native_confirmation,
)

GENERATION_A = "A" * 43
GENERATION_B = "B" * 43


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, raising=False)
    db.init_db(force=True)


def _write_generation(tmp_path, generation: str) -> None:
    (tmp_path / APPROVAL_SESSION_GENERATION_FILE).write_bytes(generation.encode("ascii") + b"\n")


def _public_key(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _signature(private_key: Ed25519PrivateKey, payload: str) -> str:
    raw = private_key.sign(payload.encode("utf-8"))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.mark.parametrize("raw", [GENERATION_A.encode("ascii"), GENERATION_A.encode("ascii") + b"\n"])
def test_generation_file_accepts_only_the_canonical_bytes(tmp_path, raw: bytes) -> None:
    (tmp_path / APPROVAL_SESSION_GENERATION_FILE).write_bytes(raw)

    assert current_approval_session_generation() == GENERATION_A


@pytest.mark.parametrize(
    "raw",
    [
        GENERATION_A.encode("ascii") + b"\r\n",
        b" " + GENERATION_A.encode("ascii"),
        GENERATION_A.encode("ascii") + b" ",
        b"A" * 42,
        b"A" * 44,
        (b"A" * 42) + b"=",
        (b"A" * 42) + b"\x80",
    ],
)
def test_generation_file_rejects_noncanonical_bytes(tmp_path, raw: bytes) -> None:
    (tmp_path / APPROVAL_SESSION_GENERATION_FILE).write_bytes(raw)

    with pytest.raises(RuntimeError, match="malformed"):
        current_approval_session_generation()


@pytest.mark.parametrize(
    "file_value",
    [
        None,
        "malformed-generation",
        GENERATION_A + (" " * 200),
        GENERATION_A + (" " * 100) + "trailing-data",
        GENERATION_A + "\ntrailing-data",
        GENERATION_A + "x",
    ],
)
def test_production_challenge_fails_closed_without_valid_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    file_value: str | None,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    if file_value is not None:
        _write_generation(tmp_path, file_value)

    with pytest.raises(HTTPException) as exc_info:
        create_native_confirmation_challenge(
            action="approve",
            endpoint="/api/approvals/approval-1/approve",
            approval_id="approval-1",
        )

    assert exc_info.value.status_code == 403
    assert "session generation" in str(exc_info.value.detail).lower()


def test_missing_generation_compatibility_requires_explicit_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "1")

    challenge = create_native_confirmation_challenge(
        action="approve",
        endpoint="/api/approvals/test-compat/approve",
        approval_id="test-compat",
    )

    assert challenge["confirmation_id"]
    assert "session_generation" not in json.dumps(challenge, sort_keys=True)


def test_signed_challenge_binds_generation_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key(private_key))
    _write_generation(tmp_path, GENERATION_A)
    challenge = create_native_confirmation_challenge(
        action="approve",
        endpoint="/api/approvals/approval-1/approve",
        approval_id="approval-1",
        preview_hmac="preview-binding",
    )

    serialized = json.dumps(challenge, sort_keys=True)
    assert GENERATION_A not in serialized
    assert "session_generation" not in serialized
    assert "auth_context" not in challenge

    signed_payload = bind_approval_session_generation(str(challenge["signing_payload"]), GENERATION_A)
    confirmation = require_native_confirmation(
        action="approve",
        endpoint="/api/approvals/approval-1/approve",
        approval_id="approval-1",
        confirmation_id=str(challenge["confirmation_id"]),
        timestamp=str(challenge["expires_at_epoch"]),
        signature=_signature(private_key, signed_payload),
        preview_hmac="preview-binding",
    )

    assert confirmation["approval_session_generation_fingerprint"] == approval_session_generation_fingerprint()
    assert "generation" not in confirmation


def test_rotation_invalidates_challenge_created_before_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key(private_key))
    _write_generation(tmp_path, GENERATION_A)
    challenge = create_native_confirmation_challenge(
        action="approve",
        endpoint="/api/approvals/approval-1/approve",
        approval_id="approval-1",
    )
    signed_payload = bind_approval_session_generation(str(challenge["signing_payload"]), GENERATION_A)
    _write_generation(tmp_path, GENERATION_B)

    with pytest.raises(HTTPException) as exc_info:
        require_native_confirmation(
            action="approve",
            endpoint="/api/approvals/approval-1/approve",
            approval_id="approval-1",
            confirmation_id=str(challenge["confirmation_id"]),
            timestamp=str(challenge["expires_at_epoch"]),
            signature=_signature(private_key, signed_payload),
        )

    assert exc_info.value.status_code == 403
    assert "session changed" in str(exc_info.value.detail).lower()


def test_final_claim_expires_old_authorization_when_rotation_precedes_claim_linearization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key")
    _write_generation(tmp_path, GENERATION_A)
    confirmed_at_epoch = int(time.time())
    approval = Approval(
        task_id="task-session-rotation",
        message="Approve before screen lock",
        status=ApprovalStatus.APPROVED,
        authorized_at=datetime.fromtimestamp(confirmed_at_epoch, UTC).isoformat(),
        auth_context={
            "channel": "desktop_native",
            "proof_type": "ed25519",
            "confirmation_id": "confirmation-before-lock",
            "confirmed_at_epoch": confirmed_at_epoch,
            "challenge_expires_at_epoch": confirmed_at_epoch + 60,
            "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
            "approval_session_generation_fingerprint": approval_session_generation_fingerprint(),
        },
    )
    db.upsert_model("approvals", approval, status=approval.status)
    _write_generation(tmp_path, GENERATION_B)

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert stored["consumed_at"] is None
    assert "session has changed" in stored["expired_reason"].lower()


def test_rotation_does_not_retroactively_revoke_an_already_linearized_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key")
    _write_generation(tmp_path, GENERATION_A)
    confirmed_at_epoch = int(time.time())
    approval = Approval(
        task_id="task-session-in-flight-residual",
        message="Claim immediately before screen lock",
        status=ApprovalStatus.APPROVED,
        authorized_at=datetime.fromtimestamp(confirmed_at_epoch, UTC).isoformat(),
        auth_context={
            "channel": "desktop_native",
            "proof_type": "ed25519",
            "confirmation_id": "confirmation-before-linearization",
            "confirmed_at_epoch": confirmed_at_epoch,
            "challenge_expires_at_epoch": confirmed_at_epoch + 60,
            "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
            "approval_session_generation_fingerprint": approval_session_generation_fingerprint(),
        },
    )
    db.upsert_model("approvals", approval, status=approval.status)

    claimed = db.claim_approval_for_execution(approval.id, now_iso())
    assert claimed is not None
    assert claimed["consumed_at"] is not None

    _write_generation(tmp_path, GENERATION_B)

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.APPROVED.value
    assert stored["consumed_at"] == claimed["consumed_at"]


@pytest.mark.parametrize(
    ("session_binding", "reason"),
    [
        (None, "session binding is missing"),
        ("not-a-fingerprint", "session binding is malformed"),
    ],
)
def test_production_auth_context_with_invalid_generation_binding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    session_binding: str | None,
    reason: str,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key")
    _write_generation(tmp_path, GENERATION_A)
    confirmed_at_epoch = int(time.time())
    auth_context = {
        "channel": "desktop_native",
        "proof_type": "ed25519",
        "confirmation_id": "confirmation-without-session",
        "confirmed_at_epoch": confirmed_at_epoch,
        "challenge_expires_at_epoch": confirmed_at_epoch + 60,
        "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
    }
    if session_binding is not None:
        auth_context["approval_session_generation_fingerprint"] = session_binding
    approval = Approval(
        task_id="task-missing-session-binding",
        message="Malformed desktop authorization",
        status=ApprovalStatus.APPROVED,
        authorized_at=datetime.fromtimestamp(confirmed_at_epoch, UTC).isoformat(),
        auth_context=auth_context,
    )
    db.upsert_model("approvals", approval, status=approval.status)

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert reason in stored["expired_reason"].lower()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("stale", "session has changed"),
        ("missing", "session binding is missing"),
    ],
)
def test_reauthorization_atomically_expires_invalid_session_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mutation: str,
    reason: str,
) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key")
    _write_generation(tmp_path, GENERATION_A)
    original_epoch = int(time.time())
    original_context = {
        "channel": "desktop_native",
        "proof_type": "ed25519",
        "confirmation_id": "original-confirmation",
        "confirmed_at_epoch": original_epoch,
        "challenge_expires_at_epoch": original_epoch + 60,
        "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
        "approval_session_generation_fingerprint": approval_session_generation_fingerprint(),
    }
    approval = Approval(
        task_id=f"task-reauthorize-{mutation}",
        message="Reauthorize after desktop session transition",
        status=ApprovalStatus.APPROVED,
        authorized_at=datetime.fromtimestamp(original_epoch, UTC).isoformat(),
        auth_context=original_context,
    )
    db.upsert_model("approvals", approval, status=approval.status)

    new_epoch = original_epoch + 1
    new_context = dict(original_context)
    new_context["confirmation_id"] = "replacement-confirmation"
    new_context["confirmed_at_epoch"] = new_epoch
    new_context["challenge_expires_at_epoch"] = new_epoch + 60
    if mutation == "stale":
        _write_generation(tmp_path, GENERATION_B)
    else:
        new_context.pop("approval_session_generation_fingerprint")

    rebound = db.reauthorize_approval_atomically(
        approval.id,
        datetime.fromtimestamp(new_epoch, UTC).isoformat(),
        new_context,
    )

    assert rebound is not None
    assert rebound["status"] == ApprovalStatus.EXPIRED.value
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert stored["consumed_at"] is None
    assert reason in stored["expired_reason"].lower()
