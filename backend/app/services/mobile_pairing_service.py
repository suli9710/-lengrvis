from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.config import AppSettings
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Task, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.execution_stage import ExecutionStage
from app.policy.approval_binding import redacted_preview
from app.policy.redaction import redact_value
from app.security.mobile_jwt import (
    REMOTE_INPUT_SCOPE,
    REMOTE_VIEW_SCOPE,
    TOKEN_SCOPE,
    decode_mobile_token,
    issue_mobile_token,
    mobile_token_scopes,
    new_device_id,
)

PAIR_CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
REMOTE_INPUT_GRANT_TTL_SECONDS = 5 * 60
PAIR_CONFIRM_FAILURE_LIMIT = 8
PAIR_CONFIRM_FAILURE_WINDOW_SECONDS = 60

_PAIR_CONFIRM_FAILURES: dict[str, list[float]] = {}
_PAIR_CONFIRM_FAILURES_LOCK = threading.Lock()


def create_pairing_request() -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()

    now = time.time()
    code = _unique_code()
    record = {
        "id": code,
        "code": code,
        "status": "pending",
        "device_id": "",
        "device_name": "",
        "created_at": _iso(now),
        "expires_at": _iso(now + PAIR_CODE_TTL_SECONDS),
        "used_at": None,
        "updated_at": _iso(now),
        "server": _server_info(),
    }
    _write_pairing_record(record)
    return {
        "code": code,
        "expires_at": record["expires_at"],
        "expires_in": PAIR_CODE_TTL_SECONDS,
        "server": record["server"],
    }


def confirm_pairing(*, code: str, device_name: str, client_host: str = "") -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()

    rate_key = _pairing_rate_key(client_host)
    _raise_if_pairing_rate_limited(rate_key)
    normalized = _normalize_code(code)
    if len(normalized) != 6:
        _record_pairing_failure(rate_key)
        raise HTTPException(status_code=422, detail="Pairing code must be 6 characters")

    result = _redeem_pairing_record(normalized, device_name)
    if result is None:
        _record_pairing_failure(rate_key)
        raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")
    _clear_pairing_failures(rate_key)
    return result


