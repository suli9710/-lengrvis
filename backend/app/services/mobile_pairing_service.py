from __future__ import annotations

import json
import secrets
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.commerce.entitlements import Feature, active_plan, has_feature
from app.commerce.licensing import subscription_confirmation_fresh_for_high_risk
from app.config import AppSettings
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Task, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.execution_stage import ExecutionStage
from app.policy.approval_binding import redacted_preview, remote_input_binding_ref
from app.policy.redaction import contains_sensitive_key, redact_public_text, redact_value
from app.security.mobile_jwt import (
    MOBILE_REMOTE_VIEW_TTL_SECONDS,
    MOBILE_TOKEN_TTL_SECONDS,
    REMOTE_INPUT_SCOPE,
    REMOTE_VIEW_SCOPE,
    TOKEN_SCOPE,
    decode_mobile_token,
    issue_mobile_token,
    mobile_token_scopes,
    new_device_id,
)

PAIR_CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = MOBILE_TOKEN_TTL_SECONDS
REMOTE_INPUT_GRANT_TTL_SECONDS = 5 * 60
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
    token = issue_mobile_token(
        device_id=device_id,
        device_name=device_name,
        expires_in_seconds=TOKEN_TTL_SECONDS,
        scope=token_scopes,
        scope_ttl=scope_ttl,
    )
    used_at = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_pairings WHERE id = ?", (code,)).fetchone()
        if not row:
            return None
        record = json.loads(row["data"])
        if record.get("status") != "pending":
            return None
        if not _pairing_claim_secret_matches(record, claim_secret):
            return None
        if _parse_iso(str(record.get("expires_at") or "")) <= now:
            updated = dict(record)
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
        updated = dict(record)
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
    return {
        "token": token,
        "token_type": "Bearer",
        "device_id": device_id,
        "expires_in": TOKEN_TTL_SECONDS,
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
        updated["updated_at"] = timestamp
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


def refresh_mobile_session_token(claims: dict[str, Any]) -> dict[str, Any]:
    """Re-issue the paired mobile token, refreshing short-lived remote view scope."""
    device_id = _text(claims.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    if not is_mobile_device_active(device_id):
        raise HTTPException(status_code=401, detail="Mobile device has been revoked")
    if _text(claims.get("source")) == "remote_input_grant":
        raise HTTPException(status_code=403, detail="Remote input grant token cannot refresh paired session")

    scopes = mobile_token_scopes(claims)
    if TOKEN_SCOPE not in scopes:
        raise HTTPException(status_code=403, detail="Mobile token scope is not allowed")

    token_scopes = [TOKEN_SCOPE]
    scope_ttl: dict[str, int] | None = None
    settings = get_effective_settings()
    if _remote_desktop_view_enabled(settings):
        token_scopes.append(REMOTE_VIEW_SCOPE)
        scope_ttl = {REMOTE_VIEW_SCOPE: MOBILE_REMOTE_VIEW_TTL_SECONDS}

    device = db.fetch_one("mobile_devices", device_id) or {}
    token = issue_mobile_token(
        device_id=device_id,
        device_name=str(claims.get("device_name") or device.get("device_name") or "Android device"),
        expires_in_seconds=TOKEN_TTL_SECONDS,
        scope=token_scopes,
        scope_ttl=scope_ttl,
        token_epoch=int(device.get("token_epoch") or 0),
    )
    return {
        "token": token,
        "token_type": "Bearer",
        "device_id": device_id,
        "expires_in": TOKEN_TTL_SECONDS,
        "view_expires_in": MOBILE_REMOTE_VIEW_TTL_SECONDS if REMOTE_VIEW_SCOPE in token_scopes else 0,
        "server": _server_info(),
    }


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


def mobile_claims_can_access_approval(approval: Approval | dict[str, Any], claims: dict[str, Any]) -> bool:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _mobile_claims_allow_approval_for_read(payload, claims)


def raise_if_mobile_claims_disallowed(approval: Approval | dict[str, Any], claims: dict[str, Any] | None) -> None:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    _raise_if_mobile_claims_disallowed(payload, claims)


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


def _raise_if_mobile_claims_disallowed(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_approve_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _raise_if_mobile_claims_disallowed_for_read(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_read_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _raise_if_mobile_claims_disallowed_for_reject(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_reject_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _mobile_claims_allow_approval(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_approval_denial_reason(approval, claims)


def _mobile_claims_allow_approval_for_read(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_approval_read_denial_reason(approval, claims)


def _mobile_approval_read_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if not reason:
        return ""
    if _paired_mobile_claims_can_read_remote_input_approval(approval, claims):
        return ""
    return reason


def _mobile_approval_reject_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if not reason:
        return ""
    if _paired_mobile_claims_can_reject_remote_input_approval(approval, claims):
        return ""
    return reason


def _mobile_approval_approve_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if reason:
        return reason
    if _remote_input_grant_cannot_self_approve(approval, claims):
        return "Remote input grant token cannot approve its own input request."
    return ""


def _remote_input_grant_cannot_self_approve(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    if claims is None or not _is_remote_input_approval(approval):
        return False
    if _text(claims.get("source")) != "remote_input_grant":
        return False
    claim_grant_id = _text(claims.get("grant_id"))
    if not claim_grant_id:
        return False
    return claim_grant_id == _approval_source_grant_id(approval)


def _mobile_approval_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    if claims is None:
        return ""

    device_id = _text(claims.get("device_id"))
    if not device_id:
        return "Mobile token is missing a device binding."
    if not is_mobile_device_active(device_id):
        return "Mobile device has been revoked."

    scopes = mobile_token_scopes(claims)
    if not scopes:
        return "Mobile token is missing an approval scope."

    allowed_devices = _approval_allowed_device_ids(approval)
    if allowed_devices and device_id not in allowed_devices:
        return "Mobile token is not allowed to access this approval."

    approval_source = _approval_source(approval)
    claim_source = _text(claims.get("source"))
    if claim_source == "remote_input_grant" and not _is_remote_input_approval(approval):
        return "Remote input grant token is not allowed for ordinary approvals."
    if approval_source and claim_source and approval_source != claim_source:
        return "Mobile token source is not allowed for this approval."

    if _is_remote_input_approval(approval):
        if REMOTE_INPUT_SCOPE not in scopes:
            return "Remote input approvals require a remote input scope."
        claim_grant_id = _text(claims.get("grant_id"))
        if _text(claims.get("source")) != "remote_input_grant" or not claim_grant_id:
            return "Remote input approvals require a remote input grant."
        if not allowed_devices:
            return "Remote input approval is missing a device binding."
        approval_grant_id = _approval_source_grant_id(approval)
        if not approval_grant_id:
            return "Remote input approval is missing a grant binding."
        if claim_grant_id != approval_grant_id:
            return "Remote input grant is not allowed to access this approval."
        return ""

    required_scopes = _approval_required_mobile_scopes(approval) or {TOKEN_SCOPE}
    if not scopes.intersection(required_scopes):
        return "Mobile token scope is not allowed for this approval."
    return ""


def _paired_mobile_claims_can_read_remote_input_approval(
    approval: dict[str, Any],
    claims: dict[str, Any] | None,
) -> bool:
    if claims is None or not _is_remote_input_approval(approval):
        return False
    device_id = _text(claims.get("device_id"))
    if not device_id or not is_mobile_device_active(device_id):
        return False
    scopes = mobile_token_scopes(claims)
    if TOKEN_SCOPE not in scopes:
        return False
    allowed_devices = _approval_allowed_device_ids(approval)
    if not allowed_devices or device_id not in allowed_devices:
        return False
    if not _approval_source_grant_id(approval):
        return False
    approval_source = _approval_source(approval)
    claim_source = _text(claims.get("source"))
    return not approval_source or not claim_source or approval_source == claim_source


def _paired_mobile_claims_can_reject_remote_input_approval(
    approval: dict[str, Any],
    claims: dict[str, Any] | None,
) -> bool:
    if _text((claims or {}).get("source")) == "remote_input_grant":
        return False
    return _paired_mobile_claims_can_read_remote_input_approval(approval, claims)


def _is_remote_input_approval(approval: dict[str, Any]) -> bool:
    approval_type = _text(approval.get("approval_type")).lower()
    source = _text(approval.get("source")).lower()
    return approval_type == "remote_input" or source == "remote_input"


def _approval_source(approval: dict[str, Any]) -> str:
    source = _text(approval.get("source")).lower()
    return "" if source in {"", "tool_call", "remote_input"} else source


def _approval_required_mobile_scopes(approval: dict[str, Any]) -> set[str]:
    return set(_text_list(approval.get("required_mobile_scopes")))


def _approval_allowed_device_ids(approval: dict[str, Any]) -> set[str]:
    device_ids = set(_text_list(approval.get("allowed_device_ids")))
    source_device_id = _text(approval.get("source_device_id"))
    if source_device_id:
        device_ids.add(source_device_id)
    if _is_remote_input_approval(approval):
        audit_device_id = _remote_input_source_device_id_from_audit(approval)
        if audit_device_id:
            device_ids.add(audit_device_id)
    return device_ids


def _approval_source_grant_id(approval: dict[str, Any]) -> str:
    grant_id = _text(approval.get("source_grant_id"))
    if grant_id:
        return grant_id
    return _remote_input_source_grant_id_from_audit(approval)


def _remote_input_source_device_id_from_audit(approval: dict[str, Any]) -> str:
    payload = _remote_input_approval_audit_payload(approval)
    return _text(payload.get("device_id")) if payload else ""


def _remote_input_source_grant_id_from_audit(approval: dict[str, Any]) -> str:
    payload = _remote_input_approval_audit_payload(approval)
    return _text(payload.get("grant_id")) if payload else ""


def _remote_input_approval_audit_payload(approval: dict[str, Any]) -> dict[str, Any] | None:
    approval_id = _text(approval.get("id"))
    task_id = _text(approval.get("task_id"))
    if not approval_id or not task_id:
        return None
    for event in db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=100):
        if event.get("event_type") != "remote.input.approval_requested":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict) or _text(payload.get("approval_id")) != approval_id:
            continue
        return payload
    return None


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            result.extend(_text_list(item))
        return result
    text = _text(value)
    return [text] if text else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_mobile_device_active(device_id: str) -> bool:
    normalized_id = _text(device_id)
    if not normalized_id:
        return False
    device = db.fetch_one("mobile_devices", normalized_id)
    if not device:
        return False
    return str(device.get("status") or "active").lower() == "active"


def _remote_input_grant_ttl(value: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = REMOTE_INPUT_GRANT_TTL_SECONDS
    return max(1, min(requested, REMOTE_INPUT_GRANT_TTL_SECONDS))


def _normalized_remote_input_grants(device: dict[str, Any]) -> list[dict[str, Any]]:
    raw_grants = device.get("remote_input_grants") or []
    if not isinstance(raw_grants, list):
        return []
    now = datetime.now(UTC)
    grants: list[dict[str, Any]] = []
    for raw in raw_grants:
        if not isinstance(raw, dict):
            continue
        grant = {
            "id": _text(raw.get("id")),
            "status": _text(raw.get("status")) or "active",
            "scope": _text(raw.get("scope")) or REMOTE_INPUT_SCOPE,
            "created_at": _text(raw.get("created_at")),
            "expires_at": _text(raw.get("expires_at")),
            "revoked_at": _text(raw.get("revoked_at")),
            # Internal anti-replay binding; never surfaced in safe payloads.
            "token_id": _text(raw.get("token_id")),
        }
        if not grant["id"]:
            continue
        if grant["status"] == "active" and _grant_expires_at(grant) < now:
            grant["status"] = "expired"
        grants.append(grant)
    return grants


def _revoked_remote_input_grants(device: dict[str, Any], timestamp: str) -> list[dict[str, Any]]:
    grants = _normalized_remote_input_grants(device)
    for grant in grants:
        if grant["status"] == "active":
            grant["status"] = "revoked"
            grant["revoked_at"] = timestamp
    return grants


def _safe_mobile_device_payload(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": device.get("device_id") or device.get("id") or "",
        "device_name": device.get("device_name") or "Android device",
        "status": str(device.get("status") or "active").lower(),
        "created_at": device.get("created_at") or "",
        "updated_at": device.get("updated_at") or "",
        "revoked_at": device.get("revoked_at") or "",
        "remote_input_grants": _safe_remote_input_grants(device),
    }


def _safe_remote_input_grants(device: dict[str, Any]) -> list[dict[str, Any]]:
    return [_safe_remote_input_grant(grant) for grant in _normalized_remote_input_grants(device)]


def _safe_remote_input_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": grant.get("id") or "",
        "status": grant.get("status") or "",
        "scope": grant.get("scope") or REMOTE_INPUT_SCOPE,
        "created_at": grant.get("created_at") or "",
        "expires_at": grant.get("expires_at") or "",
        "revoked_at": grant.get("revoked_at") or "",
        "binding_ref": remote_input_binding_ref(grant.get("id")),
    }


def _grant_expires_at(grant: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(grant.get("expires_at") or ""))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)


def _latest_plan(task_id: str) -> dict[str, Any] | None:
    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    return plans[0] if plans else None


def _safe_approval_payload(approval: dict[str, Any], claims: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(approval)
    is_remote_input = _is_remote_input_approval(payload)
    payload["message"] = _safe_mobile_text(payload.get("message") or "")
    payload["diff_preview"] = redacted_preview(payload.get("diff_preview") or {})
    payload["model_action"] = _safe_mobile_model_action(payload.get("model_action") or {})
    for key in (
        "dry_run_summary",
        "tool_effects",
        "resource_kinds",
        "runtime_control_fields",
        "runtime_fields",
        "engineering_boundary",
    ):
        if key in payload:
            payload[key] = _safe_mobile_public_value(payload.get(key), key=key)
    if is_remote_input:
        payload["remote_input_binding"] = _remote_input_public_binding_state(approval, claims)
        for key in ("source_device_id", "source_grant_id", "allowed_device_ids"):
            payload.pop(key, None)
    return payload


def _remote_input_public_binding_state(
    approval: dict[str, Any],
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_scopes = _approval_required_mobile_scopes(approval)
    allowed_devices = _approval_allowed_device_ids(approval)
    approval_grant_id = _approval_source_grant_id(approval)
    device_id = _text((claims or {}).get("device_id"))
    claim_grant_id = _text((claims or {}).get("grant_id"))
    state = {
        "device_bound": bool(allowed_devices),
        "grant_bound": bool(approval_grant_id),
        "requires_remote_input_scope": REMOTE_INPUT_SCOPE in required_scopes,
        "binding_ref": remote_input_binding_ref(approval_grant_id),
    }
    if device_id:
        state["matches_current_device"] = bool(allowed_devices and device_id in allowed_devices)
    if claim_grant_id:
        state["matches_current_grant"] = claim_grant_id == approval_grant_id
    return state


def _safe_mobile_model_action(model_action: Any) -> dict[str, Any]:
    if not isinstance(model_action, dict):
        return {}
    safe = dict(model_action)
    if "args" in safe:
        safe["args"] = _safe_mobile_model_action_args(safe.get("args"))
    for key in ("raw", "input", "inputs", "output", "observation", "prompt", "content", "text", "value"):
        if key in safe:
            safe[key] = _safe_mobile_public_value(safe[key], key=key)
    safe["redacted"] = True
    return _safe_mobile_public_value(safe)


def _safe_mobile_model_action_args(raw_args: Any) -> Any:
    if isinstance(raw_args, dict):
        return {"redacted": True, "field_count": len(raw_args)}
    if isinstance(raw_args, list | tuple | set):
        return {"redacted": True, "field_count": len(raw_args)}
    if raw_args in (None, ""):
        return {}
    return "[REDACTED]"


def _safe_mobile_text(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))


def _safe_mobile_public_value(value: Any, *, key: str = "") -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key == "model_action":
        return _safe_mobile_model_action(value)
    if normalized_key == "risk_level" and isinstance(value, str):
        normalized_risk = _safe_mobile_risk_level(value)
        if normalized_risk:
            return normalized_risk
    if contains_sensitive_key(key):
        value = redact_value({key: value}).get(key)
    if isinstance(value, dict):
        return {item_key: _safe_mobile_public_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_safe_mobile_public_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_safe_mobile_public_value(item, key=key) for item in value]
    if isinstance(value, set):
        return [_safe_mobile_public_value(item, key=key) for item in sorted(value, key=str)]
    if isinstance(value, str):
        redacted = redact_value(value)
        return redact_public_text(str(redacted or ""))
    return value


def _safe_mobile_risk_level(value: str) -> str:
    normalized = value.strip()
    allowed = {
        "R0_READ_ONLY",
        "R1_OPEN_ONLY",
        "R2_REVERSIBLE_MODIFY",
        "R3_DESTRUCTIVE_OR_SYSTEM",
        "R4_FORBIDDEN_OR_HANDOFF",
    }
    if normalized.upper() not in allowed:
        return ""
    return normalized.lower().replace("_", " ")


def _safe_mobile_task(task: Task) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    for key in ("user_goal", "final_summary"):
        payload[key] = _safe_mobile_text(payload.get(key) or "")
    payload["metadata"] = _safe_mobile_task_metadata(payload.get("metadata"))
    return payload


def _safe_mobile_task_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict) or not metadata:
        return {}
    return {"redacted": True, "field_count": len(metadata)}


def _safe_mobile_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    safe = dict(plan)
    safe["goal"] = _safe_mobile_text(safe.get("goal") or "")
    safe["assumptions"] = _safe_mobile_public_value(safe.get("assumptions") or [], key="assumptions")
    safe_steps: list[dict[str, Any]] = []
    for raw_step in safe.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        safe_steps.append(
            {
                "id": raw_step.get("id") or "",
                "order": raw_step.get("order") or 0,
                "agent_name": raw_step.get("agent_name") or "",
                "tool_name": raw_step.get("tool_name") or "",
                "description": _safe_mobile_text(raw_step.get("description") or ""),
                "status": raw_step.get("status") or "",
                "risk_level": raw_step.get("risk_level") or "",
                "requires_approval": bool(raw_step.get("requires_approval")),
                "tool_effects": _safe_mobile_public_value(raw_step.get("tool_effects") or [], key="tool_effects"),
                "resource_kinds": _safe_mobile_public_value(raw_step.get("resource_kinds") or [], key="resource_kinds"),
                "trust_tier": raw_step.get("trust_tier") or "",
                "deferred_tool": bool(raw_step.get("deferred_tool")),
                "expected_observation": _safe_mobile_text(raw_step.get("expected_observation") or ""),
            }
        )
    safe["steps"] = safe_steps
    return safe


def safe_approval_payload(approval: Approval | dict[str, Any], claims: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _safe_approval_payload(payload, claims)


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


def _server_info(transport: dict[str, Any] | None = None) -> dict[str, Any]:
    transport = transport or lan_transport_security()
    return {
        "host": _lan_ip(),
        "port": _backend_port(),
        "scheme": transport["scheme"],
        "origin": transport["origin"],
        "transport_security": transport,
    }


def lan_transport_security(settings: AppSettings | None = None) -> dict[str, Any]:
    settings = settings or get_effective_settings()
    https_enabled = bool(getattr(settings, "lan_tls_enabled", False))
    cert_file = str(getattr(settings, "lan_tls_cert_file", "") or "").strip()
    key_file = str(getattr(settings, "lan_tls_key_file", "") or "").strip()
    cert_present = bool(cert_file) and Path(cert_file).expanduser().exists()
    key_present = bool(key_file) and Path(key_file).expanduser().exists()
    tls_validation = (
        _validate_lan_tls_material(cert_file, key_file)
        if https_enabled and cert_present and key_present
        else {"ok": False, "error": "", "fingerprint_sha256": ""}
    )
    tls_ready = https_enabled and cert_present and key_present and bool(tls_validation["ok"])
    scheme = "https" if https_enabled else "http"
    origin = _configured_lan_origin(settings, scheme)

    if tls_ready:
        status = "https_ready"
        warning = ""
        next_action = "Pair mobile devices with the HTTPS address and trust the local certificate when prompted."
    elif https_enabled:
        status = "https_misconfigured"
        missing = []
        if not cert_present:
            missing.append("certificate file")
        if not key_present:
            missing.append("private key file")
        if missing:
            warning = f"LAN HTTPS is enabled but the {' and '.join(missing)} is missing."
            next_action = "Create or point Lengrvis at a local TLS certificate and key, then restart the backend."
        else:
            warning = f"LAN HTTPS certificate/key validation failed: {tls_validation['error']}"
            next_action = (
                "Point Lengrvis at a parseable certificate and matching private key, then restart the backend."
            )
    else:
        status = "http_lan_insecure"
        warning = "LAN mobile pairing uses HTTP/ws transport unless HTTPS is explicitly configured."
        next_action = (
            "Use loopback for local testing, or configure LAN TLS before pairing phones on an untrusted network."
        )

    return {
        "status": status,
        "scheme": scheme,
        "origin": origin,
        "https_enabled": https_enabled,
        "tls_ready": tls_ready,
        "cert_configured": bool(cert_file),
        "key_configured": bool(key_file),
        "cert_present": cert_present,
        "key_present": key_present,
        "tls_material_valid": bool(tls_validation["ok"]),
        "requires_trust": https_enabled,
        "trust_required": https_enabled,
        "trust_model": "local_certificate" if https_enabled else "none",
        "fingerprint_sha256": str(tls_validation.get("fingerprint_sha256") or ""),
        "certificate_fingerprint_sha256": str(tls_validation.get("fingerprint_sha256") or ""),
        "warning": warning,
        "next_action": next_action,
    }


def _validate_lan_tls_material(cert_file: str, key_file: str) -> dict[str, Any]:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_path = Path(cert_file).expanduser()
        context.load_cert_chain(str(cert_path), str(Path(key_file).expanduser()))
        fingerprint = _certificate_fingerprint_sha256(cert_path)
    except Exception as exc:  # noqa: BLE001 - readiness should report a structured status.
        return {"ok": False, "error": _safe_tls_error(exc), "fingerprint_sha256": ""}
    return {"ok": True, "error": "", "fingerprint_sha256": fingerprint}


def _certificate_fingerprint_sha256(cert_path: Path) -> str:
    data = cert_path.read_bytes()
    text = data.decode("utf-8", errors="ignore")
    if "-----BEGIN CERTIFICATE-----" in text:
        data = ssl.PEM_cert_to_DER_cert(text)
    return sha256(data).hexdigest()


def _safe_tls_error(error: Exception) -> str:
    if isinstance(error, ssl.SSLError):
        return "certificate or private key could not be parsed or do not match"
    if isinstance(error, OSError):
        return "certificate or private key file could not be opened"
    return "certificate or private key validation failed"


def _configured_lan_origin(settings: AppSettings, scheme: str) -> str:
    configured = str(getattr(settings, "lan_public_base_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"{scheme}://{_lan_ip()}:{_backend_port()}"


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _backend_port() -> int:
    from app.config import get_env

    return int(get_env("LENGRVIS_BACKEND_PORT") or "8000")


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value).timestamp()
