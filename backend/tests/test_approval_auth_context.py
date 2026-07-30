from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.security.mobile_identity import create_mobile_session
from app.security.mobile_jwt import TOKEN_SCOPE, decode_mobile_token
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_PUBLIC_KEY_ENV,
    native_confirmation_public_key_fingerprint,
)
from app.services import mobile_pairing_service
from app.services.approval_event_service import _safe_approval as safe_approval_event
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
