from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from fastapi import HTTPException

from app.commerce.entitlements import Feature, active_plan, has_feature
from app.commerce.licensing import subscription_confirmation_fresh_for_high_risk
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Task, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.execution_stage import ExecutionStage
from app.security.mobile_identity import (
    create_mobile_session_locked,
    revoke_device_credentials_locked,
    revoke_device_token_families_locked,
    rotate_mobile_refresh_token,
)
from app.security.mobile_jwt import (
    MOBILE_REMOTE_VIEW_TTL_SECONDS,
    MOBILE_TOKEN_TTL_SECONDS,
    REMOTE_INPUT_SCOPE,
    REMOTE_VIEW_SCOPE,
    TOKEN_SCOPE,
    decode_mobile_token,
    issue_mobile_token,
    new_device_id,
)
from app.services import mobile_pairing_access as _access_helpers
from app.services import mobile_pairing_common as _common_helpers
from app.services import mobile_pairing_payloads as _payload_helpers
from app.services import mobile_pairing_remote_input as _remote_input_helpers
from app.services import mobile_pairing_transport as _transport_helpers

_approval_allowed_device_ids = _access_helpers._approval_allowed_device_ids
_approval_required_mobile_scopes = _access_helpers._approval_required_mobile_scopes
_approval_source = _access_helpers._approval_source
_approval_source_grant_id = _access_helpers._approval_source_grant_id
_is_remote_input_approval = _access_helpers._is_remote_input_approval
_mobile_approval_approve_denial_reason = _access_helpers._mobile_approval_approve_denial_reason
_mobile_approval_denial_reason = _access_helpers._mobile_approval_denial_reason
_mobile_approval_read_denial_reason = _access_helpers._mobile_approval_read_denial_reason
_mobile_approval_reject_denial_reason = _access_helpers._mobile_approval_reject_denial_reason
_mobile_claims_allow_approval = _access_helpers._mobile_claims_allow_approval
_mobile_claims_allow_approval_for_read = _access_helpers._mobile_claims_allow_approval_for_read
_paired_mobile_claims_can_read_remote_input_approval = (
    _access_helpers._paired_mobile_claims_can_read_remote_input_approval
)
_paired_mobile_claims_can_reject_remote_input_approval = (
    _access_helpers._paired_mobile_claims_can_reject_remote_input_approval
)
_raise_if_mobile_claims_disallowed = _access_helpers._raise_if_mobile_claims_disallowed
_raise_if_mobile_claims_disallowed_for_read = _access_helpers._raise_if_mobile_claims_disallowed_for_read
_raise_if_mobile_claims_disallowed_for_reject = _access_helpers._raise_if_mobile_claims_disallowed_for_reject
_remote_input_approval_audit_payload = _access_helpers._remote_input_approval_audit_payload
_remote_input_grant_cannot_self_approve = _access_helpers._remote_input_grant_cannot_self_approve
_remote_input_source_device_id_from_audit = _access_helpers._remote_input_source_device_id_from_audit
_remote_input_source_grant_id_from_audit = _access_helpers._remote_input_source_grant_id_from_audit
is_mobile_device_active = _access_helpers.is_mobile_device_active
mobile_claims_can_access_approval = _access_helpers.mobile_claims_can_access_approval
raise_if_mobile_claims_disallowed = _access_helpers.raise_if_mobile_claims_disallowed

_text = _common_helpers._text
_text_list = _common_helpers._text_list
mobile_device_trust_metadata = _common_helpers.mobile_device_trust_metadata

_latest_plan = _payload_helpers._latest_plan
_remote_input_public_binding_state = _payload_helpers._remote_input_public_binding_state
_safe_approval_payload = _payload_helpers._safe_approval_payload
_safe_mobile_device_payload = _payload_helpers._safe_mobile_device_payload
_safe_mobile_device_trust = _payload_helpers._safe_mobile_device_trust
_safe_mobile_model_action = _payload_helpers._safe_mobile_model_action
_safe_mobile_model_action_args = _payload_helpers._safe_mobile_model_action_args
_safe_mobile_plan = _payload_helpers._safe_mobile_plan
_safe_mobile_public_value = _payload_helpers._safe_mobile_public_value
_safe_mobile_risk_level = _payload_helpers._safe_mobile_risk_level
_safe_mobile_task = _payload_helpers._safe_mobile_task
_safe_mobile_task_metadata = _payload_helpers._safe_mobile_task_metadata
_safe_mobile_text = _payload_helpers._safe_mobile_text
_safe_remote_input_grants = _payload_helpers._safe_remote_input_grants
safe_approval_payload = _payload_helpers.safe_approval_payload

