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
import logging
import os
import secrets
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.commerce.activation_policy import (
    ACTIVATION_KEY_PEPPER_ENV_VAR,
    ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR,
    MAX_APP_VERSION_CHARS,
    ActivationError,
    ActivationPolicy,
    ActivationRefreshRequest,
    ActivationRequest,
    hash_activation_key,
)
from app.commerce.activation_policy import (
    ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR as ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR,
)
from app.commerce.activation_policy import (
    activation_error_code as _safe_activation_error_code,
)
from app.commerce.activation_policy import (
    activation_error_message as _safe_activation_error_message,
)
from app.commerce.activation_policy import (
    activation_message_for_code as _activation_message_for_code,
)
from app.commerce.activation_policy import (
    clean_activation_key as _clean_activation_key,
)
from app.commerce.activation_policy import (
    decode_device_profile as _device_profile_payload,
)
from app.commerce.activation_policy import (
    device_binding_claim as _activation_device_binding_claim,
)
from app.commerce.activation_policy import (
    safe_label as _safe_label,
)
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
    load_revocation_manifest,
    parse_license,
    require_activation_response_nonce,
    sign_license,
    verify_license,
)
from app.core import audit as audit_core

logger = logging.getLogger(__name__)

ACTIVATION_BASE_URL_ENV_VAR = "LENGRVIS_ACTIVATION_BASE_URL"
ACTIVATION_TIMEOUT_SECONDS_ENV_VAR = "LENGRVIS_ACTIVATION_TIMEOUT_SECONDS"
ACTIVATION_ALLOW_INSECURE_HTTP_ENV_VAR = "LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP"

ACTIVATION_DB_ENV_VAR = "LENGRVIS_ACTIVATION_DB"
ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY"
ACTIVATION_SIGNING_PRIVATE_KEY_FILE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY_FILE"
ACTIVATION_SIGNING_PASSPHRASE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE"  # noqa: S105
ACTIVATION_SIGNING_PASSPHRASE_FILE_ENV_VAR = "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE_FILE"  # noqa: S105
ACTIVATION_ISSUER_ENV_VAR = "LENGRVIS_ACTIVATION_ISSUER"
ACTIVATION_RATE_LIMIT_MAX_ENV_VAR = "LENGRVIS_ACTIVATION_RATE_LIMIT_MAX"
ACTIVATION_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR = "LENGRVIS_ACTIVATION_RATE_LIMIT_WINDOW_SECONDS"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_SUBSCRIPTION_STATES = {"active", "trialing"}
_DELETABLE_SUBSCRIPTION_STATES = {"canceled", "expired", "revoked"}
_DEFAULT_TIMEOUT_SECONDS = 12.0
_DEFAULT_RATE_LIMIT_MAX = 8
_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 300
_UNSET = object()
"""Sentinel distinguishing "field omitted" from an explicit ``None`` (clear)."""


class HttpClient(Protocol):
    def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response: ...


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
    nonce_sha256 = sha256(nonce.encode("utf-8")).hexdigest()
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
            _activation_message_for_code("activation_service_unavailable"),
            code="activation_service_unavailable",
            status_code=503,
        ) from exc
    finally:
        if close_client and hasattr(http_client, "close"):
            http_client.close()
    token = str(body.get("license_token") or "").strip() if isinstance(body, dict) else ""
    if not token:
        raise ActivationError(
            _activation_message_for_code("activation_malformed_response"),
            code="activation_malformed_response",
            status_code=502,
        )
    try:
        return install_license(
            token,
            settings,
            now=now,
            expected_activation_nonce_sha256=nonce_sha256,
        )
    except LicenseError as exc:
        raise ActivationError(
            "激活服务返回的许可证未通过验签。",
            code=exc.code,
            status_code=exc.status_code,
        ) from exc


