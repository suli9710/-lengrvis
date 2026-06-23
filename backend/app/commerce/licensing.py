"""Offline license verification for the commercialization layer.

Self-hosted Team deployments ship a signed license token that unlocks paid
entitlements without phoning home. Tokens are ``<body>.<signature>`` strings
where ``body`` is base64url(JSON) and ``signature`` is an Ed25519 signature
over the body. Runtime deployments only need ``LENGRVIS_LICENSE_PUBLIC_KEY``;
the private signing key must stay offline in release/admin tooling.

The resolved license plan feeds the same entitlement gating as ``LENGRVIS_PLAN``
(see :mod:`app.commerce.entitlements`); an invalid or expired license never
crashes settings resolution -- it simply does not upgrade the plan.

This intentionally rejects the older symmetric HMAC verifier shape: a verifier
that holds the signing secret can mint arbitrary paid licenses, which is not a
strong commercial authorization boundary.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.commerce.entitlements import Plan, normalize_plan
from app.core.errors import AppError

logger = logging.getLogger(__name__)

LICENSE_KEY_ENV_VAR = "LENGRVIS_LICENSE_KEY"
LICENSE_PUBLIC_KEY_ENV_VAR = "LENGRVIS_LICENSE_PUBLIC_KEY"
LICENSE_SIGNING_KEY_ENV_VAR = "LENGRVIS_LICENSE_SIGNING_KEY"  # Deprecated HMAC-era name; ignored by runtime load.
LICENSE_FILE_NAME = "license.key"


class LicenseError(AppError):
    """Raised when a license token is malformed, unsigned, or expired."""

    def __init__(self, message: str, *, code: str = "invalid_license", status_code: int = 400) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@dataclass(frozen=True)
class License:
    plan: Plan
    subject: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    seats: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        return moment >= self.expires_at

    def is_active(self, *, now: datetime | None = None) -> bool:
        return not self.is_expired(now=now)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError("License token is not valid base64url") from exc


def _load_public_key(public_key: str) -> Ed25519PublicKey:
    text = str(public_key or "").strip()
    if not text:
        raise LicenseError("No license public key configured", code="license_public_key_missing")
    try:
        if "BEGIN PUBLIC KEY" in text:
            key = serialization.load_pem_public_key(text.encode("utf-8"))
            if not isinstance(key, Ed25519PublicKey):
                raise LicenseError("License public key must be Ed25519", code="license_public_key_invalid")
            return key
        return Ed25519PublicKey.from_public_bytes(_b64url_decode(text.removeprefix("ed25519:")))
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001 - cryptography raises several parse errors.
        raise LicenseError("License public key is invalid", code="license_public_key_invalid") from exc


def _load_private_key(private_key: str) -> Ed25519PrivateKey:
    text = str(private_key or "").strip()
    if not text:
        raise LicenseError("A private signing key is required to sign a license")
    try:
        if "BEGIN PRIVATE KEY" in text:
            key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise LicenseError("License private key must be Ed25519", code="license_private_key_invalid")
            return key
        return Ed25519PrivateKey.from_private_bytes(_b64url_decode(text.removeprefix("ed25519:")))
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001 - cryptography raises several parse errors.
        raise LicenseError("License private key is invalid", code="license_private_key_invalid") from exc


def sign_license(payload: dict[str, Any], private_key: str) -> str:
    """Produce a ``<body>.<signature>`` Ed25519 license token (test/admin helper)."""
    signer = _load_private_key(private_key)
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = signer.sign(body.encode("ascii"))
    return f"{body}.{_b64url_encode(signature)}"


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LicenseError(f"License timestamp is invalid: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_license(token: str, public_key: str) -> License:
    """Verify the token signature and decode it (raises :class:`LicenseError`)."""
    if not token or not token.strip():
        raise LicenseError("License token is empty")
    body, _, signature = token.strip().partition(".")
    if not body or not signature:
        raise LicenseError("License token is malformed")
    verifier = _load_public_key(public_key)
    try:
        verifier.verify(_b64url_decode(signature), body.encode("ascii"))
    except InvalidSignature as exc:
        raise LicenseError("License signature does not match", code="license_signature_mismatch") from exc
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("License signature is invalid", code="license_signature_invalid") from exc
    raw = _b64url_decode(body)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LicenseError("License payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LicenseError("License payload must be a JSON object")
    seats_raw = payload.get("seats", 0)
    try:
        seats = max(0, int(seats_raw))
    except (TypeError, ValueError):
        seats = 0
    return License(
        plan=normalize_plan(payload.get("plan")),
        subject=str(payload.get("subject") or payload.get("sub") or ""),
        issued_at=_parse_datetime(payload.get("issued_at") or payload.get("iat")),
        expires_at=_parse_datetime(payload.get("expires_at") or payload.get("exp")),
        seats=seats,
        payload=payload,
    )


def verify_license(token: str, public_key: str, *, now: datetime | None = None) -> License:
    """Parse and ensure the license is currently active (raises on expiry)."""
    license_ = parse_license(token, public_key)
    if license_.is_expired(now=now):
        raise LicenseError("License has expired", code="license_expired", status_code=402)
    return license_


def _read_license_token(settings: Any | None) -> str:
    env_token = os.getenv(LICENSE_KEY_ENV_VAR)
    if env_token and env_token.strip():
        return env_token.strip()
    data_dir = getattr(settings, "data_dir", "") if settings is not None else ""
    if data_dir:
        path = os.path.join(str(data_dir), LICENSE_FILE_NAME)
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    return ""


def _read_public_key() -> str:
    return (os.getenv(LICENSE_PUBLIC_KEY_ENV_VAR) or "").strip()


def load_license(settings: Any | None = None, *, now: datetime | None = None) -> License | None:
    """Best-effort license load. Returns ``None`` when absent/invalid/expired; never raises."""
    token = _read_license_token(settings)
    if not token:
        return None
    public_key = _read_public_key()
    if not public_key:
        if os.getenv(LICENSE_SIGNING_KEY_ENV_VAR):
            logger.warning(
                "License token present but only deprecated %s is set; ignoring symmetric license verifier. "
                "Set %s to an Ed25519 public key.",
                LICENSE_SIGNING_KEY_ENV_VAR,
                LICENSE_PUBLIC_KEY_ENV_VAR,
            )
        else:
            logger.warning("License token present but %s is not set; ignoring license.", LICENSE_PUBLIC_KEY_ENV_VAR)
        return None
    try:
        license_ = parse_license(token, public_key)
    except LicenseError as exc:
        logger.warning("Ignoring invalid license: %s", exc.message)
        return None
    if license_.is_expired(now=now):
        logger.warning("Ignoring expired license (expired at %s).", license_.expires_at)
        return None
    return license_


def resolve_licensed_plan(settings: Any | None = None, *, now: datetime | None = None) -> Plan | None:
    license_ = load_license(settings, now=now)
    return license_.plan if license_ is not None else None


def apply_licensed_plan(settings: Any, *, now: datetime | None = None):
    """Return settings whose ``plan`` reflects a valid, active license (no-op otherwise)."""
    plan = resolve_licensed_plan(settings, now=now)
    if plan is None:
        return settings
    try:
        import dataclasses

        return dataclasses.replace(settings, plan=plan.value)
    except Exception:  # noqa: BLE001 - never let licensing break settings resolution
        try:
            settings.plan = plan.value
        except Exception:  # noqa: BLE001
            return settings
        return settings