REMOTE_INPUT_GRANT_TTL_SECONDS = _remote_input_helpers.REMOTE_INPUT_GRANT_TTL_SECONDS
_grant_expires_at = _remote_input_helpers._grant_expires_at
_normalized_remote_input_grants = _remote_input_helpers._normalized_remote_input_grants
_remote_input_grant_ttl = _remote_input_helpers._remote_input_grant_ttl
_revoked_remote_input_grants = _remote_input_helpers._revoked_remote_input_grants
_safe_remote_input_grant = _remote_input_helpers._safe_remote_input_grant

_backend_port = _transport_helpers._backend_port
_certificate_fingerprint_sha256 = _transport_helpers._certificate_fingerprint_sha256
_configured_lan_origin = _transport_helpers._configured_lan_origin
_iso = _transport_helpers._iso
_lan_ip = _transport_helpers._lan_ip
_parse_iso = _transport_helpers._parse_iso
_safe_tls_error = _transport_helpers._safe_tls_error
_validate_lan_tls_material = _transport_helpers._validate_lan_tls_material


def _server_info(transport: dict[str, Any] | None = None) -> dict[str, Any]:
    transport = transport or lan_transport_security()
    return {
        "host": _lan_ip(),
        "port": _backend_port(),
        "scheme": transport["scheme"],
        "origin": transport["origin"],
        "transport_security": transport,
    }


def lan_transport_security(settings: Any | None = None) -> dict[str, Any]:
    return _transport_helpers.lan_transport_security(settings or get_effective_settings())

PAIR_CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = MOBILE_TOKEN_TTL_SECONDS
# 64-bit pairing-code entropy (16 hex chars). The code lives for PAIR_CODE_TTL_SECONDS
# and is single-use, so 2**64 makes online brute force over the LAN infeasible even
# when a distributed attacker spreads guesses across many source IPs.
PAIR_CODE_HEX_LENGTH = 8
PAIR_CLAIM_SECRET_BYTES = 32
PAIR_CONFIRM_FAILURE_LIMIT = 8
PAIR_CONFIRM_FAILURE_WINDOW_SECONDS = 60

_PAIR_CONFIRM_FAILURES: dict[str, list[float]] = {}
_PAIR_CONFIRM_FAILURES_LOCK = threading.Lock()


def create_pairing_request() -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()
    transport = lan_transport_security()
    _raise_if_pairing_transport_not_ready(transport)

    now = time.time()
    code = _unique_code()
    claim_secret = _new_pairing_claim_secret()
    record = {
        "id": code,
        "code": code,
        "claim_secret_hash": _hash_pairing_claim_secret(claim_secret),
        "status": "pending",
        "device_id": "",
        "device_name": "",
        "created_at": _iso(now),
        "expires_at": _iso(now + PAIR_CODE_TTL_SECONDS),
        "used_at": None,
        "updated_at": _iso(now),
        "server": _server_info(transport),
    }
    _write_pairing_record(record)
    return {
        "code": code,
        "claim_secret": claim_secret,
        "expires_at": record["expires_at"],
        "expires_in": PAIR_CODE_TTL_SECONDS,
        "server": record["server"],
    }


def _raise_if_pairing_transport_not_ready(transport: dict[str, Any]) -> None:
    if bool(transport.get("tls_ready")):
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Mobile pairing requires LAN HTTPS/WSS before generating a phone pairing code.",
            "status": str(transport.get("status") or "https_misconfigured"),
            "warning": str(transport.get("warning") or ""),
            "next_action": str(transport.get("next_action") or "Configure LAN TLS, then generate a new pairing code."),
            "transport_security": transport,
        },
    )


