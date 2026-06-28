"""Subscription activation client/server helpers.

Activation is intentionally split from runtime entitlement checks:

* the activation server validates a subscription key and returns a signed
  license token;
* the desktop app stores that token and continues to use offline Ed25519
  verification for normal operation.

The activation key is never persisted in clear text. Server storage uses an
HMAC-SHA256 hash keyed by an operator-provided pepper.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.commerce.device_identity import (
    DeviceIdentityError,
    LocalDeviceIdentity,
    collect_activation_device_identity,
)
from app.commerce.device_identity import (
    local_activation_device_id as _local_activation_device_id,
)
from app.commerce.entitlements import Plan, normalize_plan
from app.commerce.licensing import (
    License,
    LicenseError,
    install_license,
    sign_license,
    verify_license,
)
from app.core.errors import AppError

ACTIVATION_BASE_URL_ENV_VAR = "LENGRVIS_ACTIVATION_BASE_URL"
ACTIVATION_TIMEOUT_SECONDS_ENV_VAR = "LENGRVIS_ACTIVATION_TIMEOUT_SECONDS"
ACTIVATION_ALLOW_INSECURE_HTTP_ENV_VAR = "LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP"

ACTIVATION_DB_ENV_VAR = "LENGRVIS_ACTIVATION_DB"
ACTIVATION_KEY_PEPPER_ENV_VAR = "LENGRVIS_ACTIVATION_KEY_PEPPER"
ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY"
ACTIVATION_SIGNING_PRIVATE_KEY_FILE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY_FILE"
ACTIVATION_SIGNING_PASSPHRASE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE"  # noqa: S105
ACTIVATION_SIGNING_PASSPHRASE_FILE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE_FILE"  # noqa: S105
ACTIVATION_ISSUER_ENV_VAR = "LENGRVIS_ACTIVATION_ISSUER"
ACTIVATION_RATE_LIMIT_MAX_ENV_VAR = "LENGRVIS_ACTIVATION_RATE_LIMIT_MAX"
ACTIVATION_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR = "LENGRVIS_ACTIVATION_RATE_LIMIT_WINDOW_SECONDS"

MAX_ACTIVATION_KEY_CHARS = 256
MAX_DEVICE_ID_CHARS = 128
MAX_DEVICE_FINGERPRINT_CHARS = 128
MIN_DEVICE_FINGERPRINT_CHARS = 4
MAX_DEVICE_PROFILE_JSON_CHARS = 2048
MAX_APP_VERSION_CHARS = 64
MAX_NONCE_CHARS = 128
_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_SUBSCRIPTION_STATES = {"active", "trialing"}
_DEFAULT_TIMEOUT_SECONDS = 12.0
_DEFAULT_RATE_LIMIT_MAX = 8
_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 300
_ALLOWED_DEVICE_PROFILE_KEYS = {
    "schema",
    "fingerprint_version",
    "fingerprint",
    "os",
    "arch",
    "os_release",
    "signal_count",
    "signals",
    "install_hash",
    "machine_id_hash",
    "hostname_hash",
    "node_hash",
}

_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}

_UNSET = object()
"""Sentinel distinguishing "field omitted" from an explicit ``None`` (clear)."""


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response: ...


class ActivationError(AppError):
    """Raised when online activation cannot produce a trustworthy license."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "activation_failed",
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@dataclass(frozen=True)
class ActivationRequest:
    activation_key: str
    device_id: str
    app_version: str = ""
    nonce: str = ""
    device_fingerprint: str = ""
    device_profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubscriptionRecord:
    key_hash: str
    plan: Plan
    subscription_id: str
    status: str
    subject: str
    seats: int
    max_devices: int
    expires_at: datetime | None
    renews_at: datetime | None
    cancel_at_period_end: bool
    order_ref: str

    @property
    def device_limit(self) -> int:
        return max(1, int(self.max_devices or self.seats or 1))


@dataclass(frozen=True)
class ActivationResult:
    license_token: str
    license: License
    license_id: str
    plan: Plan
    subscription_id: str
    expires_at: datetime | None
    renews_at: datetime | None
    reused_device: bool


