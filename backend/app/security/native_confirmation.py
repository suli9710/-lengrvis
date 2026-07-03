from __future__ import annotations

import base64
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import HTTPException

from app.config import env_flag, get_base_settings, get_env
from app.core import db

NATIVE_CONFIRMATION_SECRET_ENV = "LENGRVIS_NATIVE_CONFIRMATION_SECRET"  # noqa: S105
NATIVE_CONFIRMATION_PUBLIC_KEY_ENV = "LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY"
NATIVE_CONFIRMATION_PUBLIC_KEY_FILE = "native_confirmation_public.key"
NATIVE_CONFIRMATION_ID_HEADER = "x-lengrvis-native-confirmation-id"
NATIVE_CONFIRMATION_TIMESTAMP_HEADER = "x-lengrvis-native-confirmation-timestamp"
NATIVE_CONFIRMATION_SIGNATURE_HEADER = "x-lengrvis-native-confirmation-signature"
NATIVE_CONFIRMATION_MAX_SKEW_SECONDS = 120
NATIVE_CONFIRMATION_CHALLENGE_TTL_SECONDS = 120
NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX_ENV = "LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX"
NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS_ENV = (
    "LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS"
)
_DEFAULT_CHALLENGE_RATE_LIMIT_MAX = 10
_DEFAULT_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class NativeConfirmationChallenge:
    confirmation_id: str
    action: str
    approval_id: str
    preview_hmac: str
    expires_at_epoch: int

    @property
    def signing_payload(self) -> str:
        return native_confirmation_signing_payload(
            action=self.action,
            approval_id=self.approval_id,
            confirmation_id=self.confirmation_id,
            preview_hmac=self.preview_hmac,
            expires_at=str(self.expires_at_epoch),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "action": self.action,
            "approval_id": self.approval_id,
            "preview_hmac": self.preview_hmac,
            "expires_at_epoch": self.expires_at_epoch,
            "signing_payload": self.signing_payload,
        }


def require_native_confirmation(
    *,
    action: str,
    approval_id: str,
    confirmation_id: str,
    timestamp: str,
    signature: str,
    preview_hmac: str = "",
) -> dict[str, Any]:
    public_key = native_confirmation_public_key()
    if public_key:
        return _require_signed_challenge(
            public_key=public_key,
            action=action,
            approval_id=approval_id,
            confirmation_id=confirmation_id,
            timestamp=timestamp,
            signature=signature,
            preview_hmac=preview_hmac,
        )
    if not _legacy_hmac_allowed():
        raise HTTPException(status_code=403, detail="Native confirmation verifier is unavailable.")
    return _require_legacy_hmac_confirmation(
        action=action,
        approval_id=approval_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
    )