def confirm_pairing(*, code: str, device_name: str, claim_secret: str = "", client_host: str = "") -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()

    rate_key = _pairing_rate_key(client_host)
    _raise_if_pairing_rate_limited(rate_key)
    normalized = _normalize_code(code)
    expected_code_length = PAIR_CODE_HEX_LENGTH * 2
    if len(normalized) != expected_code_length:
        raise HTTPException(
            status_code=422,
            detail=f"Pairing code must be {expected_code_length} characters",
        )

    result = _redeem_pairing_record(normalized, device_name, claim_secret)
    if result is None:
        _record_pairing_failure(rate_key)
        raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")
    _clear_pairing_failures(rate_key)
    return result


def _redeem_pairing_record(code: str, device_name: str, claim_secret: str) -> dict[str, Any] | None:
    now = time.time()
    device_id = new_device_id()
    device_name = _safe_device_name(device_name)
    token_scopes = [TOKEN_SCOPE]
    scope_ttl: dict[str, int] | None = None
    settings = get_effective_settings()
    if _remote_desktop_view_enabled(settings):
        token_scopes.append(REMOTE_VIEW_SCOPE)
        scope_ttl = {REMOTE_VIEW_SCOPE: MOBILE_REMOTE_VIEW_TTL_SECONDS}
    used_at = now_iso()
    session = None
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_pairings WHERE id = ?", (code,)).fetchone()
        if not row:
            return None
        pairing_record = json.loads(row["data"])
        if pairing_record.get("status") != "pending":
            return None
        if not _pairing_claim_secret_matches(pairing_record, claim_secret):
            return None
        if _parse_iso(str(pairing_record.get("expires_at") or "")) <= now:
            updated = dict(pairing_record)
            updated["status"] = "expired"
            updated["updated_at"] = used_at
            conn.execute(
                """
                UPDATE mobile_pairings
                SET data = ?,
                    status = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = ?
                """,
                (json.dumps(updated, ensure_ascii=False), "expired", used_at, code, "pending"),
            )
            return None
        updated = dict(pairing_record)
        updated.update(
            {
                "status": "used",
                "device_id": device_id,
                "device_name": device_name,
                "used_at": used_at,
                "updated_at": used_at,
            }
        )
        cursor = conn.execute(
            """
            UPDATE mobile_pairings
            SET data = ?,
                status = ?,
                used_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status = ?
            """,
            (json.dumps(updated, ensure_ascii=False), "used", used_at, used_at, code, "pending"),
        )
        if cursor.rowcount != 1:
            return None
        _upsert_mobile_device_locked(conn, device_id=device_id, device_name=device_name, timestamp=used_at)
        session = create_mobile_session_locked(
            conn,
            device_id=device_id,
            device_name=device_name,
            token_epoch=0,
            scopes=token_scopes,
            scope_ttl=scope_ttl,
        )
    if session is None:
        return None
    record(
        "mobile.session.created",
        "MobilePairingService",
        {
            "device_id": device_id,
            "family_id": session.token_family_id,
            "credential_id": session.device_credential_id,
        },
    )
    return {
        **session.model_dump(mode="json"),
        "device_id": device_id,
        "device_trust": mobile_device_trust_metadata(),
        "view_expires_in": (
            min(MOBILE_REMOTE_VIEW_TTL_SECONDS, session.expires_in) if REMOTE_VIEW_SCOPE in token_scopes else 0
        ),
        "server": _server_info(),
    }


