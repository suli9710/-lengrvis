from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.policy.risk import RiskLevel
from app.security.mobile_identity import create_mobile_session
from app.security.mobile_jwt import TOKEN_SCOPE, decode_mobile_token
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_PUBLIC_KEY_ENV,
    native_confirmation_public_key_fingerprint,
)
from app.services import mobile_pairing_service
from app.services.approval_event_service import _safe_approval as safe_approval_event
from app.services.mobile_pairing_access import _mobile_approval_requires_step_up
from app.services.mobile_pairing_payloads import safe_approval_payload


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)


def _desktop_authorization() -> tuple[str, dict[str, object]]:
    confirmed_at_epoch = int(time.time())
    authorized_at = datetime.fromtimestamp(confirmed_at_epoch, UTC).isoformat()
    return authorized_at, {
        "channel": "desktop_native",
        "proof_type": "ed25519",
        "confirmation_id": "confirmation-test",
        "confirmed_at_epoch": confirmed_at_epoch,
        "challenge_expires_at_epoch": confirmed_at_epoch + 60,
        "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
    }


def _approved(auth_context: dict[str, object], authorized_at: str, **kwargs) -> Approval:
    approval = Approval(
        task_id="task-auth-context",
        message="Approve authenticated action",
        status=ApprovalStatus.APPROVED,
        authorized_at=authorized_at,
        auth_context=auth_context,
        **kwargs,
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def _mobile_session() -> tuple[str, dict[str, object]]:
    device_id = mobile_pairing_service.new_device_id()
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name="Approval Phone")
    session = create_mobile_session(device_id=device_id, device_name="Approval Phone", scopes=[TOKEN_SCOPE])
    return device_id, decode_mobile_token(session.token)


def _trusted_biometric_claims(
    device_id: str,
    claims: dict[str, object],
    *,
    method: str = "biometric",
    expires_offset_seconds: int = 60,
) -> dict[str, object]:
    timestamp = int(time.time())
    verified_at = timestamp if expires_offset_seconds > 0 else timestamp - 60
    thumbprint = "trusted-biometric-thumbprint"
    trusted = {
        **claims,
        "amr": [method],
        "cnf": {"jkt": thumbprint},
        "step_up": {
            "method": method,
            "verified_at": verified_at,
            "expires_at": timestamp + expires_offset_seconds,
        },
    }
    with db.connect() as conn:
        row = conn.execute(
            "SELECT data FROM device_credentials WHERE id = ?",
            (claims["credential_id"],),
        ).fetchone()
        credential = json.loads(row["data"])
        credential.update(
            {
                "device_id": device_id,
                "hardware_backed": True,
                "attestation_verified": True,
                "public_key_thumbprint": thumbprint,
            }
        )
        conn.execute(
            "UPDATE device_credentials SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(credential, ensure_ascii=False), now_iso(), claims["credential_id"]),
        )
    return trusted


def _pending_mobile_r3(device_id: str) -> Approval:
    approval = Approval(
        task_id="task-mobile-r3",
        message="Approve destructive action",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[TOKEN_SCOPE],
    )
    db.upsert_model("approvals", approval)
    return approval


def test_mobile_step_up_uses_effective_risk_provenance() -> None:
    assert _mobile_approval_requires_step_up(
        {
            "engineering_boundary": {
                "risk_provenance": {
                    "version": "effective-risk/v1",
                    "declared_risk_level": RiskLevel.R1_OPEN_ONLY.value,
                    "effective_risk_level": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
                    "review_id": "review_00000000000000000000000000000000",
                }
            }
        }
    )


def test_desktop_key_rotation_expires_unconsumed_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key-a")
    authorized_at, auth_context = _desktop_authorization()
    approval = _approved(auth_context, authorized_at)

    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key-b")
    claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert claimed is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert stored["consumed_at"] is None
    assert "key has changed" in stored["expired_reason"].lower()


def test_desktop_reauthorization_rebinds_to_current_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key-a")
    authorized_at, auth_context = _desktop_authorization()
    approval = _approved(auth_context, authorized_at)

    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key-b")
    new_authorized_at, new_auth_context = _desktop_authorization()
    rebound = db.reauthorize_approval_atomically(approval.id, new_authorized_at, new_auth_context)
    claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert rebound is not None
    assert rebound["auth_context"]["public_key_fingerprint"] == native_confirmation_public_key_fingerprint()
    assert claimed is not None
    assert claimed["consumed_at"]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("device", "device has been revoked"),
        ("family", "family has been revoked"),
        ("credential", "credential has been revoked"),
        ("epoch", "session has been revoked"),
        ("generation", "family generation has changed"),
        ("family_expired", "family has expired"),
    ],
)
def test_mobile_identity_change_expires_unconsumed_approval(mutation: str, reason: str) -> None:
    device_id, claims = _mobile_session()
    authorized_at = now_iso()
    approval = _approved(
        mobile_pairing_service.mobile_approval_auth_context(claims),
        authorized_at,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[TOKEN_SCOPE],
    )

    with db.connect() as conn:
        if mutation in {"device", "epoch"}:
            row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
            device = json.loads(row["data"])
            if mutation == "device":
                device["status"] = "revoked"
            else:
                device["token_epoch"] = int(device.get("token_epoch") or 0) + 1
            conn.execute(
                "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(device, ensure_ascii=False), now_iso(), device_id),
            )
        elif mutation == "family":
            conn.execute("UPDATE token_families SET status = 'revoked' WHERE id = ?", (claims["family_id"],))
        elif mutation == "credential":
            conn.execute(
                "UPDATE device_credentials SET status = 'revoked' WHERE id = ?",
                (claims["credential_id"],),
            )
        elif mutation == "generation":
            conn.execute(
                "UPDATE token_families SET current_generation = current_generation + 1 WHERE id = ?",
                (claims["family_id"],),
            )
        else:
            expired_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            conn.execute("UPDATE token_families SET expires_at = ? WHERE id = ?", (expired_at, claims["family_id"]))

    claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert claimed is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert reason in stored["expired_reason"].lower()