def activate_license_with_server(
    activation_key: str,
    settings: Any,
    *,
    app_version: str = "",
    client: HttpClient | None = None,
    now: datetime | None = None,
) -> License:
    """Activate a subscription key online and persist the returned license."""
    key = _clean_activation_key(activation_key)
    base_url = _activation_base_url()
    identity = _collect_local_identity(settings)
    nonce = secrets.token_urlsafe(24)
    payload = {
        "activation_key": key,
        "device_id": identity.device_id,
        "device_fingerprint": identity.fingerprint,
        "device_profile": identity.profile,
        "app_version": str(app_version or "")[:MAX_APP_VERSION_CHARS],
        "nonce": nonce,
    }
    endpoint = _activation_endpoint(base_url)
    http_client = client or httpx.Client(timeout=_activation_timeout_seconds())
    close_client = client is None
    try:
        response = http_client.post(endpoint, json=payload)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise ActivationError(
            _safe_activation_error_message(exc.response),
            code=_safe_activation_error_code(exc.response),
            status_code=exc.response.status_code,
        ) from exc
    except (httpx.HTTPError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ActivationError(
            "Activation service is unavailable.",
            code="activation_service_unavailable",
            status_code=503,
        ) from exc
    finally:
        if close_client and hasattr(http_client, "close"):
            http_client.close()
    token = str(body.get("license_token") or "").strip() if isinstance(body, dict) else ""
    if not token:
        raise ActivationError(
            "Activation service returned no license.",
            code="activation_malformed_response",
            status_code=502,
        )
    try:
        return install_license(token, settings, now=now)
    except LicenseError as exc:
        raise ActivationError(
            "Activation service returned a license that could not be verified.",
            code=exc.code,
            status_code=exc.status_code,
        ) from exc


def local_activation_device_id(settings: Any) -> str:
    """Return a stable redacted device id for activation requests."""
    try:
        return _local_activation_device_id(settings)
    except DeviceIdentityError as exc:
        raise ActivationError(exc.message, code=exc.code, status_code=exc.status_code) from exc


def _collect_local_identity(settings: Any) -> LocalDeviceIdentity:
    try:
        return collect_activation_device_identity(settings)
    except DeviceIdentityError as exc:
        raise ActivationError(exc.message, code=exc.code, status_code=exc.status_code) from exc
    except Exception as exc:  # noqa: BLE001 - keep raw host/device details out of API errors.
        raise ActivationError(
            "Activation device identity is unavailable.",
            code="activation_device_identity_unavailable",
            status_code=503,
        ) from exc


def initialize_activation_db(path: Path | None = None) -> Path:
    """Create the activation-server SQLite tables if needed."""
    db_path = _activation_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_keys (
                key_hash TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                subscription_id TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                seats INTEGER NOT NULL DEFAULT 1,
                max_devices INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                renews_at TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                order_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activation_devices (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_fingerprint TEXT NOT NULL DEFAULT '',
                device_profile TEXT NOT NULL DEFAULT '{}',
                first_activated_at TEXT NOT NULL,
                last_activated_at TEXT NOT NULL,
                app_version TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (key_hash) REFERENCES subscription_keys(key_hash),
                UNIQUE (key_hash, device_id)
            )
            """
        )
        _ensure_activation_device_columns(conn)
    return db_path


def upsert_subscription_key(
    *,
    activation_key: str,
    plan: str,
    subscription_id: str,
    status: str,
    subject: str = "",
    seats: int = 1,
    max_devices: int | None = None,
    expires_at: datetime | str | None = None,
    renews_at: datetime | str | None = None,
    cancel_at_period_end: bool = False,
    order_ref: str = "",
    db_path: Path | None = None,
    pepper: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create/update one activation key without storing the raw key."""
    normalized_plan = normalize_plan(plan)
    normalized_status = _normalize_subscription_status(status)
    key_hash = hash_activation_key(activation_key, pepper=pepper)
    timestamp = _iso(now or _utc_now())
    seats_value = max(1, int(seats or 1))
    max_devices_value = max(1, int(max_devices or seats_value))
    initialize_activation_db(db_path)
    path = _activation_db_path(db_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO subscription_keys (
                key_hash, plan, subscription_id, status, subject, seats, max_devices,
                expires_at, renews_at, cancel_at_period_end, order_ref, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key_hash) DO UPDATE SET
                plan = excluded.plan,
                subscription_id = excluded.subscription_id,
                status = excluded.status,
                subject = excluded.subject,
                seats = excluded.seats,
                max_devices = excluded.max_devices,
                expires_at = excluded.expires_at,
                renews_at = excluded.renews_at,
                cancel_at_period_end = excluded.cancel_at_period_end,
                order_ref = excluded.order_ref,
                updated_at = excluded.updated_at
            """,
            (
                key_hash,
                normalized_plan.value,
                _safe_label(subscription_id, max_length=128),
                normalized_status,
                _safe_label(subject, max_length=256),
                seats_value,
                max_devices_value,
                _iso_optional(expires_at),
                _iso_optional(renews_at),
                1 if cancel_at_period_end else 0,
                _safe_label(order_ref, max_length=128),
                timestamp,
                timestamp,
            ),
        )
    return {
        "key_hash": key_hash,
        "plan": normalized_plan.value,
        "subscription_id": _safe_label(subscription_id, max_length=128),
        "status": normalized_status,
        "seats": seats_value,
        "max_devices": max_devices_value,
        "expires_at": _iso_optional(expires_at),
        "renews_at": _iso_optional(renews_at),
        "cancel_at_period_end": bool(cancel_at_period_end),
    }


def activate_subscription_key(
    request: ActivationRequest,
    *,
    db_path: Path | None = None,
    private_key: str | None = None,
    private_key_password: bytes | None = None,
    issuer: str | None = None,
    now: datetime | None = None,
) -> ActivationResult:
    """Server-side activation: validate key/device and return a signed license."""
    moment = now or _utc_now()
    key = _clean_activation_key(request.activation_key)
    device_id = _clean_device_id(request.device_id)
    device_fingerprint = _clean_device_fingerprint(request.device_fingerprint)
    device_profile = _clean_device_profile(request.device_profile)
    app_version = _safe_label(request.app_version, max_length=MAX_APP_VERSION_CHARS)
    nonce = _safe_label(request.nonce, max_length=MAX_NONCE_CHARS)
    key_hash = hash_activation_key(key)
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Acquire the write lock before any read so the device-limit count
        # check and the device insert act as one atomic critical section.
        # Without BEGIN IMMEDIATE, two concurrent activations can both pass
        # the COUNT(*) check and each insert a row, busting max_devices
        # (TOCTOU). busy_timeout makes contenders wait instead of erroring.
        # isolation_level=None (autocommit) means the explicit BEGIN IMMEDIATE
        # opens the transaction; we commit explicitly at the end of the block
        # (the context manager also commits on success / rolls back on error).
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        record = _load_subscription_record(conn, key_hash)
        if record is None:
            raise ActivationError("Activation key was not accepted.", code="activation_key_not_found", status_code=404)
        _ensure_subscription_can_activate(record, now=moment)
        device_row = conn.execute(
            """
            SELECT id, device_id, device_fingerprint
            FROM activation_devices
            WHERE key_hash = ? AND device_id = ?
            """,
            (key_hash, device_id),
        ).fetchone()
        if device_fingerprint:
            fingerprint_row = conn.execute(
                """
                SELECT id, device_id, device_fingerprint
                FROM activation_devices
                WHERE key_hash = ? AND device_fingerprint = ?
                """,
                (key_hash, device_fingerprint),
            ).fetchone()
            if fingerprint_row is not None and str(fingerprint_row["device_id"] or "") != device_id:
                raise ActivationError(
                    "Device fingerprint is already bound to another activation; unbind the old device first.",
                    code="activation_device_rebind_requires_unbind",
                    status_code=409,
                )
        reused_device = device_row is not None
        if reused_device:
            license_id = str(device_row["id"])
            stored_device_id = str(device_row["device_id"] or "")
            stored_fingerprint = str(device_row["device_fingerprint"] or "")
            if stored_device_id == device_id and stored_fingerprint and device_fingerprint:
                if not hmac.compare_digest(stored_fingerprint, device_fingerprint):
                    raise ActivationError(
                        "Device fingerprint did not match this activation.",
                        code="activation_device_fingerprint_mismatch",
                        status_code=409,
                    )
            conn.execute(
                """
                UPDATE activation_devices
                SET device_id = ?,
                    device_fingerprint = CASE
                        WHEN ? != '' THEN ?
                        ELSE device_fingerprint
                    END,
                    device_profile = CASE
                        WHEN ? != '{}' THEN ?
                        ELSE device_profile
                    END,
                    last_activated_at = ?,
                    app_version = ?
                WHERE id = ?
                """,
                (
                    device_id,
                    device_fingerprint,
                    device_fingerprint,
                    device_profile,
                    device_profile,
                    _iso(moment),
                    app_version,
                    license_id,
                ),
            )
        else:
            # New device activations must present a non-trivial device
            # fingerprint. The client still self-reports it (no attestation),
            # but a required, charset-validated, per-key-unique fingerprint
            # raises the cost of seat squatting with forged device_ids: each
            # seat now needs a distinct fingerprint (the rebind guard above
            # blocks reusing one fingerprint across device_ids).
            if not device_fingerprint:
                raise ActivationError(
                    "Device fingerprint is required to activate a new device.",
                    code="activation_fingerprint_required",
                    status_code=422,
                )
            if len(device_fingerprint) < MIN_DEVICE_FINGERPRINT_CHARS:
                raise ActivationError(
                    "Device fingerprint is too short.",
                    code="activation_device_fingerprint_invalid",
                    status_code=422,
                )
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM activation_devices WHERE key_hash = ?",
                    (key_hash,),
                ).fetchone()[0]
            )
            if count >= record.device_limit:
                raise ActivationError(
                    "Activation device limit reached.",
                    code="activation_device_limit",
                    status_code=409,
                )
            license_id = _license_id_for_device(key_hash, device_id)
            conn.execute(
                """
                INSERT INTO activation_devices (
                    id, key_hash, device_id, device_fingerprint, device_profile,
                    first_activated_at, last_activated_at, app_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    license_id,
                    key_hash,
                    device_id,
                    device_fingerprint,
                    device_profile,
                    _iso(moment),
                    _iso(moment),
                    app_version,
                ),
            )
        conn.commit()
    token = _sign_activation_license(
        record,
        license_id=license_id,
        device_id=device_id,
        device_fingerprint=device_fingerprint,
        app_version=app_version,
        nonce=nonce,
        private_key=private_key,
        private_key_password=private_key_password,
        issuer=issuer,
        now=moment,
    )
    public_key = _activation_public_key_for_self_check()
    license_ = verify_license(token, public_key, now=moment) if public_key else None
    return ActivationResult(
        license_token=token,
        license=license_ if license_ is not None else License(plan=record.plan, license_id=license_id),
        license_id=license_id,
        plan=record.plan,
        subscription_id=record.subscription_id,
        expires_at=record.expires_at,
        renews_at=record.renews_at,
        reused_device=reused_device,
    )


def enforce_activation_rate_limit(scope: str, *, now: float | None = None) -> None:
    """Simple per-process rate limit for activation attempts."""
    current = time.time() if now is None else now
    window = _env_int(ACTIVATION_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR, _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
    maximum = _env_int(ACTIVATION_RATE_LIMIT_MAX_ENV_VAR, _DEFAULT_RATE_LIMIT_MAX)
    if maximum <= 0:
        return
    key = scope or "unknown"
    cutoff = current - max(1, window)
    bucket = [value for value in _RATE_LIMIT_BUCKETS.get(key, []) if value > cutoff]
    if len(bucket) >= maximum:
        _RATE_LIMIT_BUCKETS[key] = bucket
        raise ActivationError("Too many activation attempts.", code="activation_rate_limited", status_code=429)
    bucket.append(current)
    _RATE_LIMIT_BUCKETS[key] = bucket


def hash_activation_key(activation_key: str, *, pepper: str | None = None) -> str:
    key = _clean_activation_key(activation_key)
    secret = str(pepper if pepper is not None else os.getenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "")).strip()
    if not secret:
        raise ActivationError(
            "Activation key pepper is not configured.",
            code="activation_server_unconfigured",
            status_code=503,
        )
    return hmac.new(secret.encode("utf-8"), key.encode("utf-8"), sha256).hexdigest()


def activation_server_configured() -> bool:
    return bool(
        str(os.getenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "")).strip()
        and _read_activation_private_key(required=False)
    )


def _ensure_activation_device_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(activation_devices)").fetchall()}
    if "device_fingerprint" not in columns:
        conn.execute("ALTER TABLE activation_devices ADD COLUMN device_fingerprint TEXT NOT NULL DEFAULT ''")
    if "device_profile" not in columns:
        conn.execute("ALTER TABLE activation_devices ADD COLUMN device_profile TEXT NOT NULL DEFAULT '{}'")


def _sign_activation_license(
    record: SubscriptionRecord,
    *,
    license_id: str,
    device_id: str,
    device_fingerprint: str,
    app_version: str,
    nonce: str,
    private_key: str | None,
    private_key_password: bytes | None,
    issuer: str | None,
    now: datetime,
) -> str:
    signing_key = private_key if private_key is not None else _read_activation_private_key(required=True)
    password = private_key_password if private_key_password is not None else _read_activation_private_key_password()
    issuer_value = _safe_label(issuer or os.getenv(ACTIVATION_ISSUER_ENV_VAR, "Lengrvis Activation"), max_length=128)
    payload: dict[str, Any] = {
        "schema": 1,
        "license_id": license_id,
        "issuer": issuer_value,
        "subject": record.subject,
        "plan": record.plan.value,
        "seats": record.seats,
        "issued_at": _iso(now),
        "expires_at": _iso(record.expires_at) if record.expires_at else None,
        "subscription_id": record.subscription_id,
        "subscription_status": record.status,
        "renews_at": _iso(record.renews_at) if record.renews_at else None,
        "cancel_at_period_end": record.cancel_at_period_end,
        "order_ref": record.order_ref or None,
        "device_id": device_id,
        "device_fingerprint": device_fingerprint or None,
        "activation": {
            "source": "activation_server",
            "nonce_sha256": sha256(nonce.encode("utf-8")).hexdigest() if nonce else "",
            "app_version": app_version,
        },
    }
    return sign_license(payload, signing_key, password=password)


def _load_subscription_record(conn: sqlite3.Connection, key_hash: str) -> SubscriptionRecord | None:
    row = conn.execute("SELECT * FROM subscription_keys WHERE key_hash = ?", (key_hash,)).fetchone()
    if row is None:
        return None
    return SubscriptionRecord(
        key_hash=key_hash,
        plan=normalize_plan(row["plan"]),
        subscription_id=str(row["subscription_id"] or ""),
        status=_normalize_subscription_status(row["status"]),
        subject=str(row["subject"] or ""),
        seats=max(1, int(row["seats"] or 1)),
        max_devices=max(1, int(row["max_devices"] or row["seats"] or 1)),
        expires_at=_parse_datetime(row["expires_at"]),
        renews_at=_parse_datetime(row["renews_at"]),
        cancel_at_period_end=bool(row["cancel_at_period_end"]),
        order_ref=str(row["order_ref"] or ""),
    )


def _ensure_subscription_can_activate(record: SubscriptionRecord, *, now: datetime) -> None:
    if record.status not in _ALLOWED_SUBSCRIPTION_STATES:
        raise ActivationError(
            "Subscription is not active.",
            code=f"subscription_{record.status}",
            status_code=402,
        )
    if record.expires_at is not None and now >= record.expires_at:
        raise ActivationError("Subscription has expired.", code="subscription_expired", status_code=402)


def _activation_base_url() -> str:
    raw = str(os.getenv(ACTIVATION_BASE_URL_ENV_VAR, "")).strip()
    if not raw:
        raise ActivationError("Activation server is not configured.", code="activation_unconfigured", status_code=503)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ActivationError("Activation server URL is invalid.", code="activation_url_invalid", status_code=503)
    host = (parsed.hostname or "").lower()
    insecure_allowed = str(os.getenv(ACTIVATION_ALLOW_INSECURE_HTTP_ENV_VAR, "")).strip().lower() in _TRUE_VALUES
    if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost", "::1"} and not insecure_allowed:
        raise ActivationError("Activation server must use HTTPS.", code="activation_https_required", status_code=503)
    return raw.rstrip("/")


def _activation_endpoint(base_url: str) -> str:
    return f"{base_url}/api/v1/activations"


def _activation_timeout_seconds() -> float:
    raw = os.getenv(ACTIVATION_TIMEOUT_SECONDS_ENV_VAR, "")
    try:
        return max(1.0, min(60.0, float(raw))) if raw else _DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS


def _activation_db_path(path: Path | None = None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    raw = str(os.getenv(ACTIVATION_DB_ENV_VAR, "")).strip()
    if not raw:
        raise ActivationError(
            "Activation database is not configured.",
            code="activation_server_unconfigured",
            status_code=503,
        )
    return Path(raw).expanduser().resolve()


def _read_activation_private_key(*, required: bool) -> str:
    value = str(os.getenv(ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR, "")).strip()
    if value:
        return value
    path = str(os.getenv(ACTIVATION_SIGNING_PRIVATE_KEY_FILE_ENV_VAR, "")).strip()
    if path:
        try:
            return Path(path).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ActivationError(
                "Activation signing key is unavailable.",
                code="activation_server_unconfigured",
                status_code=503,
            ) from exc
    if required:
        raise ActivationError(
            "Activation signing key is not configured.",
            code="activation_server_unconfigured",
            status_code=503,
        )
    return ""


def _read_activation_private_key_password() -> bytes | None:
    value = str(os.getenv(ACTIVATION_SIGNING_PASSPHRASE_ENV_VAR, "")).strip()
    if value:
        return value.encode("utf-8")
    path = str(os.getenv(ACTIVATION_SIGNING_PASSPHRASE_FILE_ENV_VAR, "")).strip()
    if not path:
        return None
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ActivationError(
            "Activation signing passphrase is unavailable.",
            code="activation_server_unconfigured",
            status_code=503,
        ) from exc
    return text.encode("utf-8") if text else None


def _activation_public_key_for_self_check() -> str:
    return str(os.getenv("LENGRVIS_LICENSE_PUBLIC_KEY", "")).strip()


def _clean_activation_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ActivationError("Activation key is required.", code="activation_key_required", status_code=422)
    if len(text) > MAX_ACTIVATION_KEY_CHARS:
        raise ActivationError("Activation key is too long.", code="activation_key_invalid", status_code=422)
    return text


def _clean_device_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ActivationError("Device id is required.", code="activation_device_required", status_code=422)
    if len(text) > MAX_DEVICE_ID_CHARS:
        raise ActivationError("Device id is too long.", code="activation_device_invalid", status_code=422)
    if any(char.isspace() for char in text):
        raise ActivationError("Device id is invalid.", code="activation_device_invalid", status_code=422)
    return text


def _clean_device_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_DEVICE_FINGERPRINT_CHARS:
        raise ActivationError(
            "Device fingerprint is too long.",
            code="activation_device_fingerprint_invalid",
            status_code=422,
        )
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.")
    if any(char not in allowed for char in text):
        raise ActivationError(
            "Device fingerprint is invalid.",
            code="activation_device_fingerprint_invalid",
            status_code=422,
        )
    return text


def _clean_device_profile(value: Mapping[str, Any] | dict[str, Any] | None) -> str:
    profile = _safe_device_profile(value if isinstance(value, Mapping) else {})
    return json.dumps(profile, sort_keys=True, separators=(",", ":"))


def _safe_device_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, raw in value.items():
        name = str(key or "").strip()
        if name not in _ALLOWED_DEVICE_PROFILE_KEYS:
            continue
        if isinstance(raw, bool):
            result[name] = raw
        elif isinstance(raw, int | float):
            result[name] = raw
        elif isinstance(raw, list):
            result[name] = [_safe_label(item, max_length=64) for item in raw[:16]]
        else:
            result[name] = _safe_label(raw, max_length=128)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_DEVICE_PROFILE_JSON_CHARS:
        compact = {
            key: result[key]
            for key in (
                "schema",
                "fingerprint_version",
                "fingerprint",
                "os",
                "arch",
                "signal_count",
                "signals",
            )
            if key in result
        }
        return compact
    return result


def _license_id_for_device(key_hash: str, device_id: str) -> str:
    digest = sha256(f"{key_hash}:{device_id}".encode()).hexdigest()[:24]
    return f"lic_{digest}"


def _normalize_subscription_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    allowed = {"active", "trialing", "past_due", "canceled", "expired", "revoked"}
    if text not in allowed:
        raise ValueError("subscription status must be one of active, trialing, past_due, canceled, expired, revoked")
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_optional(value: datetime | str | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _iso(value)
    return _iso(_parse_datetime(value))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_label(value: Any, *, max_length: int) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _safe_activation_error_code(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return "activation_failed"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("code"):
            return str(error["code"])
        if data.get("code"):
            return str(data["code"])
    return "activation_failed"


def _safe_activation_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return "Activation failed."
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        detail = data.get("detail")
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])
        if isinstance(detail, str) and detail:
            return detail
    return "Activation failed."


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def write_activation_key_once(path: Path, activation_key: str) -> None:
    """Admin helper for writing a generated key to a private handoff file."""
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(activation_key)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite activation key handoff file: {target}")
        os.replace(temp_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        temp_path.unlink(missing_ok=True)


def list_subscription_keys(*, db_path: Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return redacted subscription/key records for the admin panel."""
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                k.*,
                COUNT(d.id) AS device_count,
                MAX(d.last_activated_at) AS last_activated_at
            FROM subscription_keys k
            LEFT JOIN activation_devices d ON d.key_hash = k.key_hash
            GROUP BY k.key_hash
            ORDER BY k.updated_at DESC, k.created_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 200), 500)),),
        ).fetchall()
        return [_subscription_admin_payload(conn, row) for row in rows]


def revoke_subscription_key(
    *,
    key_hash: str,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark a subscription key revoked and surface license-revocation handoff.

    Revoking the activation key blocks new activations. Existing desktop
    installs hold signed offline licenses, so operators must publish/deploy a
    signed revocation manifest for the listed license IDs before claiming the
    paid entitlement is disabled on those devices.
    """
    record = update_subscription_key(
        key_hash=key_hash,
        status="revoked",
        cancel_at_period_end=False,
        db_path=db_path,
        now=now,
    )
    revoked_license_ids = [
        str(device.get("license_id") or "")
        for device in record.get("devices", [])
        if isinstance(device, dict) and device.get("license_id")
    ]
    if revoked_license_ids:
        record["revoked_license_ids"] = revoked_license_ids
        record["revocation_manifest_required"] = True
        record["revocation_manifest_note"] = (
            "Existing activated devices require a signed license revocation manifest "
            "or replacement-license handoff before paid features are disabled."
        )
    else:
        record["revoked_license_ids"] = []
        record["revocation_manifest_required"] = False
    return record


def renew_subscription_key(
    *,
    key_hash: str,
    status: str = "active",
    expires_at: datetime | str | None = None,
    renews_at: datetime | str | None = None,
    cancel_at_period_end: bool = False,
    max_devices: int | None = None,
    seats: int | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update renewal/status fields for one subscription key.

    Renewal always rewrites the expiry window, so an explicit ``None`` here
    means "clear the expiry (long-term valid)" rather than "leave unchanged".
    """
    return update_subscription_key(
        key_hash=key_hash,
        status=status,
        expires_at=expires_at,
        renews_at=renews_at,
        cancel_at_period_end=cancel_at_period_end,
        max_devices=max_devices,
        seats=seats,
        db_path=db_path,
        now=now,
    )


def update_subscription_key(
    *,
    key_hash: str,
    status: str | None = None,
    expires_at: datetime | str | None | object = _UNSET,
    renews_at: datetime | str | None | object = _UNSET,
    cancel_at_period_end: bool | None = None,
    max_devices: int | None = None,
    seats: int | None = None,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Patch mutable subscription fields for the admin panel.

    ``expires_at``/``renews_at`` use the ``_UNSET`` sentinel so callers can
    explicitly clear a column (pass ``None`` → store ``NULL``, e.g. "long-term
    valid") while omitting the argument leaves the existing value untouched.
    """
    normalized_hash = _clean_key_hash(key_hash)
    updates: list[str] = []
    values: list[Any] = []
    if status is not None:
        updates.append("status = ?")
        values.append(_normalize_subscription_status(status))
    if expires_at is not _UNSET:
        updates.append("expires_at = ?")
        values.append(_iso_optional(expires_at))
    if renews_at is not _UNSET:
        updates.append("renews_at = ?")
        values.append(_iso_optional(renews_at))
    if cancel_at_period_end is not None:
        updates.append("cancel_at_period_end = ?")
        values.append(1 if cancel_at_period_end else 0)
    if max_devices is not None:
        updates.append("max_devices = ?")
        values.append(max(1, int(max_devices)))
    if seats is not None:
        updates.append("seats = ?")
        values.append(max(1, int(seats)))
    updates.append("updated_at = ?")
    values.append(_iso(now or _utc_now()))
    values.append(normalized_hash)

    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        result = conn.execute(
            f"UPDATE subscription_keys SET {', '.join(updates)} WHERE key_hash = ?",  # noqa: S608
            tuple(values),
        )
        if result.rowcount == 0:
            raise ActivationError("Subscription key was not found.", code="activation_key_not_found", status_code=404)
        row = conn.execute("SELECT * FROM subscription_keys WHERE key_hash = ?", (normalized_hash,)).fetchone()
        if row is None:
            raise ActivationError("Subscription key was not found.", code="activation_key_not_found", status_code=404)
        return _subscription_admin_payload(conn, row)


def unbind_activation_device(*, license_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """Remove one activated device so the seat can be reused."""
    normalized = _safe_label(license_id, max_length=128)
    if not normalized:
        raise ActivationError("License id is required.", code="activation_device_required", status_code=422)
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT id, key_hash, device_id FROM activation_devices WHERE id = ?",
            (normalized,),
        ).fetchone()
        if row is None:
            raise ActivationError(
                "Activation device was not found.",
                code="activation_device_not_found",
                status_code=404,
            )
        conn.execute("DELETE FROM activation_devices WHERE id = ?", (normalized,))
    return {"license_id": normalized, "removed": True}


def _subscription_admin_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    key_hash = str(row["key_hash"] or "")
    devices = conn.execute(
        """
        SELECT id, device_id, device_fingerprint, device_profile,
               first_activated_at, last_activated_at, app_version
        FROM activation_devices
        WHERE key_hash = ?
        ORDER BY last_activated_at DESC
        """,
        (key_hash,),
    ).fetchall()
    device_count = _row_value(row, "device_count")
    last_activated_at = _row_value(row, "last_activated_at")
    return {
        "key_hash": key_hash,
        "key_hash_prefix": key_hash[:12],
        "plan": normalize_plan(row["plan"]).value,
        "subscription_id": str(row["subscription_id"] or ""),
        "status": _normalize_subscription_status(row["status"]),
        "subject": str(row["subject"] or ""),
        "seats": max(1, int(row["seats"] or 1)),
        "max_devices": max(1, int(row["max_devices"] or row["seats"] or 1)),
        "expires_at": row["expires_at"],
        "renews_at": row["renews_at"],
        "cancel_at_period_end": bool(row["cancel_at_period_end"]),
        "order_ref": str(row["order_ref"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "device_count": int(device_count or len(devices)),
        "last_activated_at": last_activated_at,
        "devices": [_device_admin_payload(device) for device in devices],
    }


def _device_admin_payload(row: sqlite3.Row) -> dict[str, Any]:
    device_id = str(row["device_id"] or "")
    fingerprint = str(_row_value(row, "device_fingerprint") or "")
    profile = _device_profile_payload(_row_value(row, "device_profile"))
    return {
        "license_id": str(row["id"] or ""),
        "device_label": _redact_identifier(device_id),
        "device_fingerprint_label": _redact_identifier(fingerprint) if fingerprint else "",
        "device_profile": profile,
        "risk_label": "fingerprint_bound" if fingerprint else "legacy_device_id_only",
        "first_activated_at": row["first_activated_at"],
        "last_activated_at": row["last_activated_at"],
        "app_version": str(row["app_version"] or ""),
    }


def _device_profile_payload(value: Any) -> dict[str, Any]:
    try:
        profile = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(profile, dict):
        return {}
    return _safe_device_profile(profile)


def _redact_identifier(value: str) -> str:
    """Mask an identifier so it is never surfaced verbatim in the admin panel.

    Short ids used to be returned unchanged; now every value keeps only a short
    recognizable head (and tail when long enough) with the middle redacted.
    """
    text = str(value or "").strip()
    length = len(text)
    if length == 0:
        return ""
    if length <= 3:
        return "*" * length
    head_len = min(8, max(2, length - 4))
    head = text[:head_len]
    tail = text[-2:] if length - head_len >= 4 else ""
    return f"{head}...{tail}" if tail else f"{head}..."


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def _clean_key_hash(value: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ActivationError("Subscription key id is invalid.", code="activation_key_invalid", status_code=422)
    return text