def refresh_license_with_server(
    license_token: str,
    settings: Any,
    *,
    app_version: str = "",
    client: HttpClient | None = None,
    now: datetime | None = None,
    persist: bool = True,
) -> License:
    """Refresh a subscription license online and return a newly signed token.

    Subscription licenses must periodically prove that the activation server
    still considers the subscription active. The server response is a full
    Ed25519-signed replacement license; unsigned status JSON is never enough to
    extend local paid entitlements.
    """
    token = str(license_token or "").strip()
    if not token:
        raise ActivationError(
            _activation_message_for_code("license_token_required"), code="license_token_required", status_code=422
        )
    base_url = _activation_base_url()
    identity = _collect_local_identity(settings)
    nonce = secrets.token_urlsafe(24)
    payload = {
        "license_token": token,
        "device_id": identity.device_id,
        "device_fingerprint": identity.fingerprint,
        "device_profile": identity.profile,
        "app_version": str(app_version or "")[:MAX_APP_VERSION_CHARS],
        "nonce": nonce,
    }
    endpoint = _license_refresh_endpoint(base_url)
    nonce_sha256 = sha256(nonce.encode("utf-8")).hexdigest()
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
            _activation_message_for_code("activation_service_unavailable"),
            code="activation_service_unavailable",
            status_code=503,
        ) from exc
    finally:
        if close_client and hasattr(http_client, "close"):
            http_client.close()
    refreshed_token = str(body.get("license_token") or "").strip() if isinstance(body, dict) else ""
    if not refreshed_token:
        raise ActivationError(
            _activation_message_for_code("activation_malformed_response"),
            code="activation_malformed_response",
            status_code=502,
        )
    if persist:
        try:
            return install_license(
                refreshed_token,
                settings,
                now=now,
                expected_activation_nonce_sha256=nonce_sha256,
            )
        except LicenseError as exc:
            raise ActivationError(
                "激活服务返回的许可证未通过验签。",
                code=exc.code,
                status_code=exc.status_code,
            ) from exc
    public_key = _activation_public_key_for_self_check()
    if not public_key:
        raise ActivationError(
            _activation_message_for_code("license_public_key_missing"),
            code="license_public_key_missing",
            status_code=503,
        )
    try:
        revocations, _ = load_revocation_manifest(settings, public_key=public_key)
        license_ = verify_license(
            refreshed_token,
            public_key,
            now=now,
            revocations=revocations,
            expected_device_id=identity.device_id,
            expected_device_fingerprint=identity.fingerprint,
        )
        require_activation_response_nonce(license_, nonce_sha256)
        return license_
    except LicenseError as exc:
        raise ActivationError(
            "激活服务返回的许可证未通过验签。",
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
            _activation_message_for_code("activation_device_identity_unavailable"),
            code="activation_device_identity_unavailable",
            status_code=503,
        ) from exc