def enforce_native_confirmation_challenge_rate_limit(
    scope: str,
    *,
    now: float | None = None,
    db_path: Path | None = None,
    maximum: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """Cross-process rate limit for native confirmation challenge creation."""
    current = time.time() if now is None else now
    window = (
        window_seconds
        if window_seconds is not None
        else _env_int(
            NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS_ENV,
            _DEFAULT_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS,
        )
    )
    max_val = (
        maximum
        if maximum is not None
        else _env_int(NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX_ENV, _DEFAULT_CHALLENGE_RATE_LIMIT_MAX)
    )
    if max_val <= 0:
        return
    key = (scope or "unknown").strip() or "unknown"
    cutoff = current - max(1, window)
    path = db_path or _challenge_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_challenge_rate_limit_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM native_confirmation_challenge_rate_limits WHERE attempted_at <= ?", (cutoff,))
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM native_confirmation_challenge_rate_limits
            WHERE scope = ? AND attempted_at > ?
            """,
            (key, cutoff),
        ).fetchone()[0]
        if int(count) >= max_val:
            conn.rollback()
            raise HTTPException(
                status_code=429,
                detail="Too many native confirmation challenge requests. Try again later.",
            )
        conn.execute(
            "INSERT INTO native_confirmation_challenge_rate_limits(scope, attempted_at) VALUES (?, ?)",
            (key, current),
        )
        conn.commit()


def create_native_confirmation_challenge(
    *,
    action: str,
    approval_id: str,
    preview_hmac: str = "",
    now: int | None = None,
) -> dict[str, Any]:
    normalized_action = _clean_action(action)
    normalized_approval_id = str(approval_id or "").strip()
    if not normalized_approval_id:
        raise HTTPException(status_code=422, detail="Approval id is required.")
    _evict_expired_challenges(now=now)
    challenge = NativeConfirmationChallenge(
        confirmation_id=secrets.token_urlsafe(24),
        action=normalized_action,
        approval_id=normalized_approval_id,
        preview_hmac=str(preview_hmac or "").strip(),
        expires_at_epoch=int(now or time.time()) + NATIVE_CONFIRMATION_CHALLENGE_TTL_SECONDS,
    )
    _store_challenge(challenge)
    payload = challenge.public_dict()
    payload["public_key_fingerprint"] = native_confirmation_public_key_fingerprint()
    return payload


def native_confirmation_public_key() -> str:
    configured = str(get_env(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV) or "").strip()
    if configured:
        return configured
    return _local_native_confirmation_public_key()


def _local_native_confirmation_public_key() -> str:
    try:
        key_path = Path(get_base_settings().data_dir) / NATIVE_CONFIRMATION_PUBLIC_KEY_FILE
        if not key_path.is_file():
            return ""
        return key_path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001 - broad-exception-boundary: verifier should fail closed when the key is unavailable.
        return ""


def native_confirmation_public_key_fingerprint() -> str:
    public_key = native_confirmation_public_key()
    if not public_key:
        return ""
    return sha256(public_key.encode("utf-8")).hexdigest()[:16]


def native_confirmation_signing_payload(
    *,
    action: str,
    approval_id: str,
    confirmation_id: str,
    preview_hmac: str,
    expires_at: str,
) -> str:
    return "\n".join(
        [
            "approval-v2",
            _clean_action(action),
            str(approval_id or "").strip(),
            str(confirmation_id or "").strip(),
            str(preview_hmac or "").strip(),
            str(expires_at or "").strip(),
        ]
    )


def native_confirmation_secret() -> str:
    if not _legacy_hmac_allowed():
        return ""
    return str(get_env(NATIVE_CONFIRMATION_SECRET_ENV) or "").strip()


def native_confirmation_signature(
    *,
    secret: str,
    action: str,
    approval_id: str,
    confirmation_id: str,
    timestamp: str,
) -> str:
    body = "\n".join(["approval", action, approval_id, confirmation_id, timestamp])
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()


def _require_signed_challenge(
    *,
    public_key: str,
    action: str,
    approval_id: str,
    confirmation_id: str,
    timestamp: str,
    signature: str,
    preview_hmac: str,
) -> dict[str, Any]:
    if not confirmation_id or not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Native confirmation proof is required.")
    try:
        expires_at = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Native confirmation timestamp is invalid.") from exc
    challenge = _pop_stored_challenge(confirmation_id)
    if challenge is None:
        raise HTTPException(status_code=403, detail="Native confirmation challenge is invalid or already used.")
    if int(time.time()) > challenge.expires_at_epoch:
        raise HTTPException(status_code=403, detail="Native confirmation proof is expired.")
    if expires_at != challenge.expires_at_epoch:
        raise HTTPException(status_code=403, detail="Native confirmation timestamp is invalid.")
    if challenge.action != _clean_action(action) or challenge.approval_id != str(approval_id or "").strip():
        raise HTTPException(status_code=403, detail="Native confirmation challenge does not match this approval.")
    if challenge.preview_hmac != str(preview_hmac or "").strip():
        raise HTTPException(status_code=403, detail="Native confirmation preview changed.")
    try:
        decoded_signature = _b64url_decode(signature)
        parsed_key = _load_public_key(public_key)
        parsed_key.verify(decoded_signature, challenge.signing_payload.encode("utf-8"))
    except (InvalidSignature, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Native confirmation proof is invalid.") from exc
    return {
        "desktop_native_confirmed": True,
        "confirmation_id": confirmation_id,
        "confirmed_at_epoch": int(time.time()),
        "challenge_expires_at_epoch": expires_at,
        "public_key_fingerprint": native_confirmation_public_key_fingerprint(),
    }


def _require_legacy_hmac_confirmation(
    *,
    action: str,
    approval_id: str,
    confirmation_id: str,
    timestamp: str,
    signature: str,
) -> dict[str, Any]:
    secret = native_confirmation_secret()
    if not secret:
        raise HTTPException(status_code=403, detail="Native confirmation verifier is unavailable.")
    if not confirmation_id or not timestamp or not signature:
        raise HTTPException(status_code=403, detail="Native confirmation proof is required.")
    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Native confirmation timestamp is invalid.") from exc
    now = int(time.time())
    if abs(now - timestamp_int) > NATIVE_CONFIRMATION_MAX_SKEW_SECONDS:
        raise HTTPException(status_code=403, detail="Native confirmation proof is expired.")
    expected = native_confirmation_signature(
        secret=secret,
        action=action,
        approval_id=approval_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
    )
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Native confirmation proof is invalid.")
    return {
        "desktop_native_confirmed": True,
        "confirmation_id": confirmation_id,
        "confirmed_at_epoch": timestamp_int,
        "legacy_hmac": True,
    }


def _legacy_hmac_allowed() -> bool:
    if not str(get_env(NATIVE_CONFIRMATION_SECRET_ENV) or "").strip():
        return False
    return env_flag("LENGRVIS_TEST") or bool(str(get_env("PYTEST_CURRENT_TEST") or "").strip())


def _clean_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="Native confirmation action is invalid.")
    return normalized


def _store_challenge(challenge: NativeConfirmationChallenge) -> None:
    path = _challenge_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_challenge_table(conn)
        conn.execute(
            """
            INSERT INTO native_confirmation_challenges(
                confirmation_id, action, approval_id, preview_hmac, expires_at_epoch
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                challenge.confirmation_id,
                challenge.action,
                challenge.approval_id,
                challenge.preview_hmac,
                challenge.expires_at_epoch,
            ),
        )