def test_mobile_decision_persists_device_bound_auth_context(monkeypatch: pytest.MonkeyPatch) -> None:
    device_id, claims = _mobile_session()
    approval = Approval(
        task_id="task-mobile-decision",
        message="Approve from mobile",
        allowed_device_ids=[device_id],
        required_mobile_scopes=[TOKEN_SCOPE],
    )
    db.upsert_model("approvals", approval)
    monkeypatch.setattr(mobile_pairing_service, "_approval_state_error", lambda _approval_id: "")

    decided = mobile_pairing_service.approve_approval(approval.id, claims)
    claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert decided.authorized_at
    assert decided.auth_context["channel"] == "mobile"
    assert decided.auth_context["device_id"] == device_id
    assert decided.auth_context["token_family_id"] == claims["family_id"]
    assert decided.auth_context["family_generation"] == claims["family_generation"]
    assert decided.auth_context["credential_id"] == claims["credential_id"]
    assert claimed is not None


def test_mobile_r3_decision_and_claim_revalidate_valid_biometric_in_each_transaction() -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(device_id, raw_claims)
    approval = _pending_mobile_r3(device_id)
    decided_at = now_iso()

    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=mobile_pairing_service.mobile_approval_auth_context(claims),
    )
    claimed = db.claim_approval_for_execution(approval.id, now_iso())

    assert decided is not None
    assert decided["status"] == ApprovalStatus.APPROVED.value
    assert decided["auth_context"]["step_up_method"] == "biometric"
    assert decided["auth_context"]["proof_thumbprint"] == "trusted-biometric-thumbprint"
    assert claimed is not None
    assert claimed["consumed_at"]


@pytest.mark.parametrize(
    ("method", "expires_offset_seconds", "reason"),
    [
        ("password", 60, "biometric authentication method"),
        ("biometric", -1, "biometric step-up has expired"),
    ],
)
def test_mobile_r3_decision_rejects_wrong_or_expired_biometric_inside_transaction(
    method: str,
    expires_offset_seconds: int,
    reason: str,
) -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(
        device_id,
        raw_claims,
        method=method,
        expires_offset_seconds=expires_offset_seconds,
    )
    approval = _pending_mobile_r3(device_id)
    decided_at = now_iso()

    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=mobile_pairing_service.mobile_approval_auth_context(claims),
    )

    assert decided is not None
    assert decided["status"] == ApprovalStatus.EXPIRED.value
    assert reason in decided["expired_reason"].lower()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("amr", "biometric authentication method"),
        ("verified_future", "timestamps are invalid"),
        ("oversized_lifetime", "lifetime exceeds"),
        ("proof_thumbprint", "proof thumbprint"),
        ("hardware_backed", "not hardware-backed"),
        ("attestation_verified", "attestation is not verified"),
    ],
)
def test_mobile_r3_decision_revalidates_complete_biometric_binding_inside_transaction(
    mutation: str,
    reason: str,
) -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(device_id, raw_claims)
    approval = _pending_mobile_r3(device_id)
    decided_at = now_iso()
    auth_context = mobile_pairing_service.mobile_approval_auth_context(claims)
    if mutation == "amr":
        auth_context["authentication_methods"] = ["password"]
    elif mutation == "verified_future":
        verified_at = int(datetime.now(UTC).timestamp()) + 60
        auth_context["step_up_verified_at"] = verified_at
        auth_context["step_up_expires_at"] = verified_at + 60
    elif mutation == "oversized_lifetime":
        auth_context["step_up_expires_at"] = int(auth_context["step_up_verified_at"]) + 901
    elif mutation == "proof_thumbprint":
        auth_context["proof_thumbprint"] = "different-thumbprint"
    else:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT data FROM device_credentials WHERE id = ?",
                (claims["credential_id"],),
            ).fetchone()
            credential = json.loads(row["data"])
            credential[mutation] = False
            conn.execute(
                "UPDATE device_credentials SET data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(credential, ensure_ascii=False), now_iso(), claims["credential_id"]),
            )

    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=auth_context,
    )

    assert decided is not None
    assert decided["status"] == ApprovalStatus.EXPIRED.value
    assert reason in decided["expired_reason"].lower()