def initialize_activation_db(path: Path | None = None) -> Path:
    """Create the activation-server SQLite tables if needed."""
    db_path = _activation_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
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
                server_device_ref TEXT NOT NULL DEFAULT '',
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
        _backfill_activation_server_device_refs(conn)
        _ensure_activation_rate_limit_table(conn)
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
    prepared = ActivationPolicy.from_environment().prepare_activation(request)
    device_id = prepared.device.device_id
    device_fingerprint = prepared.device.fingerprint
    device_profile = prepared.device.profile_json
    app_version = prepared.app_version
    nonce = prepared.nonce
    key_hash = prepared.key_hash
    server_device_ref = prepared.server_device_ref
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
            raise ActivationError(
                _activation_message_for_code("activation_key_not_found"),
                code="activation_key_not_found",
                status_code=404,
            )
        _ensure_subscription_can_activate(record, now=moment)
        device_row = conn.execute(
            """
            SELECT id, device_id, server_device_ref, device_fingerprint
            FROM activation_devices
            WHERE key_hash = ? AND server_device_ref = ?
            """,
            (key_hash, server_device_ref),
        ).fetchone()
        legacy_device_row = None
        if device_row is None:
            legacy_device_row = conn.execute(
                """
                SELECT id, device_id, server_device_ref, device_fingerprint
                FROM activation_devices
                WHERE key_hash = ? AND device_id = ?
                """,
                (key_hash, device_id),
            ).fetchone()
            device_row = legacy_device_row
        reused_device = device_row is not None
        if reused_device:
            license_id = str(device_row["id"])
            stored_server_device_ref = str(_row_value(device_row, "server_device_ref") or "")
            stored_fingerprint = str(device_row["device_fingerprint"] or "")
            prepared.ensure_existing_device_matches(
                fingerprint=stored_fingerprint,
                server_device_ref=stored_server_device_ref,
            )
            conn.execute(
                """
                UPDATE activation_devices
                SET device_id = ?,
                    server_device_ref = ?,
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
                    server_device_ref,
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
            # but the seat key is server-derived from key_hash + fingerprint,
            # so swapping a client device_id does not consume another seat.
            prepared.require_new_device_fingerprint()
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM activation_devices WHERE key_hash = ?",
                    (key_hash,),
                ).fetchone()[0]
            )
            if count >= record.device_limit:
                raise ActivationError(
                    _activation_message_for_code("activation_device_limit"),
                    code="activation_device_limit",
                    status_code=409,
                )
            license_id = prepared.license_id
            conn.execute(
                """
                INSERT INTO activation_devices (
                    id, key_hash, device_id, server_device_ref, device_fingerprint, device_profile,
                    first_activated_at, last_activated_at, app_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    license_id,
                    key_hash,
                    device_id,
                    server_device_ref,
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
        device_profile=device_profile,
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


def refresh_subscription_license(
    request: ActivationRefreshRequest,
    *,
    db_path: Path | None = None,
    private_key: str | None = None,
    private_key_password: bytes | None = None,
    issuer: str | None = None,
    now: datetime | None = None,
) -> ActivationResult:
    """Server-side subscription status refresh for an existing device license."""
    moment = now or _utc_now()
    public_key = _activation_public_key_for_self_check()
    if not public_key:
        raise ActivationError(
            _activation_message_for_code("activation_server_unconfigured"),
            code="activation_server_unconfigured",
            status_code=503,
        )
    try:
        current_license = parse_license(request.license_token, public_key)
    except LicenseError as exc:
        raise ActivationError("许可证令牌未通过验签。", code=exc.code, status_code=402) from exc
    if not current_license.license_id:
        raise ActivationError(
            _activation_message_for_code("license_id_required"), code="license_id_required", status_code=422
        )
    if not current_license.subscription_id:
        raise ActivationError(
            _activation_message_for_code("subscription_required"), code="subscription_required", status_code=422
        )

    prepared = ActivationPolicy.from_environment().prepare_refresh(
        request,
        expected_device_id=current_license.device_id,
    )
    device_id = prepared.device.device_id
    device_profile = prepared.device.profile_json
    app_version = prepared.app_version
    nonce = prepared.nonce

    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        row = conn.execute(
            """
            SELECT id, key_hash, device_id, server_device_ref, device_fingerprint
            FROM activation_devices
            WHERE id = ?
            """,
            (current_license.license_id,),
        ).fetchone()
        if row is None:
            raise ActivationError(
                _activation_message_for_code("activation_device_not_found"),
                code="activation_device_not_found",
                status_code=402,
            )
        stored_device_id = str(row["device_id"] or "")
        if not hmac.compare_digest(stored_device_id, device_id):
            raise ActivationError(
                _activation_message_for_code("activation_device_mismatch"),
                code="activation_device_mismatch",
                status_code=402,
            )
        stored_fingerprint = str(row["device_fingerprint"] or "")
        stored_server_device_ref = str(_row_value(row, "server_device_ref") or "")
        resolved_binding = prepared.resolve_binding(
            key_hash=str(row["key_hash"] or ""),
            stored_fingerprint=stored_fingerprint,
            stored_server_device_ref=stored_server_device_ref,
            license_fingerprint=current_license.device_fingerprint,
        )
        next_fingerprint = resolved_binding.fingerprint
        next_server_device_ref = resolved_binding.server_device_ref
        record = _load_subscription_record(conn, str(row["key_hash"] or ""))
        if record is None:
            raise ActivationError(
                _activation_message_for_code("activation_key_not_found"),
                code="activation_key_not_found",
                status_code=402,
            )
        if not hmac.compare_digest(record.subscription_id, current_license.subscription_id):
            raise ActivationError(
                _activation_message_for_code("subscription_mismatch"),
                code="subscription_mismatch",
                status_code=402,
            )
        _ensure_subscription_can_activate(record, now=moment)
        conn.execute(
            """
            UPDATE activation_devices
            SET server_device_ref = CASE
                    WHEN ? != '' THEN ?
                    ELSE server_device_ref
                END,
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
                next_server_device_ref,
                next_server_device_ref,
                next_fingerprint,
                next_fingerprint,
                device_profile,
                device_profile,
                _iso(moment),
                app_version,
                current_license.license_id,
            ),
        )

    refreshed_token = _sign_activation_license(
        record,
        license_id=current_license.license_id,
        device_id=device_id,
        device_fingerprint=next_fingerprint,
        device_profile=device_profile,
        app_version=app_version,
        nonce=nonce,
        private_key=private_key,
        private_key_password=private_key_password,
        issuer=issuer,
        now=moment,
    )
    refreshed_license = verify_license(refreshed_token, public_key, now=moment)
    return ActivationResult(
        license_token=refreshed_token,
        license=refreshed_license,
        license_id=current_license.license_id,
        plan=record.plan,
        subscription_id=record.subscription_id,
        expires_at=record.expires_at,
        renews_at=record.renews_at,
        reused_device=True,
    )


def enforce_activation_rate_limit(
    scope: str,
    *,
    now: float | None = None,
    db_path: Path | None = None,
    maximum: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """Cross-process activation rate limit backed by the activation SQLite DB."""
    current = time.time() if now is None else now
    window = (
        window_seconds
        if window_seconds is not None
        else _env_int(ACTIVATION_RATE_LIMIT_WINDOW_SECONDS_ENV_VAR, _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
    )
    max_val = maximum if maximum is not None else _env_int(ACTIVATION_RATE_LIMIT_MAX_ENV_VAR, _DEFAULT_RATE_LIMIT_MAX)
    if max_val <= 0:
        return
    key = (scope or "unknown").strip() or "unknown"
    cutoff = current - max(1, window)
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_activation_rate_limit_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM activation_rate_limits WHERE attempted_at <= ?", (cutoff,))
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM activation_rate_limits
            WHERE scope = ? AND attempted_at > ?
            """,
            (key, cutoff),
        ).fetchone()[0]
        if int(count) >= max_val:
            conn.rollback()
            raise ActivationError(
                _activation_message_for_code("activation_rate_limited"),
                code="activation_rate_limited",
                status_code=429,
            )
        conn.execute(
            "INSERT INTO activation_rate_limits(scope, attempted_at) VALUES (?, ?)",
            (key, current),
        )
        conn.commit()


def record_activation_audit(
    event_type: str,
    *,
    result: ActivationResult | None = None,
    code: str = "",
    client_ref: str = "",
) -> None:
    """Record activation-server audit metadata without keys, tokens, or raw device ids."""
    payload: dict[str, Any] = {"client_ref": _safe_label(client_ref, max_length=64)}
    if result is not None:
        payload.update(
            {
                "license_id": result.license_id,
                "plan": result.plan.value,
                "subscription_id": result.subscription_id,
                "reused_device": result.reused_device,
            }
        )
    if code:
        payload["code"] = _safe_label(code, max_length=64)
    try:
        audit_core.record(event_type, "activation_server", payload)
    except Exception as exc:  # noqa: BLE001 - audit must not change activation outcomes.
        logger.warning("Activation audit write failed; continuing: %s", type(exc).__name__)


def activation_server_configured() -> bool:
    return bool(
        str(os.getenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "")).strip()
        and str(os.getenv(ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, "")).strip()
        and _read_activation_private_key(required=False)
    )


def _ensure_activation_device_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(activation_devices)").fetchall()}
    if "server_device_ref" not in columns:
        conn.execute("ALTER TABLE activation_devices ADD COLUMN server_device_ref TEXT NOT NULL DEFAULT ''")
    if "device_fingerprint" not in columns:
        conn.execute("ALTER TABLE activation_devices ADD COLUMN device_fingerprint TEXT NOT NULL DEFAULT ''")
    if "device_profile" not in columns:
        conn.execute("ALTER TABLE activation_devices ADD COLUMN device_profile TEXT NOT NULL DEFAULT '{}'")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_activation_devices_key_server_ref
        ON activation_devices(key_hash, server_device_ref)
        WHERE server_device_ref != ''
        """
    )


def _backfill_activation_server_device_refs(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, key_hash, device_id, device_fingerprint, server_device_ref
        FROM activation_devices
        WHERE server_device_ref = ''
        """
    ).fetchall()
    policy = ActivationPolicy.from_environment()
    for row in rows:
        key_hash = str(row["key_hash"] or "")
        fingerprint = str(row["device_fingerprint"] or "")
        device_id = str(row["device_id"] or "")
        try:
            server_ref = policy.server_device_ref(
                key_hash=key_hash,
                device_fingerprint=fingerprint,
                legacy_device_id=device_id,
            )
        except ActivationError:
            continue
        conn.execute(
            "UPDATE activation_devices SET server_device_ref = ? WHERE id = ?",
            (server_ref, str(row["id"] or "")),
        )


