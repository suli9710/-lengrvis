from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.config_sources import env_flag
from app.core import db
from app.core.approval_observability import (
    ApprovalClaimOutcome,
    observe_atomic_decision,
    record_claim_outcome,
)


def _normalized_approval(data: dict[str, Any]) -> dict[str, Any]:
    from app.core.schemas import Approval

    return Approval.model_validate(data).model_dump(mode="json")


def _approval_expired(data: dict[str, Any], at: str) -> bool:
    from app.core.schemas import approval_is_expired

    return approval_is_expired(data, at=at)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _approval_authorization_error(conn, data: dict[str, Any], consumed_at: str) -> str:
    authorized_at = _parse_timestamp(data.get("authorized_at"))
    auth_context = data.get("auth_context")
    if authorized_at is None and not auth_context:
        if env_flag("LENGRVIS_TEST"):
            return ""
        return "Approval is missing an authentication context."
    if authorized_at is None or not isinstance(auth_context, dict) or not auth_context:
        return "Approval authentication context is incomplete."

    consumed_timestamp = _parse_timestamp(consumed_at)
    if consumed_timestamp is None or authorized_at > consumed_timestamp:
        return "Approval authorization timestamp is invalid."

    channel = str(auth_context.get("channel") or "").strip().lower()
    if channel == "desktop_native":
        return _desktop_authorization_error(auth_context, authorized_at)
    if channel == "mobile":
        return _mobile_authorization_error(conn, data, auth_context, consumed_timestamp)
    return "Approval authentication channel is invalid."


def _desktop_authorization_error(auth_context: dict[str, Any], authorized_at: datetime) -> str:
    from app.security.approval_session import approval_session_authorization_error
    from app.security.native_confirmation import (
        native_confirmation_legacy_hmac_fingerprint,
        native_confirmation_public_key_fingerprint,
    )

    if not str(auth_context.get("confirmation_id") or "").strip():
        return "Desktop confirmation id is missing."
    try:
        confirmed_at = datetime.fromtimestamp(int(auth_context.get("confirmed_at_epoch")), UTC)
    except (TypeError, ValueError, OSError):
        return "Desktop confirmation timestamp is invalid."
    if abs((confirmed_at - authorized_at).total_seconds()) > 5:
        return "Desktop confirmation timestamp does not match the authorization."

    proof_type = str(auth_context.get("proof_type") or "").strip().lower()
    if proof_type == "ed25519":
        try:
            challenge_expires_at = int(auth_context.get("challenge_expires_at_epoch"))
        except (TypeError, ValueError):
            return "Desktop confirmation challenge expiry is invalid."
        if challenge_expires_at < int(confirmed_at.timestamp()):
            return "Desktop confirmation challenge expiry is invalid."
        expected = str(auth_context.get("public_key_fingerprint") or "").strip()
        current = native_confirmation_public_key_fingerprint()
        if not expected or not current or expected != current:
            return "Desktop confirmation key has changed."
    elif proof_type == "legacy_hmac":
        expected = str(auth_context.get("legacy_hmac_fingerprint") or "").strip()
        current = native_confirmation_legacy_hmac_fingerprint()
        if not expected or not current or expected != current:
            return "Desktop confirmation secret has changed."
    else:
        return "Desktop confirmation proof type is invalid."
    return approval_session_authorization_error(auth_context.get("approval_session_generation_fingerprint"))


