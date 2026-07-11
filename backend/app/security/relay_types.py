"""Public ciphertext relay and TLS pin lifecycle contracts.

These models deliberately exclude task plaintext and private key material. The
cloud relay may route an envelope, but only paired endpoint devices can decrypt
its payload.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_RELAY_QUEUE_SECONDS = 24 * 60 * 60
MAX_RELAY_CIPHERTEXT_CHARS = 2 * 1024 * 1024
MAX_USABLE_TLS_PINS_PER_ORIGIN = 2
MAX_ACTIVE_TLS_PIN_SECONDS = 31 * 24 * 60 * 60
MAX_NEXT_TLS_PIN_SECONDS = 24 * 60 * 60
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5 * 60

_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RelayContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


RelayPayloadType = Literal[
    "template_start",
    "task_status_request",
    "task_status_response",
    "task_follow_up",
    "task_pause",
    "task_cancel",
    "ack",
]


class RelayEnvelope(RelayContract):
    """Opaque, replay-bounded message accepted by the Preview relay surface."""

    schema_version: Literal["relay-envelope-v1"] = "relay-envelope-v1"
    envelope_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    account_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sender_device_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    recipient_device_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    payload_type: RelayPayloadType
    key_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    sequence: int = Field(ge=0)
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    reply_to: str = Field(default="", max_length=128, pattern=r"^$|^[A-Za-z0-9_-]+$")
    nonce: str = Field(min_length=16, max_length=128)
    ciphertext: str = Field(min_length=16, max_length=MAX_RELAY_CIPHERTEXT_CHARS)
    ciphertext_sha256: str
    aad_sha256: str
    created_at: str
    expires_at: str

    @field_validator("nonce", "ciphertext")
    @classmethod
    def validate_base64url(cls, value: str) -> str:
        if not _BASE64URL_RE.fullmatch(value):
            raise ValueError("relay binary fields must use base64url encoding")
        try:
            _decode_base64url(value)
        except ValueError as exc:
            raise ValueError("relay binary fields must use valid base64url encoding") from exc
        return value.rstrip("=")

    @field_validator("ciphertext_sha256", "aad_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("relay digests must be 64 hexadecimal SHA-256 characters")
        return normalized

    @model_validator(mode="after")
    def validate_envelope(self) -> RelayEnvelope:
        created = _parse_utc(self.created_at)
        expires = _parse_utc(self.expires_at)
        _validate_not_far_future(created, label="relay envelope creation")
        lifetime = (expires - created).total_seconds()
        if lifetime <= 0:
            raise ValueError("relay envelope expiry must follow creation")
        if lifetime > MAX_RELAY_QUEUE_SECONDS:
            raise ValueError("relay envelope cannot remain queued for more than 24 hours")
        if self.sender_device_id == self.recipient_device_id:
            raise ValueError("relay envelope sender and recipient must be different devices")
        actual_digest = hashlib.sha256(_decode_base64url(self.ciphertext)).hexdigest()
        if actual_digest != self.ciphertext_sha256:
            raise ValueError("relay ciphertext digest does not match")
        return self

    def is_expired(self, *, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return _parse_utc(self.expires_at) <= now.astimezone(UTC)


class TlsPinRecord(RelayContract):
    """One certificate fingerprint bound to one exact HTTPS origin."""

    schema_version: Literal["tls-pin-record-v1"] = "tls-pin-record-v1"
    pin_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    origin: str = Field(min_length=9, max_length=512)
    host: str = Field(min_length=1, max_length=253)
    fingerprint_sha256: str
    status: Literal["active", "next", "revoked"] = "active"
    created_at: str
    expires_at: str
    source_device_id: str = Field(default="", max_length=128, pattern=r"^$|^[A-Za-z0-9_-]+$")
    revoked_at: str = ""

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return _normalize_host(value)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return _normalize_https_origin(value)

    @field_validator("fingerprint_sha256")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        normalized = value.replace(":", "").strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("TLS pin fingerprint must be a SHA-256 certificate digest")
        return normalized

    @model_validator(mode="after")
    def validate_lifecycle(self) -> TlsPinRecord:
        parsed_origin = urlsplit(self.origin)
        if parsed_origin.hostname != self.host:
            raise ValueError("TLS pin origin and host must match")
        created = _parse_utc(self.created_at)
        expires = _parse_utc(self.expires_at)
        _validate_not_far_future(created, label="TLS pin creation")
        lifetime = (expires - created).total_seconds()
        if lifetime <= 0:
            raise ValueError("TLS pin expiry must follow creation")
        maximum_lifetime = MAX_NEXT_TLS_PIN_SECONDS if self.status == "next" else MAX_ACTIVE_TLS_PIN_SECONDS
        if lifetime > maximum_lifetime:
            raise ValueError("TLS pin lifetime exceeds its status limit")
        if self.status == "revoked":
            if not self.revoked_at:
                raise ValueError("revoked TLS pins require revoked_at")
            if _parse_utc(self.revoked_at) < created:
                raise ValueError("TLS pin revocation cannot precede creation")
        elif self.revoked_at:
            raise ValueError("usable TLS pins cannot carry revoked_at")
        return self

    def is_usable(self, *, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        normalized_now = now.astimezone(UTC)
        created = _parse_utc(self.created_at)
        return (
            self.status in {"active", "next"}
            and created <= normalized_now + timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_SECONDS)
            and _parse_utc(self.expires_at) > normalized_now
        )


def validate_tls_pin_set(
    records: list[TlsPinRecord],
    *,
    origin: str,
    at: datetime | None = None,
) -> list[TlsPinRecord]:
    """Return the bounded active/next set or fail closed on ambiguous trust."""

    normalized_origin = _normalize_https_origin(origin)
    usable = [record for record in records if record.origin == normalized_origin and record.is_usable(at=at)]
    if not any(record.status == "active" for record in usable):
        raise ValueError("TLS pin set has no active pin")
    if len(usable) > MAX_USABLE_TLS_PINS_PER_ORIGIN:
        raise ValueError("TLS pin set contains an unexpected number of usable pins")
    if len({record.pin_id for record in usable}) != len(usable):
        raise ValueError("TLS pin set contains duplicate pin identifiers")
    if len({record.fingerprint_sha256 for record in usable}) != len(usable):
        raise ValueError("TLS pin set contains duplicate certificate fingerprints")
    return sorted(usable, key=lambda record: (record.status != "active", record.created_at, record.pin_id))


def _decode_base64url(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid base64url") from exc


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def _validate_not_far_future(value: datetime, *, label: str) -> None:
    latest = datetime.now(UTC) + timedelta(seconds=MAX_FUTURE_CLOCK_SKEW_SECONDS)
    if value > latest:
        raise ValueError(f"{label} cannot be more than 5 minutes in the future")


def _normalize_host(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    if not candidate or any(character in candidate for character in "/@[]"):
        raise ValueError("TLS pin host must be an exact hostname")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        if ":" in candidate:
            raise ValueError("TLS pin host is invalid") from None
    try:
        normalized = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("TLS pin host is invalid") from exc
    labels = normalized.split(".")
    if (
        any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9-]+", label)
            or label.startswith("-")
            or label.endswith("-")
            for label in labels
        )
        or len(normalized) > 253
    ):
        raise ValueError("TLS pin host is invalid")
    return normalized


def _normalize_https_origin(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("TLS pin origin must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("TLS pin origin must not contain credentials, path, query, or fragment")
    host = _normalize_host(parsed.hostname)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("TLS pin origin port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("TLS pin origin port is invalid")
    try:
        is_ipv6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_ipv6 = False
    origin_host = f"[{host}]" if is_ipv6 else host
    return f"https://{origin_host}{f':{port}' if port is not None else ''}"