def _ensure_activation_rate_limit_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activation_rate_limits (
            scope TEXT NOT NULL,
            attempted_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_activation_rate_limits_scope_time
        ON activation_rate_limits(scope, attempted_at)
        """
    )


def _sign_activation_license(
    record: SubscriptionRecord,
    *,
    license_id: str,
    device_id: str,
    device_fingerprint: str,
    device_profile: str,
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
            "device_binding": _activation_device_binding_claim(
                device_profile,
                device_fingerprint=device_fingerprint,
            ),
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
            _activation_message_for_code(f"subscription_{record.status}"),
            code=f"subscription_{record.status}",
            status_code=402,
        )
    if record.expires_at is not None and now >= record.expires_at:
        raise ActivationError(
            _activation_message_for_code("subscription_expired"), code="subscription_expired", status_code=402
        )


def _activation_base_url() -> str:
    raw = str(os.getenv(ACTIVATION_BASE_URL_ENV_VAR, "")).strip()
    if not raw:
        raise ActivationError(
            _activation_message_for_code("activation_unconfigured"), code="activation_unconfigured", status_code=503
        )
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ActivationError(
            _activation_message_for_code("activation_url_invalid"), code="activation_url_invalid", status_code=503
        )
    host = (parsed.hostname or "").lower()
    insecure_allowed = str(os.getenv(ACTIVATION_ALLOW_INSECURE_HTTP_ENV_VAR, "")).strip().lower() in _TRUE_VALUES
    if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost", "::1"} and not insecure_allowed:
        raise ActivationError(
            _activation_message_for_code("activation_https_required"), code="activation_https_required", status_code=503
        )
    return raw.rstrip("/")


def _activation_endpoint(base_url: str) -> str:
    return f"{base_url}/api/v1/activations"


def _license_refresh_endpoint(base_url: str) -> str:
    return f"{base_url}/api/v1/licenses/refresh"


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
            _activation_message_for_code("activation_server_unconfigured"),
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
                _activation_message_for_code("activation_server_unconfigured"),
                code="activation_server_unconfigured",
                status_code=503,
            ) from exc
    if required:
        raise ActivationError(
            _activation_message_for_code("activation_server_unconfigured"),
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
            _activation_message_for_code("activation_server_unconfigured"),
            code="activation_server_unconfigured",
            status_code=503,
        ) from exc
    return text.encode("utf-8") if text else None


def _activation_public_key_for_self_check() -> str:
    return str(os.getenv("LENGRVIS_LICENSE_PUBLIC_KEY", "")).strip()


def _normalize_subscription_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    allowed = {"active", "trialing", "past_due", "canceled", "expired", "revoked"}
    if text not in allowed:
        raise ValueError("订阅状态必须是 active、trialing、past_due、canceled、expired 或 revoked。")
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
            raise FileExistsError(f"拒绝覆盖已有授权码交接文件：{target}")
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
        record["revocation_manifest_note"] = "已有激活设备需要发布签名吊销清单或交接替换许可证后，付费能力才会被停用。"
    else:
        record["revoked_license_ids"] = []
        record["revocation_manifest_required"] = False
    return record


def delete_subscription_key(
    *,
    key_hash: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Delete a terminal, device-free subscription record from the admin list."""
    normalized_hash = _clean_key_hash(key_hash)
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM subscription_keys WHERE key_hash = ?", (normalized_hash,)).fetchone()
        if row is None:
            raise ActivationError(
                _activation_message_for_code("activation_key_not_found"),
                code="activation_key_not_found",
                status_code=404,
            )
        status = _normalize_subscription_status(row["status"])
        if status not in _DELETABLE_SUBSCRIPTION_STATES:
            raise ActivationError(
                _activation_message_for_code("subscription_delete_not_terminal"),
                code="subscription_delete_not_terminal",
                status_code=409,
            )
        device_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM activation_devices WHERE key_hash = ?",
                (normalized_hash,),
            ).fetchone()[0]
            or 0
        )
        if device_count > 0:
            raise ActivationError(
                _activation_message_for_code("subscription_delete_has_devices"),
                code="subscription_delete_has_devices",
                status_code=409,
            )
        conn.execute("DELETE FROM subscription_keys WHERE key_hash = ?", (normalized_hash,))
    return {
        "removed": True,
        "key_hash": normalized_hash,
        "key_hash_prefix": normalized_hash[:12],
    }


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
            raise ActivationError(
                _activation_message_for_code("activation_key_not_found"),
                code="activation_key_not_found",
                status_code=404,
            )
        row = conn.execute("SELECT * FROM subscription_keys WHERE key_hash = ?", (normalized_hash,)).fetchone()
        if row is None:
            raise ActivationError(
                _activation_message_for_code("activation_key_not_found"),
                code="activation_key_not_found",
                status_code=404,
            )
        return _subscription_admin_payload(conn, row)