def _mobile_authorization_error(
    conn,
    approval: dict[str, Any],
    auth_context: dict[str, Any],
    consumed_at: datetime,
) -> str:
    device_id = str(auth_context.get("device_id") or "").strip()
    family_id = str(auth_context.get("token_family_id") or "").strip()
    credential_id = str(auth_context.get("credential_id") or "").strip()
    scopes = auth_context.get("scopes")
    try:
        token_epoch = int(auth_context.get("token_epoch"))
    except (TypeError, ValueError):
        return "Mobile authorization token epoch is invalid."
    raw_family_generation = auth_context.get("family_generation")
    if raw_family_generation is None and env_flag("LENGRVIS_TEST"):
        family_generation = None
    elif (
        isinstance(raw_family_generation, bool)
        or not isinstance(raw_family_generation, int)
        or raw_family_generation < 0
    ):
        return "Mobile authorization family generation is invalid."
    else:
        family_generation = raw_family_generation
    if not device_id or not family_id or not credential_id:
        return "Mobile authorization binding is incomplete."
    if not isinstance(scopes, list) or not all(isinstance(scope, str) and scope.strip() for scope in scopes):
        return "Mobile authorization scopes are invalid."
    required_scopes = {
        str(scope).strip() for scope in approval.get("required_mobile_scopes") or [] if str(scope).strip()
    }
    if not required_scopes.issubset({scope.strip() for scope in scopes}):
        return "Mobile authorization no longer satisfies the approval scopes."

    device_row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
    family = conn.execute(
        "SELECT device_id, credential_id, status, current_generation, expires_at FROM token_families WHERE id = ?",
        (family_id,),
    ).fetchone()
    credential = conn.execute(
        "SELECT device_id, status FROM device_credentials WHERE id = ?",
        (credential_id,),
    ).fetchone()
    if not device_row or not family or not credential:
        return "Mobile authorization binding no longer exists."
    try:
        device = json.loads(device_row["data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return "Mobile device state is invalid."
    if str(device.get("status") or "active").lower() != "active":
        return "Mobile device has been revoked."
    try:
        current_epoch = int(device.get("token_epoch") or 0)
    except (TypeError, ValueError):
        return "Mobile device token epoch is invalid."
    if current_epoch != token_epoch:
        return "Mobile session has been revoked."
    if str(family["device_id"]) != device_id or str(credential["device_id"]) != device_id:
        return "Mobile authorization device binding has changed."
    if str(family["credential_id"]) != credential_id:
        return "Mobile authorization credential binding has changed."
    try:
        current_family_generation = int(family["current_generation"])
    except (TypeError, ValueError):
        return "Mobile token family generation is invalid."
    if family_generation is not None and current_family_generation != family_generation:
        return "Mobile authorization family generation has changed."
    if str(family["status"] or "").lower() != "active":
        return "Mobile token family has been revoked."
    if str(credential["status"] or "").lower() != "active":
        return "Mobile device credential has been revoked."
    family_expires_at = _parse_timestamp(family["expires_at"])
    if family_expires_at is None or family_expires_at <= consumed_at:
        return "Mobile token family has expired."
    return ""


def _expire_approval_locked(
    conn,
    approval_id: str,
    data: dict[str, Any],
    expired_at: str,
    reason: str,
) -> dict[str, Any]:
    data = _normalized_approval(data)
    data["status"] = "expired"
    data["decided_at"] = expired_at
    data["expired_reason"] = reason
    stored = db._json(data)
    conn.execute(
        "UPDATE approvals SET data = ?, status = ? WHERE id = ?",
        (stored, "expired", approval_id),
    )
    db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data


def claim_approval_for_execution(approval_id: str, consumed_at: str) -> dict[str, Any] | None:
    """Atomically mark an approved approval as consumed before side effects run."""
    try:
        result, outcome = _claim_approval_for_execution(approval_id, consumed_at)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: preserve the original claim failure.
        record_claim_outcome("error")
        raise
    record_claim_outcome(outcome)
    return result


def _claim_approval_for_execution(
    approval_id: str,
    consumed_at: str,
) -> tuple[dict[str, Any] | None, ApprovalClaimOutcome]:
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ? AND status = ?",
            (approval_id, "approved"),
        ).fetchone()
        if not row:
            return None, "unavailable"
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = _normalized_approval(json.loads(row["data"]))
        if data.get("consumed_at"):
            return None, "already_consumed"
        if _approval_expired(data, consumed_at):
            _expire_approval_locked(conn, approval_id, data, consumed_at, "Approval authorization expired.")
            return None, "expired"
        # Reading the atomically replaced desktop generation inside this write
        # transaction is the claim's cross-process linearization point. A
        # rotation completed before this read expires the approval; a rotation
        # after it does not retroactively revoke an already-linearized claim.
        # Existing runtime cancel/stop boundaries own any action already in flight.
        authorization_error = _approval_authorization_error(conn, data, consumed_at)
        if authorization_error:
            _expire_approval_locked(conn, approval_id, data, consumed_at, authorization_error)
            return None, "authorization_invalidated"
        data["consumed_at"] = consumed_at
        stored = db._json(data)
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (stored, approval_id, "approved"),
        )
        if cursor.rowcount != 1:
            return None, "conflict"
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data, "claimed"


def expire_approval_if_pending(approval_id: str, expired_at: str, reason: str = "") -> dict[str, Any] | None:
    """Atomically expire one pending, unconsumed approval."""
    return expire_approval_if_unconsumed(approval_id, expired_at, reason, statuses={"pending"})


def expire_stale_approvals(expired_at: str) -> list[dict[str, Any]]:
    expired: list[dict[str, Any]] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, data FROM approvals WHERE status IN (?, ?)",
            ("pending", "approved"),
        ).fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "approvals", row["id"], row["data"])
            data = _normalized_approval(json.loads(row["data"]))
            if data.get("consumed_at") or not _approval_expired(data, expired_at):
                continue
            expired.append(
                _expire_approval_locked(conn, row["id"], data, expired_at, "Approval authorization expired.")
            )
    return expired