def _pop_stored_challenge(confirmation_id: str) -> NativeConfirmationChallenge | None:
    normalized_id = str(confirmation_id or "").strip()
    if not normalized_id:
        return None
    _evict_expired_challenges()
    path = _challenge_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_challenge_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT action, approval_id, preview_hmac, expires_at_epoch
            FROM native_confirmation_challenges
            WHERE confirmation_id = ?
            """,
            (normalized_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        conn.execute(
            "DELETE FROM native_confirmation_challenges WHERE confirmation_id = ?",
            (normalized_id,),
        )
        conn.commit()
        return NativeConfirmationChallenge(
            confirmation_id=normalized_id,
            action=str(row[0]),
            approval_id=str(row[1]),
            preview_hmac=str(row[2]),
            expires_at_epoch=int(row[3]),
        )


def _evict_expired_challenges(*, now: int | None = None) -> None:
    moment = int(now or time.time())
    path = _challenge_db_path()
    if not path.exists():
        return
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_challenge_table(conn)
        conn.execute(
            "DELETE FROM native_confirmation_challenges WHERE expires_at_epoch < ?",
            (moment,),
        )


def _ensure_challenge_rate_limit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS native_confirmation_challenge_rate_limits (
            scope TEXT NOT NULL,
            attempted_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_native_confirmation_challenge_rate_limits_scope_time
        ON native_confirmation_challenge_rate_limits(scope, attempted_at)
        """
    )


def _ensure_challenge_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS native_confirmation_challenges (
            confirmation_id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            preview_hmac TEXT NOT NULL,
            expires_at_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_native_confirmation_challenges_expires
        ON native_confirmation_challenges(expires_at_epoch)
        """
    )


def _challenge_db_path() -> Path:
    db.init_db()
    return db.db_path()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def _load_public_key(value: str) -> Ed25519PublicKey:
    raw = _b64url_decode(value)
    if len(raw) == 32:
        return Ed25519PublicKey.from_public_bytes(raw)
    key = load_der_public_key(raw)
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Native confirmation public key must be Ed25519.")
    return key