def test_mobile_r3_decision_rechecks_credential_after_service_precheck() -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(device_id, raw_claims)
    approval = _pending_mobile_r3(device_id)
    mobile_pairing_service._raise_if_mobile_claims_disallowed(approval.model_dump(mode="json"), claims)
    with db.connect() as conn:
        conn.execute(
            "UPDATE device_credentials SET status = 'revoked' WHERE id = ?",
            (claims["credential_id"],),
        )
    decided_at = now_iso()

    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=mobile_pairing_service.mobile_approval_auth_context(claims),
    )

    assert decided is not None
    assert decided["status"] == ApprovalStatus.EXPIRED.value
    assert "credential has been revoked" in decided["expired_reason"].lower()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("hardware_backed", "not hardware-backed"),
        ("attestation_verified", "attestation is not verified"),
        ("public_key_thumbprint", "proof thumbprint"),
        ("status", "credential has been revoked"),
    ],
)
def test_mobile_r3_claim_rechecks_current_biometric_credential_state(mutation: str, reason: str) -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(device_id, raw_claims)
    approval = _pending_mobile_r3(device_id)
    decided_at = now_iso()
    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=mobile_pairing_service.mobile_approval_auth_context(claims),
    )
    assert decided is not None and decided["status"] == ApprovalStatus.APPROVED.value
    with db.connect() as conn:
        if mutation == "status":
            conn.execute(
                "UPDATE device_credentials SET status = 'revoked' WHERE id = ?",
                (claims["credential_id"],),
            )
        else:
            row = conn.execute(
                "SELECT data FROM device_credentials WHERE id = ?",
                (claims["credential_id"],),
            ).fetchone()
            credential = json.loads(row["data"])
            credential[mutation] = "rotated-thumbprint" if mutation == "public_key_thumbprint" else False
            conn.execute(
                "UPDATE device_credentials SET data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(credential, ensure_ascii=False), now_iso(), claims["credential_id"]),
            )

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert reason in stored["expired_reason"].lower()


def test_mobile_r3_claim_uses_consumed_at_for_biometric_expiry() -> None:
    device_id, raw_claims = _mobile_session()
    claims = _trusted_biometric_claims(device_id, raw_claims)
    approval = _pending_mobile_r3(device_id)
    decided_at = now_iso()
    decided = db.decide_approval_atomically(
        approval.id,
        ApprovalStatus.APPROVED.value,
        decided_at,
        authorized_at=decided_at,
        auth_context=mobile_pairing_service.mobile_approval_auth_context(claims),
    )
    assert decided is not None and decided["status"] == ApprovalStatus.APPROVED.value
    consumed_at = datetime.fromtimestamp(int(claims["step_up"]["expires_at"]), UTC).isoformat()

    assert db.claim_approval_for_execution(approval.id, consumed_at) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert "biometric step-up has expired" in stored["expired_reason"].lower()


def test_mobile_approval_without_family_generation_fails_closed_outside_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_id, claims = _mobile_session()
    auth_context = mobile_pairing_service.mobile_approval_auth_context(claims)
    auth_context.pop("family_generation")
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    approval = _approved(
        auth_context,
        now_iso(),
        allowed_device_ids=[device_id],
        required_mobile_scopes=[TOKEN_SCOPE],
    )

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert "family generation is invalid" in stored["expired_reason"].lower()


@pytest.mark.parametrize(
    ("authorized_at", "auth_context", "reason"),
    [
        (now_iso(), {}, "context is incomplete"),
        (now_iso(), {"channel": "unknown"}, "channel is invalid"),
    ],
)
def test_malformed_auth_context_fails_closed(
    authorized_at: str,
    auth_context: dict[str, object],
    reason: str,
) -> None:
    approval = _approved(auth_context, authorized_at)

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert reason in stored["expired_reason"].lower()


def test_legacy_approved_record_fails_closed_outside_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    approval = Approval(
        task_id="task-legacy-approval",
        message="Legacy approval",
        status=ApprovalStatus.APPROVED,
    )
    db.upsert_model("approvals", approval, status=approval.status)

    assert db.claim_approval_for_execution(approval.id, now_iso()) is None
    stored = db.fetch_one("approvals", approval.id)
    assert stored["status"] == ApprovalStatus.EXPIRED.value
    assert "missing an authentication context" in stored["expired_reason"].lower()


def test_public_approval_payload_omits_auth_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, "desktop-key-a")
    authorized_at, auth_context = _desktop_authorization()
    auth_context["approval_session_generation_fingerprint"] = "c" * 64
    approval = _approved(auth_context, authorized_at)

    payload = safe_approval_payload(approval)
    event_payload = safe_approval_event(approval)

    assert payload["authorized_at"] == authorized_at
    assert "auth_context" not in payload
    assert "auth_context" not in event_payload
    assert auth_context["approval_session_generation_fingerprint"] not in json.dumps(payload, sort_keys=True)
    assert auth_context["approval_session_generation_fingerprint"] not in json.dumps(event_payload, sort_keys=True)
