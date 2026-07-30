from __future__ import annotations

import json
import secrets
import threading
import time
from hashlib import sha256
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import now_iso
from app.services.mobile_pairing_common import mobile_device_trust_metadata
from app.services.mobile_pairing_transport import _parse_iso

PAIR_CODE_TTL_SECONDS = 300
PAIR_CODE_HEX_LENGTH = 8
PAIR_CLAIM_SECRET_BYTES = 32
PAIR_CONFIRM_FAILURE_LIMIT = 8
PAIR_CONFIRM_FAILURE_WINDOW_SECONDS = 60

PAIR_CONFIRM_FAILURES: dict[str, list[float]] = {}
PAIR_CONFIRM_FAILURES_LOCK = threading.Lock()


def write_pairing_record(record: dict[str, Any]) -> None:
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


def load_pairing_record(code: str) -> dict[str, Any] | None:
    return db.fetch_one("mobile_pairings", code)


def expire_pairing_record(record: dict[str, Any]) -> None:
    updated = dict(record)
    updated["status"] = "expired"
    updated["updated_at"] = now_iso()
    write_pairing_record(updated)


def expire_stale_pairings() -> None:
    now = time.time()
    for pairing_record in db.fetch_many("mobile_pairings", limit=500):
        if pairing_record.get("status") != "pending":
            continue
        expires_at = _parse_iso(str(pairing_record.get("expires_at") or ""))
        if expires_at <= now:
            expire_pairing_record(pairing_record)


def upsert_mobile_device(*, device_id: str, device_name: str) -> None:
    timestamp = now_iso()
    with db.connect() as conn:
        upsert_mobile_device_locked(conn, device_id=device_id, device_name=device_name, timestamp=timestamp)


def upsert_mobile_device_locked(conn: Any, *, device_id: str, device_name: str, timestamp: str) -> None:
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


def unique_code() -> str:
    for _ in range(100):
        code = secrets.token_hex(PAIR_CODE_HEX_LENGTH)
        if not db.fetch_one("mobile_pairings", code):
            return code
    raise HTTPException(status_code=503, detail="Unable to allocate a pairing code")


def new_pairing_claim_secret() -> str:
    return secrets.token_urlsafe(PAIR_CLAIM_SECRET_BYTES)


def hash_pairing_claim_secret(claim_secret: str) -> str:
    return sha256(str(claim_secret or "").encode("utf-8")).hexdigest()


def pairing_claim_secret_matches(record: dict[str, Any], claim_secret: str) -> bool:
    expected_hash = str(record.get("claim_secret_hash") or "").strip()
    supplied = str(claim_secret or "").strip()
    if not expected_hash or not supplied:
        return False
    return secrets.compare_digest(expected_hash, hash_pairing_claim_secret(supplied))


def normalize_code(code: str) -> str:
    return "".join(character for character in code if character.isalnum()).lower()


def safe_device_name(device_name: str) -> str:
    cleaned = "".join(character for character in str(device_name or "") if character.isprintable()).strip()
    return cleaned[:80] or "Android device"


def pairing_rate_key(client_host: str) -> str:
    return (client_host or "unknown").strip().lower() or "unknown"


def raise_if_pairing_rate_limited(rate_key: str) -> None:
    now = time.time()
    with PAIR_CONFIRM_FAILURES_LOCK:
        failures = recent_pairing_failures(rate_key, now)
        if len(failures) >= PAIR_CONFIRM_FAILURE_LIMIT:
            PAIR_CONFIRM_FAILURES[rate_key] = failures
            raise HTTPException(status_code=429, detail="Too many failed pairing attempts. Try again later.")
        PAIR_CONFIRM_FAILURES[rate_key] = failures


def record_pairing_failure(rate_key: str) -> None:
    now = time.time()
    with PAIR_CONFIRM_FAILURES_LOCK:
        failures = recent_pairing_failures(rate_key, now)
        failures.append(now)
        PAIR_CONFIRM_FAILURES[rate_key] = failures


def clear_pairing_failures(rate_key: str) -> None:
    with PAIR_CONFIRM_FAILURES_LOCK:
        PAIR_CONFIRM_FAILURES.pop(rate_key, None)


def recent_pairing_failures(rate_key: str, now: float) -> list[float]:
    cutoff = now - PAIR_CONFIRM_FAILURE_WINDOW_SECONDS
    return [timestamp for timestamp in PAIR_CONFIRM_FAILURES.get(rate_key, []) if timestamp >= cutoff]
