from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core import db
from app.core.audit import record
from app.core.schemas import now_iso
from app.security.mobile_jwt import MOBILE_TOKEN_TTL_SECONDS, issue_mobile_token

MOBILE_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
MOBILE_REFRESH_TOKEN_PREFIX = "lrt"  # noqa: S105 - opaque token format label, not a secret.
MOBILE_REFRESH_TOKEN_SECRET_BYTES = 32


class DeviceCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    device_id: str
    credential_type: Literal["paired_device"] = "paired_device"
    status: Literal["active", "revoked"] = "active"
    public_key_thumbprint: str = ""
    hardware_backed: bool = False
    attestation_verified: bool = False
    created_at: str
    updated_at: str
    last_used_at: str = ""
    revoked_at: str = ""


class TokenFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    device_id: str
    credential_id: str
    status: Literal["active", "revoked", "expired", "compromised"] = "active"
    current_generation: int = Field(default=0, ge=0)
    expires_at: str
    created_at: str
    updated_at: str
    last_rotated_at: str = ""
    revoked_at: str = ""
    reuse_detected_at: str = ""
    revocation_reason: str = ""


class MobileSessionTokens(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - protocol token type, not a secret.
    expires_in: int = Field(ge=1)
    expires_at: str
    refresh_token: str
    refresh_expires_in: int = Field(ge=1)
    refresh_expires_at: str
    token_family_id: str
    device_credential_id: str


def create_mobile_session_locked(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    device_name: str,
    token_epoch: int,
    scopes: list[str],
    scope_ttl: dict[str, int] | None = None,
    refresh_expires_in_seconds: int = MOBILE_REFRESH_TOKEN_TTL_SECONDS,
) -> MobileSessionTokens:
    timestamp = now_iso()
    now = _parse_iso(timestamp)
    refresh_ttl = max(1, min(int(refresh_expires_in_seconds), MOBILE_REFRESH_TOKEN_TTL_SECONDS))
    refresh_expires_at = (now + timedelta(seconds=refresh_ttl)).isoformat()

    credential = DeviceCredential(
        id=f"devcred_{secrets.token_hex(12)}",
        device_id=device_id,
        created_at=timestamp,
        updated_at=timestamp,
    )
    family = TokenFamily(
        id=f"tf_{secrets.token_hex(16)}",
        device_id=device_id,
        credential_id=credential.id,
        expires_at=refresh_expires_at,
        created_at=timestamp,
        updated_at=timestamp,
    )
    refresh_token, refresh_record = _new_refresh_token_record(family, generation=0, timestamp=timestamp)
    _insert_device_credential_locked(conn, credential)
    _insert_token_family_locked(conn, family)
    _insert_refresh_token_locked(conn, refresh_record)

    access_ttl = min(MOBILE_TOKEN_TTL_SECONDS, refresh_ttl)
    access_expires_at = (now + timedelta(seconds=access_ttl)).isoformat()
    access_token = issue_mobile_token(
        device_id=device_id,
        device_name=device_name,
        expires_in_seconds=access_ttl,
        scope=scopes,
        scope_ttl=scope_ttl,
        token_epoch=token_epoch,
        family_id=family.id,
        credential_id=credential.id,
    )
    return MobileSessionTokens(
        token=access_token,
        expires_in=access_ttl,
        expires_at=access_expires_at,
        refresh_token=refresh_token,
        refresh_expires_in=refresh_ttl,
        refresh_expires_at=refresh_expires_at,
        token_family_id=family.id,
        device_credential_id=credential.id,
    )


def create_mobile_session(
    *,
    device_id: str,
    device_name: str,
    scopes: list[str],
    scope_ttl: dict[str, int] | None = None,
) -> MobileSessionTokens:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Mobile device is not paired")
        device = json.loads(row["data"])
        if str(device.get("status") or "active").lower() != "active":
            raise HTTPException(status_code=401, detail="Mobile device has been revoked")
        return create_mobile_session_locked(
            conn,
            device_id=device_id,
            device_name=device_name,
            token_epoch=int(device.get("token_epoch") or 0),
            scopes=scopes,
            scope_ttl=scope_ttl,
        )


def rotate_mobile_refresh_token(
    refresh_token: str,
    *,
    scopes: list[str],
    scope_ttl: dict[str, int] | None = None,
) -> tuple[MobileSessionTokens, dict[str, Any]]:
    token_id, normalized_token = _parse_refresh_token(refresh_token)
    timestamp = now_iso()
    now = _parse_iso(timestamp)
    reuse_payload: dict[str, Any] | None = None
    rejection_detail = ""
    session: MobileSessionTokens | None = None
    device_payload: dict[str, Any] = {}

    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        token_row = conn.execute(
            "SELECT * FROM mobile_refresh_tokens WHERE id = ?",
            (token_id,),
        ).fetchone()
        if not token_row or not secrets.compare_digest(
            str(token_row["secret_hash"]),
            _refresh_token_hash(normalized_token),
        ):
            rejection_detail = "Invalid mobile refresh token"
        else:
            family_row = conn.execute("SELECT * FROM token_families WHERE id = ?", (token_row["family_id"],)).fetchone()
            device_row = conn.execute(
                "SELECT data FROM mobile_devices WHERE id = ?",
                (token_row["device_id"],),
            ).fetchone()
            credential_row = (
                conn.execute("SELECT * FROM device_credentials WHERE id = ?", (family_row["credential_id"],)).fetchone()
                if family_row
                else None
            )
            if not family_row or not device_row or not credential_row:
                rejection_detail = "Mobile refresh token binding is invalid"
            else:
                family = _token_family_from_row(family_row)
                credential = _device_credential_from_row(credential_row)
                device_payload = json.loads(device_row["data"])
                current_status = str(token_row["status"] or "")
                device_active = str(device_payload.get("status") or "active").lower() == "active"
                if current_status == "rotated":
                    _mark_refresh_reuse_locked(conn, family, device_payload, timestamp=timestamp)
                    reuse_payload = {
                        "device_id": family.device_id,
                        "family_id": family.id,
                        "generation": int(token_row["generation"]),
                    }
                elif current_status != "active" or family.status != "active" or credential.status != "active":
                    rejection_detail = "Mobile refresh token has been revoked"
                elif not device_active:
                    rejection_detail = "Mobile device has been revoked"
                elif _parse_iso(str(token_row["expires_at"])) <= now or _parse_iso(family.expires_at) <= now:
                    _expire_token_family_locked(conn, family, timestamp=timestamp)
                    rejection_detail = "Mobile refresh token expired"
                elif int(token_row["generation"]) != family.current_generation:
                    _mark_refresh_reuse_locked(conn, family, device_payload, timestamp=timestamp)
                    reuse_payload = {
                        "device_id": family.device_id,
                        "family_id": family.id,
                        "generation": int(token_row["generation"]),
                    }
                else:
                    next_generation = family.current_generation + 1
                    next_refresh_token, next_record = _new_refresh_token_record(
                        family,
                        generation=next_generation,
                        timestamp=timestamp,
                    )
                    conn.execute(
                        """
                        UPDATE mobile_refresh_tokens
                        SET status = 'rotated', used_at = ?, replaced_by_id = ?, updated_at = ?, data = ?
                        WHERE id = ? AND status = 'active'
                        """,
                        (
                            timestamp,
                            next_record["id"],
                            timestamp,
                            _refresh_token_data(
                                token_id=str(token_row["id"]),
                                family_id=family.id,
                                device_id=family.device_id,
                                generation=int(token_row["generation"]),
                                status="rotated",
                                expires_at=str(token_row["expires_at"]),
                                created_at=str(token_row["created_at"]),
                                updated_at=timestamp,
                                used_at=timestamp,
                                replaced_by_id=next_record["id"],
                            ),
                            token_id,
                        ),
                    )
                    _insert_refresh_token_locked(conn, next_record)
                    family.current_generation = next_generation
                    family.updated_at = timestamp
                    family.last_rotated_at = timestamp
                    _update_token_family_locked(conn, family)
                    credential.updated_at = timestamp
                    credential.last_used_at = timestamp
                    _update_device_credential_locked(conn, credential)

                    family_remaining = max(1, int((_parse_iso(family.expires_at) - now).total_seconds()))
                    access_ttl = min(MOBILE_TOKEN_TTL_SECONDS, family_remaining)
                    access_expires_at = (now + timedelta(seconds=access_ttl)).isoformat()
                    access_token = issue_mobile_token(
                        device_id=family.device_id,
                        device_name=str(device_payload.get("device_name") or "Android device"),
                        expires_in_seconds=access_ttl,
                        scope=scopes,
                        scope_ttl=scope_ttl,
                        token_epoch=int(device_payload.get("token_epoch") or 0),
                        family_id=family.id,
                        credential_id=credential.id,
                    )
                    session = MobileSessionTokens(
                        token=access_token,
                        expires_in=access_ttl,
                        expires_at=access_expires_at,
                        refresh_token=next_refresh_token,
                        refresh_expires_in=family_remaining,
                        refresh_expires_at=family.expires_at,
                        token_family_id=family.id,
                        device_credential_id=credential.id,
                    )

    if reuse_payload is not None:
        record("mobile.session.refresh_reuse_detected", "MobileIdentity", reuse_payload)
        raise HTTPException(
            status_code=401,
            detail="Mobile refresh token reuse detected; session family revoked",
        )
    if rejection_detail:
        raise HTTPException(status_code=401, detail=rejection_detail)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid mobile refresh token")
    record(
        "mobile.session.refresh_rotated",
        "MobileIdentity",
        {
            "device_id": device_payload.get("device_id") or device_payload.get("id") or "",
            "family_id": session.token_family_id,
        },
    )
    return session, device_payload


def revoke_device_token_families_locked(
    conn: sqlite3.Connection,
    device_id: str,
    *,
    timestamp: str,
    reason: str,
) -> None:
    rows = conn.execute("SELECT * FROM token_families WHERE device_id = ?", (device_id,)).fetchall()
    for row in rows:
        family = _token_family_from_row(row)
        if family.status == "active":
            family.status = "revoked"
            family.revoked_at = timestamp
            family.updated_at = timestamp
            family.revocation_reason = reason
            _update_token_family_locked(conn, family)
        conn.execute(
            """
            UPDATE mobile_refresh_tokens
            SET status = CASE WHEN status = 'active' THEN 'revoked' ELSE status END,
                updated_at = ?
            WHERE family_id = ?
            """,
            (timestamp, family.id),
        )


def revoke_device_credentials_locked(conn: sqlite3.Connection, device_id: str, *, timestamp: str) -> None:
    rows = conn.execute("SELECT * FROM device_credentials WHERE device_id = ?", (device_id,)).fetchall()
    for row in rows:
        credential = _device_credential_from_row(row)
        if credential.status == "revoked":
            continue
        credential.status = "revoked"
        credential.revoked_at = timestamp
        credential.updated_at = timestamp
        _update_device_credential_locked(conn, credential)


def _mark_refresh_reuse_locked(
    conn: sqlite3.Connection,
    family: TokenFamily,
    device: dict[str, Any],
    *,
    timestamp: str,
) -> None:
    family.status = "compromised"
    family.reuse_detected_at = timestamp
    family.revoked_at = timestamp
    family.updated_at = timestamp
    family.revocation_reason = "refresh_token_reuse"
    _update_token_family_locked(conn, family)
    conn.execute(
        "UPDATE mobile_refresh_tokens SET status = 'revoked', updated_at = ? WHERE family_id = ?",
        (timestamp, family.id),
    )
    device["token_epoch"] = int(device.get("token_epoch") or 0) + 1
    device["remote_input_grants"] = _revoked_remote_input_grants(device, timestamp)
    device.pop("push_subscription", None)
    device["updated_at"] = timestamp
    conn.execute(
        "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
        (json.dumps(device, ensure_ascii=False), timestamp, family.device_id),
    )


def _expire_token_family_locked(conn: sqlite3.Connection, family: TokenFamily, *, timestamp: str) -> None:
    family.status = "expired"
    family.revoked_at = timestamp
    family.updated_at = timestamp
    family.revocation_reason = "expired"
    _update_token_family_locked(conn, family)
    conn.execute(
        "UPDATE mobile_refresh_tokens SET status = 'expired', updated_at = ? WHERE family_id = ?",
        (timestamp, family.id),
    )


def _new_refresh_token_record(
    family: TokenFamily,
    *,
    generation: int,
    timestamp: str,
) -> tuple[str, dict[str, Any]]:
    token_id = f"mrt_{secrets.token_hex(16)}"
    secret = secrets.token_urlsafe(MOBILE_REFRESH_TOKEN_SECRET_BYTES)
    refresh_token = f"{MOBILE_REFRESH_TOKEN_PREFIX}.{token_id}.{secret}"
    record_data = {
        "id": token_id,
        "family_id": family.id,
        "device_id": family.device_id,
        "generation": generation,
        "secret_hash": _refresh_token_hash(refresh_token),
        "status": "active",
        "expires_at": family.expires_at,
        "created_at": timestamp,
        "updated_at": timestamp,
        "used_at": None,
        "replaced_by_id": None,
    }
    record_data["data"] = _refresh_token_data(
        token_id=token_id,
        family_id=family.id,
        device_id=family.device_id,
        generation=generation,
        status="active",
        expires_at=family.expires_at,
        created_at=timestamp,
        updated_at=timestamp,
        used_at=None,
        replaced_by_id=None,
    )
    return refresh_token, record_data


def _parse_refresh_token(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip()
    parts = normalized.split(".")
    if len(parts) != 3 or parts[0] != MOBILE_REFRESH_TOKEN_PREFIX:
        raise HTTPException(status_code=401, detail="Invalid mobile refresh token")
    token_id = parts[1]
    if not token_id.startswith("mrt_") or not parts[2]:
        raise HTTPException(status_code=401, detail="Invalid mobile refresh token")
    return token_id, normalized


def _refresh_token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refresh_token_data(
    *,
    token_id: str,
    family_id: str,
    device_id: str,
    generation: int,
    status: str,
    expires_at: str,
    created_at: str,
    updated_at: str,
    used_at: str | None,
    replaced_by_id: str | None,
) -> str:
    return json.dumps(
        {
            "id": token_id,
            "family_id": family_id,
            "device_id": device_id,
            "generation": generation,
            "status": status,
            "expires_at": expires_at,
            "created_at": created_at,
            "updated_at": updated_at,
            "used_at": used_at,
            "replaced_by_id": replaced_by_id,
        },
        ensure_ascii=False,
    )


def _insert_device_credential_locked(conn: sqlite3.Connection, credential: DeviceCredential) -> None:
    conn.execute(
        """
        INSERT INTO device_credentials
            (id, device_id, credential_type, status, data, created_at, updated_at, revoked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            credential.id,
            credential.device_id,
            credential.credential_type,
            credential.status,
            credential.model_dump_json(),
            credential.created_at,
            credential.updated_at,
            credential.revoked_at or None,
        ),
    )


def _update_device_credential_locked(conn: sqlite3.Connection, credential: DeviceCredential) -> None:
    conn.execute(
        """
        UPDATE device_credentials
        SET status = ?, data = ?, updated_at = ?, revoked_at = ?
        WHERE id = ?
        """,
        (
            credential.status,
            credential.model_dump_json(),
            credential.updated_at,
            credential.revoked_at or None,
            credential.id,
        ),
    )


def _insert_token_family_locked(conn: sqlite3.Connection, family: TokenFamily) -> None:
    conn.execute(
        """
        INSERT INTO token_families
            (id, device_id, credential_id, status, current_generation, expires_at, data,
             created_at, updated_at, revoked_at, reuse_detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            family.id,
            family.device_id,
            family.credential_id,
            family.status,
            family.current_generation,
            family.expires_at,
            family.model_dump_json(),
            family.created_at,
            family.updated_at,
            family.revoked_at or None,
            family.reuse_detected_at or None,
        ),
    )


def _update_token_family_locked(conn: sqlite3.Connection, family: TokenFamily) -> None:
    conn.execute(
        """
        UPDATE token_families
        SET status = ?, current_generation = ?, data = ?, updated_at = ?, revoked_at = ?, reuse_detected_at = ?
        WHERE id = ?
        """,
        (
            family.status,
            family.current_generation,
            family.model_dump_json(),
            family.updated_at,
            family.revoked_at or None,
            family.reuse_detected_at or None,
            family.id,
        ),
    )


def _insert_refresh_token_locked(conn: sqlite3.Connection, refresh_record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO mobile_refresh_tokens
            (id, family_id, device_id, generation, secret_hash, status, expires_at, data,
             created_at, updated_at, used_at, replaced_by_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            refresh_record["id"],
            refresh_record["family_id"],
            refresh_record["device_id"],
            refresh_record["generation"],
            refresh_record["secret_hash"],
            refresh_record["status"],
            refresh_record["expires_at"],
            refresh_record["data"],
            refresh_record["created_at"],
            refresh_record["updated_at"],
            refresh_record["used_at"],
            refresh_record["replaced_by_id"],
        ),
    )


def _device_credential_from_row(row: sqlite3.Row) -> DeviceCredential:
    return DeviceCredential.model_validate_json(str(row["data"]))


def _token_family_from_row(row: sqlite3.Row) -> TokenFamily:
    return TokenFamily.model_validate_json(str(row["data"]))


def _revoked_remote_input_grants(device: dict[str, Any], timestamp: str) -> list[dict[str, Any]]:
    grants = device.get("remote_input_grants")
    if not isinstance(grants, list):
        return []
    revoked: list[dict[str, Any]] = []
    for raw in grants:
        if not isinstance(raw, dict):
            continue
        grant = dict(raw)
        if str(grant.get("status") or "active").lower() == "active":
            grant["status"] = "revoked"
            grant["revoked_at"] = timestamp
        revoked.append(grant)
    return revoked


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=401, detail="Mobile session timestamp is invalid") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