def list_pending_approvals(claims: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    approvals = db.fetch_many("approvals", "status = ?", ("pending",))
    return [
        _safe_approval_payload(row, claims) for row in approvals if _mobile_claims_allow_approval_for_read(row, claims)
    ]


def get_approval_detail(approval_id: str, claims: dict[str, Any] | None = None) -> dict[str, Any]:
    approval_data = db.fetch_one("approvals", approval_id)
    if not approval_data:
        raise HTTPException(status_code=404, detail="Approval not found")
    _raise_if_mobile_claims_disallowed_for_read(approval_data, claims)

    approval = Approval.model_validate(approval_data)
    task_data = db.fetch_one("tasks", approval.task_id)
    task = Task.model_validate(task_data) if task_data else None
    plan = _latest_plan(task.id if task else approval.task_id)
    approval_payload = _safe_approval_payload(approval.model_dump(mode="json"), claims)
    return {
        "approval": approval_payload,
        "task": _safe_mobile_task(task) if task else None,
        "plan": _safe_mobile_plan(plan),
        "preview": approval_payload.get("diff_preview", {}),
    }


def list_mobile_devices(claims: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    allowed_device_id = _text(claims.get("device_id")) if claims is not None else ""
    devices: list[dict[str, Any]] = []
    for row in db.fetch_many("mobile_devices", limit=100):
        device = _safe_mobile_device_payload(row)
        if device["status"] != "active":
            continue
        device_id = device["device_id"]
        if allowed_device_id and device_id != allowed_device_id:
            continue
        devices.append(device)
    return devices


def list_active_remote_input_grants_for_claims(claims: dict[str, Any]) -> list[dict[str, Any]]:
    device_id = _text(claims.get("device_id"))
    if not device_id:
        return []
    device = db.fetch_one("mobile_devices", device_id)
    if not device or str(device.get("status") or "active").lower() != "active":
        return []
    return [
        _safe_remote_input_grant(grant)
        for grant in _normalized_remote_input_grants(device)
        if grant["status"] == "active"
    ]


def create_remote_input_grant(
    device_id: str,
    *,
    expires_in_seconds: int = REMOTE_INPUT_GRANT_TTL_SECONDS,
) -> dict[str, Any]:
    _require_remote_control_enabled()
    normalized_id = _text(device_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="Missing mobile device id")
    expires_in = _remote_input_grant_ttl(expires_in_seconds)
    now = datetime.now(UTC)
    created_at = now.isoformat()
    expires_at = (now + timedelta(seconds=expires_in)).isoformat()
    grant_id = f"rig_{secrets.token_hex(12)}"

    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Mobile device not found")
        device = json.loads(row["data"])
        if str(device.get("status") or "active").lower() != "active":
            raise HTTPException(status_code=409, detail="Mobile device has been revoked")

        grants = _normalized_remote_input_grants(device)
        grant_record = {
            "id": grant_id,
            "status": "active",
            "scope": REMOTE_INPUT_SCOPE,
            "created_at": created_at,
            "expires_at": expires_at,
            "revoked_at": "",
        }
        grants.append(grant_record)
        device["remote_input_grants"] = grants
        device["updated_at"] = created_at
        conn.execute(
            """
            UPDATE mobile_devices
            SET data = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(device, ensure_ascii=False), created_at, normalized_id),
        )

    record(
        "mobile.remote_input_grant.created",
        "MobilePairingService",
        {"device_id": normalized_id, "grant_id": grant_id, "expires_at": expires_at},
    )
    from app.services.approval_event_service import publish_remote_input_grant_created

    publish_remote_input_grant_created(normalized_id, grant_record)
    return {
        "grant_id": grant_id,
        "device_id": normalized_id,
        "expires_at": expires_at,
        "expires_in": expires_in,
        "device": _safe_mobile_device_payload(device),
    }


def claim_remote_input_grant_token(grant_id: str, claims: dict[str, Any]) -> dict[str, Any]:
    device_id = _text(claims.get("device_id"))
    normalized_grant_id = _text(grant_id)
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    if not normalized_grant_id:
        raise HTTPException(status_code=422, detail="Missing remote input grant id")
    _require_remote_control_enabled()

    token_id = secrets.token_hex(16)
    grant, device_token_epoch = _bind_remote_input_grant_token_id(device_id, normalized_grant_id, token_id)
    expires_at = _grant_expires_at(grant)
    remaining = int((expires_at - datetime.now(UTC)).total_seconds())
    if remaining <= 0:
        raise HTTPException(status_code=401, detail="Remote input grant expired")
    token = issue_mobile_token(
        device_id=device_id,
        device_name=str(claims.get("device_name") or "Android device"),
        expires_in_seconds=min(remaining, REMOTE_INPUT_GRANT_TTL_SECONDS),
        scope=REMOTE_INPUT_SCOPE,
        source="remote_input_grant",
        grant_id=normalized_grant_id,
        token_id=token_id,
        token_epoch=device_token_epoch,
    )
    record(
        "mobile.remote_input_grant.claimed",
        "MobilePairingService",
        {"device_id": device_id, "grant_id": normalized_grant_id, "expires_at": grant["expires_at"]},
    )
    return {
        "token": token,
        "token_type": "Bearer",
        "grant_id": normalized_grant_id,
        "device_id": device_id,
        "expires_at": grant["expires_at"],
        "expires_in": max(1, min(remaining, REMOTE_INPUT_GRANT_TTL_SECONDS)),
        "grant": _safe_remote_input_grant(grant),
    }


def _bind_remote_input_grant_token_id(device_id: str, grant_id: str, token_id: str) -> tuple[dict[str, Any], int]:
    """Atomically bind ``token_id`` to the named grant, rotating any prior token.

    Returns ``(normalized_grant, device_token_epoch)``. Raises the same HTTP
    errors the inline claim path used to raise (missing/revoked device,
    unknown/inactive grant).
    """
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Mobile device has been revoked")
        device = json.loads(row["data"])
        if str(device.get("status") or "active").lower() != "active":
            raise HTTPException(status_code=401, detail="Mobile device has been revoked")
        grants = _normalized_remote_input_grants(device)
        target = next((grant for grant in grants if grant["id"] == grant_id), None)
        if target is None:
            raise HTTPException(status_code=403, detail="Remote input grant is not available for this device")
        if target["status"] != "active":
            raise HTTPException(status_code=401, detail="Remote input grant is not active")
        target["token_id"] = token_id
        device["remote_input_grants"] = grants
        device["updated_at"] = timestamp
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), timestamp, device_id),
        )
        token_epoch = int(device.get("token_epoch") or 0)
    return target, token_epoch


def revoke_remote_input_grant(device_id: str, grant_id: str) -> dict[str, Any]:
    normalized_id = _text(device_id)
    normalized_grant_id = _text(grant_id)
    if not normalized_id or not normalized_grant_id:
        raise HTTPException(status_code=422, detail="Missing remote input grant id")
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Mobile device not found")
        device = json.loads(row["data"])
        grants = _normalized_remote_input_grants(device)
        matched: dict[str, Any] | None = None
        for grant in grants:
            if str(grant.get("id") or "") != normalized_grant_id:
                continue
            matched = grant
            grant["status"] = "revoked"
            grant["revoked_at"] = timestamp
            break
        if matched is None:
            raise HTTPException(status_code=404, detail="Remote input grant not found")
        device["remote_input_grants"] = grants
        device["updated_at"] = timestamp
        conn.execute(
            """
            UPDATE mobile_devices
            SET data = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(device, ensure_ascii=False), timestamp, normalized_id),
        )
    record(
        "mobile.remote_input_grant.revoked",
        "MobilePairingService",
        {"device_id": normalized_id, "grant_id": normalized_grant_id},
    )
    from app.services.approval_event_service import publish_remote_input_grant_revoked

    publish_remote_input_grant_revoked(normalized_id, matched)
    return _safe_remote_input_grant(matched)


def revoke_mobile_device(device_id: str) -> dict[str, Any]:
    normalized_id = _text(device_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="Missing mobile device id")
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Mobile device not found")
        updated = json.loads(row["data"])
        updated["status"] = "revoked"
        updated["revoked_at"] = timestamp
        updated["updated_at"] = timestamp
        updated["remote_input_grants"] = _revoked_remote_input_grants(updated, timestamp)
        updated.pop("push_subscription", None)
        revoke_device_token_families_locked(
            conn,
            normalized_id,
            timestamp=timestamp,
            reason="device_revoked",
        )
        revoke_device_credentials_locked(conn, normalized_id, timestamp=timestamp)
        conn.execute(
            """
            UPDATE mobile_devices
            SET data = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(updated, ensure_ascii=False), timestamp, normalized_id),
        )
    payload = {
        "device_id": normalized_id,
        "device_name": updated.get("device_name") or "Android device",
        "status": "revoked",
        "revoked_at": timestamp,
        "updated_at": timestamp,
        "device_trust": _safe_mobile_device_trust(updated),
        "remote_input_grants": _safe_remote_input_grants(updated),
    }
    from app.services.approval_event_service import publish_mobile_device_revoked

    publish_mobile_device_revoked(payload)
    return payload


def revoke_mobile_device_sessions(device_id: str) -> dict[str, Any]:
    """Invalidate all tokens issued for a device without un-pairing it.

    Bumps the device's ``token_epoch`` so every previously issued paired/grant
    token fails validation, while the device stays active and can mint a fresh
    token on the next claim/pair. Active remote-input grants are also revoked.
    """
    normalized_id = _text(device_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="Missing mobile device id")
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Mobile device not found")
        updated = json.loads(row["data"])
        updated["token_epoch"] = int(updated.get("token_epoch") or 0) + 1
        updated["remote_input_grants"] = _revoked_remote_input_grants(updated, timestamp)
        updated.pop("push_subscription", None)
        updated["updated_at"] = timestamp
        revoke_device_token_families_locked(
            conn,
            normalized_id,
            timestamp=timestamp,
            reason="device_sessions_revoked",
        )
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(updated, ensure_ascii=False), timestamp, normalized_id),
        )
    record(
        "mobile.device.sessions_revoked",
        "MobilePairingService",
        {"device_id": normalized_id, "token_epoch": updated["token_epoch"]},
    )
    return {
        "device_id": normalized_id,
        "status": str(updated.get("status") or "active").lower(),
        "token_epoch": updated["token_epoch"],
        "updated_at": timestamp,
        "device_trust": _safe_mobile_device_trust(updated),
        "remote_input_grants": _safe_remote_input_grants(updated),
    }


def revoke_own_mobile_device(device_id: str, claims: dict[str, Any]) -> dict[str, Any]:
    normalized_id = _text(device_id)
    claim_device_id = _text(claims.get("device_id"))
    if not claim_device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    if normalized_id != claim_device_id:
        raise HTTPException(status_code=403, detail="Mobile token can only revoke its own device")
    return revoke_mobile_device(normalized_id)


def approve_approval(approval_id: str, claims: dict[str, Any] | None = None) -> Approval:
    return _decide_approval(approval_id, ApprovalStatus.APPROVED, claims=claims)


def reject_approval(approval_id: str, claims: dict[str, Any] | None = None) -> Approval:
    return _decide_approval(approval_id, ApprovalStatus.REJECTED, claims=claims)


def refresh_mobile_session_token(refresh_token: str) -> dict[str, Any]:
    """Rotate a refresh-token family and issue a short-lived paired access token."""
    token_scopes = [TOKEN_SCOPE]
    scope_ttl: dict[str, int] | None = None
    settings = get_effective_settings()
    if _remote_desktop_view_enabled(settings):
        token_scopes.append(REMOTE_VIEW_SCOPE)
        scope_ttl = {REMOTE_VIEW_SCOPE: MOBILE_REMOTE_VIEW_TTL_SECONDS}

    session, device = rotate_mobile_refresh_token(
        refresh_token,
        scopes=token_scopes,
        scope_ttl=scope_ttl,
    )
    return {
        **session.model_dump(mode="json"),
        "device_id": str(device.get("device_id") or device.get("id") or ""),
        "device_trust": _safe_mobile_device_trust(device),
        "view_expires_in": (
            min(MOBILE_REMOTE_VIEW_TTL_SECONDS, session.expires_in) if REMOTE_VIEW_SCOPE in token_scopes else 0
        ),
        "server": _server_info(),
    }


def register_mobile_push_subscription(
    claims: dict[str, Any],
    *,
    provider: str,
    push_token: str,
) -> dict[str, str]:
    device_id = _text(claims.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    normalized_provider = _text(provider).lower()
    normalized_token = _text(push_token)
    if normalized_provider != "expo" or not _valid_expo_push_token(normalized_token):
        raise HTTPException(status_code=422, detail="Invalid mobile push subscription")
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Mobile device is not paired")
        device = json.loads(row["data"])
        if str(device.get("status") or "active").lower() != "active":
            raise HTTPException(status_code=401, detail="Mobile device has been revoked")
        device["push_subscription"] = {
            "provider": normalized_provider,
            "token": normalized_token,
            "updated_at": timestamp,
        }
        device["updated_at"] = timestamp
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), timestamp, device_id),
        )
    record(
        "mobile.push_subscription.registered",
        "MobilePairingService",
        {"device_id": device_id, "provider": normalized_provider},
    )
    return {"status": "registered", "provider": normalized_provider}


def unregister_mobile_push_subscription(claims: dict[str, Any]) -> dict[str, str]:
    device_id = _text(claims.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    _remove_mobile_push_subscription(device_id)
    return {"status": "unregistered"}


def _remove_mobile_push_subscription(device_id: str, *, expected_token: str = "") -> bool:
    normalized_id = _text(device_id)
    if not normalized_id:
        return False
    timestamp = now_iso()
    removed = False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            return False
        device = json.loads(row["data"])
        subscription = device.get("push_subscription")
        if not isinstance(subscription, dict):
            return False
        if expected_token and _text(subscription.get("token")) != expected_token:
            return False
        device.pop("push_subscription", None)
        device["updated_at"] = timestamp
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), timestamp, normalized_id),
        )
        removed = True
    if removed:
        record(
            "mobile.push_subscription.removed",
            "MobilePairingService",
            {"device_id": normalized_id},
        )
    return removed


def _valid_expo_push_token(value: str) -> bool:
    return bool(re.fullmatch(r"(?:Expo|Exponent)PushToken\[[A-Za-z0-9_-]{1,200}\]", value))


def validate_mobile_token(token: str) -> dict[str, Any]:
    return decode_mobile_token(token)


def _remote_desktop_view_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "remote_desktop_enabled", False)) and has_feature(
        active_plan(settings), Feature.REMOTE_VIEW
    )


def _require_remote_control_enabled(settings: Any | None = None) -> None:
    settings = settings or get_effective_settings()
    if not bool(getattr(settings, "remote_desktop_enabled", False)):
        raise HTTPException(status_code=403, detail="Remote desktop is disabled")
    if not has_feature(active_plan(settings), Feature.REMOTE_CONTROL):
        raise HTTPException(status_code=403, detail="Remote desktop is disabled")
    if not subscription_confirmation_fresh_for_high_risk(settings):
        raise HTTPException(status_code=403, detail="Remote input requires a fresh subscription confirmation")


def _write_pairing_record(record: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO mobile_pairings (id, data, status, created_at, expires_at, used_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data=excluded.data,
                status=excluded.status,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at,
                used_at=excluded.used_at,
                updated_at=excluded.updated_at
            """,
            (
                record["id"],
                json.dumps(record, ensure_ascii=False),
                record["status"],
                record["created_at"],
                record["expires_at"],
                record["used_at"],
                record["updated_at"],
            ),
        )


def _load_pairing_record(code: str) -> dict[str, Any] | None:
    return db.fetch_one("mobile_pairings", code)


def _expire_pairing_record(record: dict[str, Any]) -> None:
    updated = dict(record)
    updated["status"] = "expired"
    updated["updated_at"] = now_iso()
    _write_pairing_record(updated)


def _expire_stale_pairings() -> None:
    now = time.time()
    for pairing_record in db.fetch_many("mobile_pairings", limit=500):
        if pairing_record.get("status") != "pending":
            continue
        expires_at = _parse_iso(str(pairing_record.get("expires_at") or ""))
        if expires_at <= now:
            _expire_pairing_record(pairing_record)


def _upsert_mobile_device(*, device_id: str, device_name: str) -> None:
    timestamp = now_iso()
    with db.connect() as conn:
        _upsert_mobile_device_locked(conn, device_id=device_id, device_name=device_name, timestamp=timestamp)


def _upsert_mobile_device_locked(conn: Any, *, device_id: str, device_name: str, timestamp: str) -> None:
    body = {
        "id": device_id,
        "device_id": device_id,
        "device_name": device_name,
        "status": "active",
        "revoked_at": "",
        "remote_input_grants": [],
        "token_epoch": 0,
        "device_trust": mobile_device_trust_metadata(),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    conn.execute(
        """
        INSERT INTO mobile_devices (id, data, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
        """,
        (device_id, json.dumps(body, ensure_ascii=False), body["created_at"], body["updated_at"]),
    )


def _decide_approval(approval_id: str, status: ApprovalStatus, *, claims: dict[str, Any] | None = None) -> Approval:
    existing = db.fetch_one("approvals", approval_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Approval not found")
    if status == ApprovalStatus.REJECTED:
        _raise_if_mobile_claims_disallowed_for_reject(existing, claims)
    else:
        _raise_if_mobile_claims_disallowed(existing, claims)
    existing_approval = Approval.model_validate(existing)
    if existing_approval.consumed_at:
        raise HTTPException(status_code=409, detail="Approval has already been consumed.")
    if existing_approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {existing_approval.status}.")

    if status == ApprovalStatus.APPROVED:
        state_error = _approval_state_error(approval_id)
        if state_error:
            expired = db.expire_approval_if_pending(approval_id, now_iso(), state_error)
            if expired:
                from app.services.approval_event_service import publish_approval_decided

                publish_approval_decided(Approval.model_validate(expired))
            raise HTTPException(status_code=409, detail="Approval is no longer executable.")
    data = db.decide_approval_atomically(approval_id, status.value, now_iso())
    if not data:
        existing_approval = Approval.model_validate(db.fetch_one("approvals", approval_id) or existing)
        raise HTTPException(status_code=409, detail=f"Approval is already {existing_approval.status}.")

    from app.services.approval_event_service import publish_approval_decided

    approval = Approval.model_validate(data)
    publish_approval_decided(approval)
    return approval


def _approval_state_error(approval_id: str) -> str:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        return ""
    approval = Approval.model_validate(data)
    task_data = db.fetch_one("tasks", approval.task_id)
    if not task_data:
        return f"Task not found: {approval.task_id}"
    task = Task.model_validate(task_data)
    if task.execution_stage != ExecutionStage.AWAITING_APPROVAL:
        return f"Task execution stage is {task.execution_stage}; expected awaiting_approval."
    plan = _latest_plan(task.id)
    if not plan:
        return f"Plan not found for task: {task.id}"
    step = next((item for item in plan.get("steps", []) if item.get("id") == approval.step_id), None)
    if not step:
        return f"Step not found for approval: {approval.step_id}"
    if step.get("status") != "waiting_user_approval":
        return f"Step status is {step.get('status')}; expected waiting_user_approval."
    return ""


def _unique_code() -> str:
    for _ in range(100):
        code = secrets.token_hex(PAIR_CODE_HEX_LENGTH)
        if not db.fetch_one("mobile_pairings", code):
            return code
    raise HTTPException(status_code=503, detail="Unable to allocate a pairing code")


def _new_pairing_claim_secret() -> str:
    return secrets.token_urlsafe(PAIR_CLAIM_SECRET_BYTES)


def _hash_pairing_claim_secret(claim_secret: str) -> str:
    return sha256(str(claim_secret or "").encode("utf-8")).hexdigest()


def _pairing_claim_secret_matches(record: dict[str, Any], claim_secret: str) -> bool:
    expected_hash = str(record.get("claim_secret_hash") or "").strip()
    supplied = str(claim_secret or "").strip()
    if not expected_hash or not supplied:
        return False
    return secrets.compare_digest(expected_hash, _hash_pairing_claim_secret(supplied))


def _normalize_code(code: str) -> str:
    return "".join(character for character in code if character.isalnum()).lower()


def _safe_device_name(device_name: str) -> str:
    cleaned = "".join(character for character in str(device_name or "") if character.isprintable()).strip()
    return cleaned[:80] or "Android device"


def _pairing_rate_key(client_host: str) -> str:
    return (client_host or "unknown").strip().lower() or "unknown"


def _raise_if_pairing_rate_limited(rate_key: str) -> None:
    now = time.time()
    with _PAIR_CONFIRM_FAILURES_LOCK:
        failures = _recent_pairing_failures(rate_key, now)
        if len(failures) >= PAIR_CONFIRM_FAILURE_LIMIT:
            _PAIR_CONFIRM_FAILURES[rate_key] = failures
            raise HTTPException(status_code=429, detail="Too many failed pairing attempts. Try again later.")
        _PAIR_CONFIRM_FAILURES[rate_key] = failures


def _record_pairing_failure(rate_key: str) -> None:
    now = time.time()
    with _PAIR_CONFIRM_FAILURES_LOCK:
        failures = _recent_pairing_failures(rate_key, now)
        failures.append(now)
        _PAIR_CONFIRM_FAILURES[rate_key] = failures


def _clear_pairing_failures(rate_key: str) -> None:
    with _PAIR_CONFIRM_FAILURES_LOCK:
        _PAIR_CONFIRM_FAILURES.pop(rate_key, None)


def _recent_pairing_failures(rate_key: str, now: float) -> list[float]:
    cutoff = now - PAIR_CONFIRM_FAILURE_WINDOW_SECONDS
    return [timestamp for timestamp in _PAIR_CONFIRM_FAILURES.get(rate_key, []) if timestamp >= cutoff]