def expire_approval_if_unconsumed(
    approval_id: str,
    expired_at: str,
    reason: str = "",
    *,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically expire one unconsumed approval in an allowed status."""
    allowed_statuses = statuses or {"pending", "approved"}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = _normalized_approval(json.loads(row["data"]))
        current_status = str(data.get("status") or "")
        if current_status not in allowed_statuses or data.get("consumed_at"):
            return None
        data["status"] = "expired"
        data["decided_at"] = expired_at
        if reason:
            data["expired_reason"] = reason
        placeholders = ",".join("?" for _ in allowed_statuses)
        query_template = """
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status IN ({status_placeholders})
              AND json_extract(data, '$.consumed_at') IS NULL
        """
        query = query_template.format(status_placeholders=placeholders)  # noqa: S608
        stored = db._json(data)
        cursor = conn.execute(
            query,
            (stored, "expired", approval_id, *sorted(allowed_statuses)),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data


def expire_pending_approvals_for_task(task_id: str, expired_at: str, reason: str = "") -> list[dict[str, Any]]:
    """Atomically expire all pending, unconsumed approvals for a task."""
    expired: list[dict[str, Any]] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, data FROM approvals WHERE task_id = ? AND status = ?",
            (task_id, "pending"),
        ).fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "approvals", row["id"], row["data"])
            data = json.loads(row["data"])
            if data.get("status") != "pending" or data.get("consumed_at"):
                continue
            data["status"] = "expired"
            data["decided_at"] = expired_at
            if reason:
                data["expired_reason"] = reason
            stored = db._json(data)
            cursor = conn.execute(
                """
                UPDATE approvals
                SET data = ?,
                    status = ?
                WHERE id = ?
                  AND status = ?
                  AND json_extract(data, '$.status') = ?
                  AND json_extract(data, '$.consumed_at') IS NULL
                """,
                (stored, "expired", row["id"], "pending", "pending"),
            )
            if cursor.rowcount == 1:
                db._store_sensitive_record_integrity(conn, "approvals", row["id"], stored)
                expired.append(data)
    return expired


def count_pending_remote_input_approvals(grant_id: str, device_id: str) -> int:
    """Count active pending remote-input approvals for one grant/device binding."""
    normalized_grant_id = str(grant_id or "").strip()
    normalized_device_id = str(device_id or "").strip()
    if not normalized_grant_id or not normalized_device_id:
        return 0
    from app.core.schemas import now_iso

    expire_stale_approvals(now_iso())
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, data
            FROM approvals
            WHERE status = ?
              AND json_extract(data, '$.status') = ?
              AND json_extract(data, '$.source') = ?
              AND json_extract(data, '$.source_grant_id') = ?
              AND json_extract(data, '$.source_device_id') = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (
                "pending",
                "pending",
                "remote_input",
                normalized_grant_id,
                normalized_device_id,
            ),
        ).fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "approvals", row["id"], row["data"])
    return len(rows)


@observe_atomic_decision
def decide_approval_atomically(
    approval_id: str,
    status: str,
    decided_at: str,
    *,
    authorized_at: str | None = None,
    auth_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically move a pending, unconsumed approval to a terminal decision."""
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported approval decision status: {status}")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = _normalized_approval(json.loads(row["data"]))
        if data.get("status") != "pending" or data.get("consumed_at"):
            return None
        if _approval_expired(data, decided_at):
            return _expire_approval_locked(
                conn,
                approval_id,
                data,
                decided_at,
                "Approval authorization expired.",
            )
        data["status"] = status
        data["decided_at"] = decided_at
        if authorized_at is not None or auth_context is not None:
            if not authorized_at or not isinstance(auth_context, dict) or not auth_context:
                raise ValueError("Approval authorization requires both authorized_at and auth_context")
            data["authorized_at"] = authorized_at
            data["auth_context"] = dict(auth_context)
        stored = db._json(data)
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.status') = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (stored, status, approval_id, "pending", "pending"),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data


def reauthorize_approval_atomically(
    approval_id: str,
    authorized_at: str,
    auth_context: dict[str, Any],
) -> dict[str, Any] | None:
    """Replace auth evidence for an approved, unconsumed approval without extending its TTL."""
    if not authorized_at or not isinstance(auth_context, dict) or not auth_context:
        raise ValueError("Approval authorization requires both authorized_at and auth_context")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ? AND status = ?",
            (approval_id, "approved"),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = _normalized_approval(json.loads(row["data"]))
        if data.get("consumed_at"):
            return None
        if _approval_expired(data, authorized_at):
            return _expire_approval_locked(
                conn,
                approval_id,
                data,
                authorized_at,
                "Approval authorization expired.",
            )
        candidate = dict(data)
        candidate["authorized_at"] = authorized_at
        candidate["auth_context"] = dict(auth_context)
        authorization_error = _approval_authorization_error(conn, candidate, authorized_at)
        if authorization_error:
            return _expire_approval_locked(
                conn,
                approval_id,
                data,
                authorized_at,
                authorization_error,
            )
        data["authorized_at"] = authorized_at
        data["auth_context"] = dict(auth_context)
        stored = db._json(data)
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (stored, approval_id, "approved"),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data
