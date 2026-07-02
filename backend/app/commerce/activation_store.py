"""SQLite persistence and admin views for subscription activation."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.commerce.activation_policy import (
    ActivationError,
    ActivationPolicy,
    hash_activation_key,
)
from app.commerce.activation_policy import (
    activation_message_for_code as _activation_message_for_code,
)
from app.commerce.activation_policy import (
    decode_device_profile as _device_profile_payload,
)
from app.commerce.activation_policy import (
    safe_label as _safe_label,
)
from app.commerce.entitlements import normalize_plan

ACTIVATION_STORE_UNSET = object()
"""Sentinel distinguishing omitted fields from explicit NULL updates."""

_DELETABLE_SUBSCRIPTION_STATES = {"canceled", "expired", "revoked"}


class ActivationStore:
    """Own activation-server SQLite schema and subscription admin lifecycle."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    def initialize_database(self) -> Path:
        """Create the activation-server SQLite tables if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
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
        return self.path

    def upsert_subscription_key(
        self,
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
        pepper: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Create/update one activation key without storing the raw key."""
        normalized_plan = normalize_plan(plan)
        normalized_status = normalize_subscription_status(status)
        key_hash = hash_activation_key(activation_key, pepper=pepper)
        timestamp = activation_iso(now or datetime.now(UTC))
        seats_value = max(1, int(seats or 1))
        max_devices_value = max(1, int(max_devices or seats_value))
        self.initialize_database()
        with sqlite3.connect(self.path) as conn:
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
                    activation_iso_optional(expires_at),
                    activation_iso_optional(renews_at),
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
            "expires_at": activation_iso_optional(expires_at),
            "renews_at": activation_iso_optional(renews_at),
            "cancel_at_period_end": bool(cancel_at_period_end),
        }

    def list_subscription_keys(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return redacted subscription/key records for the admin panel."""
        self.initialize_database()
        with sqlite3.connect(self.path) as conn:
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
            return [self._subscription_admin_payload(conn, row) for row in rows]

    def revoke_subscription_key(
        self,
        *,
        key_hash: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Mark a subscription key revoked and surface license-revocation handoff."""
        record = self.update_subscription_key(
            key_hash=key_hash,
            status="revoked",
            cancel_at_period_end=False,
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
                "已有激活设备需要发布签名吊销清单或交接替换许可证后，付费能力才会被停用。"
            )
        else:
            record["revoked_license_ids"] = []
            record["revocation_manifest_required"] = False
        return record

    def delete_subscription_key(self, *, key_hash: str) -> dict[str, Any]:
        """Delete a terminal, device-free subscription record from the admin list."""
        normalized_hash = clean_key_hash(key_hash)
        self.initialize_database()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM subscription_keys WHERE key_hash = ?",
                (normalized_hash,),
            ).fetchone()
            if row is None:
                raise ActivationError(
                    _activation_message_for_code("activation_key_not_found"),
                    code="activation_key_not_found",
                    status_code=404,
                )
            status = normalize_subscription_status(row["status"])
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
        self,
        *,
        key_hash: str,
        status: str = "active",
        expires_at: datetime | str | None = None,
        renews_at: datetime | str | None = None,
        cancel_at_period_end: bool = False,
        max_devices: int | None = None,
        seats: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Update renewal/status fields for one subscription key."""
        return self.update_subscription_key(
            key_hash=key_hash,
            status=status,
            expires_at=expires_at,
            renews_at=renews_at,
            cancel_at_period_end=cancel_at_period_end,
            max_devices=max_devices,
            seats=seats,
            now=now,
        )

    def update_subscription_key(
        self,
        *,
        key_hash: str,
        status: str | None = None,
        expires_at: datetime | str | None | object = ACTIVATION_STORE_UNSET,
        renews_at: datetime | str | None | object = ACTIVATION_STORE_UNSET,
        cancel_at_period_end: bool | None = None,
        max_devices: int | None = None,
        seats: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Patch mutable subscription fields for the admin panel."""
        normalized_hash = clean_key_hash(key_hash)
        updates: list[str] = []
        values: list[Any] = []
        if status is not None:
            updates.append("status = ?")
            values.append(normalize_subscription_status(status))
        if expires_at is not ACTIVATION_STORE_UNSET:
            updates.append("expires_at = ?")
            values.append(activation_iso_optional(expires_at))
        if renews_at is not ACTIVATION_STORE_UNSET:
            updates.append("renews_at = ?")
            values.append(activation_iso_optional(renews_at))
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
        values.append(activation_iso(now or datetime.now(UTC)))
        values.append(normalized_hash)

        self.initialize_database()
        with sqlite3.connect(self.path) as conn:
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
            row = conn.execute(
                "SELECT * FROM subscription_keys WHERE key_hash = ?",
                (normalized_hash,),
            ).fetchone()
            if row is None:
                raise ActivationError(
                    _activation_message_for_code("activation_key_not_found"),
                    code="activation_key_not_found",
                    status_code=404,
                )
            return self._subscription_admin_payload(conn, row)

    def unbind_activation_device(self, *, license_id: str) -> dict[str, Any]:
        """Remove one activated device so the seat can be reused."""
        normalized = _safe_label(license_id, max_length=128)
        if not normalized:
            raise ActivationError(
                _activation_message_for_code("license_id_required"),
                code="activation_device_required",
                status_code=422,
            )
        self.initialize_database()
        with sqlite3.connect(self.path) as conn:
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

    def _subscription_admin_payload(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
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
        device_count = row_value(row, "device_count")
        last_activated_at = row_value(row, "last_activated_at")
        return {
            "key_hash": key_hash,
            "key_hash_prefix": key_hash[:12],
            "plan": normalize_plan(row["plan"]).value,
            "subscription_id": str(row["subscription_id"] or ""),
            "status": normalize_subscription_status(row["status"]),
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


def _device_admin_payload(row: sqlite3.Row) -> dict[str, Any]:
    device_id = str(row["device_id"] or "")
    server_device_ref = str(row_value(row, "server_device_ref") or "")
    fingerprint = str(row_value(row, "device_fingerprint") or "")
    profile = _device_profile_payload(row_value(row, "device_profile"))
    return {
        "license_id": str(row["id"] or ""),
        "device_label": redact_identifier(device_id),
        "server_device_ref_label": redact_identifier(server_device_ref) if server_device_ref else "",
        "device_fingerprint_label": redact_identifier(fingerprint) if fingerprint else "",
        "device_profile": profile,
        "risk_label": "server_fingerprint_bound" if server_device_ref and fingerprint else "legacy_device_id_only",
        "first_activated_at": row["first_activated_at"],
        "last_activated_at": row["last_activated_at"],
        "app_version": str(row["app_version"] or ""),
    }


def redact_identifier(value: str) -> str:
    """Mask an identifier so it is never surfaced verbatim in the admin panel."""
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


def row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def clean_key_hash(value: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ActivationError(
            _activation_message_for_code("activation_key_invalid"),
            code="activation_key_invalid",
            status_code=422,
        )
    return text


def normalize_subscription_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    allowed = {"active", "trialing", "past_due", "canceled", "expired", "revoked"}
    if text not in allowed:
        raise ValueError("订阅状态必须是 active、trialing、past_due、canceled、expired 或 revoked。")
    return text


def parse_activation_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def activation_iso_optional(value: datetime | str | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return activation_iso(value)
    parsed = parse_activation_datetime(value)
    return activation_iso(parsed) if parsed else None


def activation_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