def unbind_activation_device(*, license_id: str, db_path: Path | None = None) -> dict[str, Any]:
    """Remove one activated device so the seat can be reused."""
    normalized = _safe_label(license_id, max_length=128)
    if not normalized:
        raise ActivationError(
            _activation_message_for_code("license_id_required"), code="activation_device_required", status_code=422
        )
    path = initialize_activation_db(db_path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT id, key_hash, device_id FROM activation_devices WHERE id = ?",
            (normalized,),
        ).fetchone()
        if row is None:
            raise ActivationError(
                _activation_message_for_code("activation_device_not_found"),
                code="activation_device_not_found",
                status_code=404,
            )
        conn.execute("DELETE FROM activation_devices WHERE id = ?", (normalized,))
    return {"license_id": normalized, "removed": True}


def _subscription_admin_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    key_hash = str(row["key_hash"] or "")
    devices = conn.execute(
        """
        SELECT id, device_id, server_device_ref, device_fingerprint, device_profile,
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
    server_device_ref = str(_row_value(row, "server_device_ref") or "")
    fingerprint = str(_row_value(row, "device_fingerprint") or "")
    profile = _device_profile_payload(_row_value(row, "device_profile"))
    return {
        "license_id": str(row["id"] or ""),
        "device_label": _redact_identifier(device_id),
        "server_device_ref_label": _redact_identifier(server_device_ref) if server_device_ref else "",
        "device_fingerprint_label": _redact_identifier(fingerprint) if fingerprint else "",
        "device_profile": profile,
        "risk_label": "server_fingerprint_bound" if server_device_ref and fingerprint else "legacy_device_id_only",
        "first_activated_at": row["first_activated_at"],
        "last_activated_at": row["last_activated_at"],
        "app_version": str(row["app_version"] or ""),
    }


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
        raise ActivationError(
            _activation_message_for_code("activation_key_invalid"), code="activation_key_invalid", status_code=422
        )
    return text