def _redeem_pairing_record(code: str, device_name: str) -> dict[str, Any] | None:
    now = time.time()
    device_id = new_device_id()
    device_name = _safe_device_name(device_name)
    token_scopes = [TOKEN_SCOPE]
    if get_effective_settings().remote_desktop_enabled:
        token_scopes.append(REMOTE_VIEW_SCOPE)
    token = issue_mobile_token(
        device_id=device_id,
        device_name=device_name,
        expires_in_seconds=TOKEN_TTL_SECONDS,
        scope=token_scopes,
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
    return [_safe_approval_payload(row) for row in approvals if _mobile_claims_allow_approval(row, claims)]


def get_approval_detail(approval_id: str, claims: dict[str, Any] | None = None) -> dict[str, Any]:
    approval_data = db.fetch_one("approvals", approval_id)
    if not approval_data:
        raise HTTPException(status_code=404, detail="Approval not found")
    _raise_if_mobile_claims_disallowed(approval_data, claims)

    approval = Approval.model_validate(approval_data)
    task_data = db.fetch_one("tasks", approval.task_id)
    task = Task.model_validate(task_data) if task_data else None
    plan = _latest_plan(task.id if task else approval.task_id)
    approval_payload = _safe_approval_payload(approval.model_dump(mode="json"))
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


def create_remote_input_grant(device_id: str, *, expires_in_seconds: int = REMOTE_INPUT_GRANT_TTL_SECONDS) -> dict[str, Any]:
    if not get_effective_settings().remote_desktop_enabled:
        raise HTTPException(status_code=403, detail="Remote desktop is disabled")
    normalized_id = _text(device_id)
    if not normalized_id:
        raise HTTPException(status_code=422, detail="Missing mobile device id")
    expires_in = _remote_input_grant_ttl(expires_in_seconds)
    now = datetime.now(timezone.utc)
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
    if not get_effective_settings().remote_desktop_enabled:
        raise HTTPException(status_code=403, detail="Remote desktop is disabled")

    device = db.fetch_one("mobile_devices", device_id)
    if not device or str(device.get("status") or "active").lower() != "active":
        raise HTTPException(status_code=401, detail="Mobile device has been revoked")

    for grant in _normalized_remote_input_grants(device):
        if grant["id"] != normalized_grant_id:
            continue
        if grant["status"] != "active":
            raise HTTPException(status_code=401, detail="Remote input grant is not active")
        expires_at = _grant_expires_at(grant)
        remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            raise HTTPException(status_code=401, detail="Remote input grant expired")
        token = issue_mobile_token(
            device_id=device_id,
            device_name=str(device.get("device_name") or claims.get("device_name") or "Android device"),
            expires_in_seconds=min(remaining, REMOTE_INPUT_GRANT_TTL_SECONDS),
            scope=REMOTE_INPUT_SCOPE,
            source="remote_input_grant",
            grant_id=normalized_grant_id,
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

    raise HTTPException(status_code=403, detail="Remote input grant is not available for this device")


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


def validate_mobile_token(token: str) -> dict[str, Any]:
    return decode_mobile_token(token)


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
    for record in db.fetch_many("mobile_pairings", limit=500):
        if record.get("status") != "pending":
            continue
        expires_at = _parse_iso(str(record.get("expires_at") or ""))
        if expires_at <= now:
            _expire_pairing_record(record)


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
    return _mobile_claims_allow_approval(payload, claims)


def raise_if_mobile_claims_disallowed(approval: Approval | dict[str, Any], claims: dict[str, Any] | None) -> None:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    _raise_if_mobile_claims_disallowed(payload, claims)


def _decide_approval(approval_id: str, status: ApprovalStatus, *, claims: dict[str, Any] | None = None) -> Approval:
    existing = db.fetch_one("approvals", approval_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Approval not found")
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
    reason = _mobile_approval_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _mobile_claims_allow_approval(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_approval_denial_reason(approval, claims)


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
    if isinstance(value, (list, tuple, set)):
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
    now = datetime.now(timezone.utc)
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
    }


def _grant_expires_at(grant: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(grant.get("expires_at") or ""))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)


def _latest_plan(task_id: str) -> dict[str, Any] | None:
    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    return plans[0] if plans else None


def _safe_approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    payload = dict(approval)
    payload["message"] = redact_value(payload.get("message") or "")
    payload["diff_preview"] = redacted_preview(payload.get("diff_preview") or {})
    return payload


def _safe_mobile_task(task: Task) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    for key in ("user_goal", "final_summary"):
        payload[key] = redact_value(payload.get(key) or "")
    return payload


def _safe_mobile_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    safe = dict(plan)
    safe["goal"] = redact_value(safe.get("goal") or "")
    safe["assumptions"] = redact_value(safe.get("assumptions") or [])
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
                "description": redact_value(raw_step.get("description") or ""),
                "status": raw_step.get("status") or "",
                "risk_level": raw_step.get("risk_level") or "",
                "requires_approval": bool(raw_step.get("requires_approval")),
                "tool_effects": redact_value(raw_step.get("tool_effects") or []),
                "resource_kinds": redact_value(raw_step.get("resource_kinds") or []),
                "trust_tier": raw_step.get("trust_tier") or "",
                "deferred_tool": bool(raw_step.get("deferred_tool")),
                "expected_observation": redact_value(raw_step.get("expected_observation") or ""),
            }
        )
    safe["steps"] = safe_steps
    return safe


def safe_approval_payload(approval: Approval | dict[str, Any]) -> dict[str, Any]:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _safe_approval_payload(payload)


def _unique_code() -> str:
    for _ in range(100):
        code = secrets.token_hex(3)
        if not db.fetch_one("mobile_pairings", code):
            return code
    raise HTTPException(status_code=503, detail="Unable to allocate a pairing code")


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


def _server_info() -> dict[str, Any]:
    transport = lan_transport_security()
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
    tls_ready = https_enabled and cert_present and key_present
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
        warning = f"LAN HTTPS is enabled but the {' and '.join(missing)} is missing."
        next_action = "Create or point Lengrvis at a local TLS certificate and key, then restart the backend."
    else:
        status = "http_lan_insecure"
        warning = "LAN mobile pairing uses HTTP/ws transport unless HTTPS is explicitly configured."
        next_action = "Use loopback for local testing, or configure LAN TLS before pairing phones on an untrusted network."

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
        "requires_trust": https_enabled,
        "trust_required": https_enabled,
        "trust_model": "local_certificate" if https_enabled else "none",
        "warning": warning,
        "next_action": next_action,
    }


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
    import os

    return int(os.environ.get("LENGRVIS_BACKEND_PORT") or os.environ.get("LENGRVIS_BACKEND_PORT") or os.environ.get("LENGRVIS_BACKEND_PORT") or "8000")


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value).timestamp()
