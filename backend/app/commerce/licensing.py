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
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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
LICENSE_REVOCATIONS_ENV_VAR = "LENGRVIS_LICENSE_REVOCATIONS"
LICENSE_REVOCATIONS_FILE_NAME = "license-revocations.key"
COMMERCIAL_RELEASE_ENV_VAR = "LENGRVIS_COMMERCIAL_RELEASE"
MAX_LICENSE_TOKEN_BYTES = 64 * 1024
MAX_REVOCATION_TOKEN_BYTES = 1024 * 1024
_TRUE_VALUES = {"1", "true", "yes", "on"}


class LicenseError(AppError):
    """Raised when a license token is malformed, unsigned, or expired."""

    def __init__(self, message: str, *, code: str = "invalid_license", status_code: int = 400) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@dataclass(frozen=True)
class License:
    plan: Plan
    license_id: str = ""
    subject: str = ""
    issuer: str = ""
    replaces: str = ""
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


@dataclass(frozen=True)
class RevocationManifest:
    generated_at: datetime | None
    issuer: str
    revoked_license_ids: frozenset[str]
    records: tuple[dict[str, Any], ...]
    payload: dict[str, Any] = field(default_factory=dict)

    def is_revoked(self, license_id: str) -> bool:
        return bool(license_id) and license_id in self.revoked_license_ids


def _license_file_path(settings: Any | None) -> Path | None:
    data_dir = getattr(settings, "data_dir", "") if settings is not None else ""
    if not data_dir:
        return None
    return Path(str(data_dir)).expanduser().resolve() / LICENSE_FILE_NAME


def _revocation_file_path(settings: Any | None) -> Path | None:
    data_dir = getattr(settings, "data_dir", "") if settings is not None else ""
    if not data_dir:
        return None
    return Path(str(data_dir)).expanduser().resolve() / LICENSE_REVOCATIONS_FILE_NAME


def _license_token_with_source(settings: Any | None) -> tuple[str, str | None]:
    env_token = os.getenv(LICENSE_KEY_ENV_VAR)
    if env_token and env_token.strip():
        return env_token.strip(), "environment"
    path = _license_file_path(settings)
    if path is None:
        return "", None
    try:
        return path.read_text(encoding="utf-8").strip(), "file"
    except OSError:
        return "", None


def _revocation_token_with_source(settings: Any | None) -> tuple[str, str | None]:
    env_token = os.getenv(LICENSE_REVOCATIONS_ENV_VAR)
    if env_token and env_token.strip():
        return env_token.strip(), "environment"
    path = _revocation_file_path(settings)
    if path is None:
        return "", None
    try:
        return path.read_text(encoding="utf-8").strip(), "file"
    except OSError:
        return "", None


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


def _load_private_key(private_key: str, *, password: bytes | None = None) -> Ed25519PrivateKey:
    text = str(private_key or "").strip()
    if not text:
        raise LicenseError("A private signing key is required to sign a license")
    try:
        if "PRIVATE KEY" in text:
            key = serialization.load_pem_private_key(text.encode("utf-8"), password=password)
            if not isinstance(key, Ed25519PrivateKey):
                raise LicenseError("License private key must be Ed25519", code="license_private_key_invalid")
            return key
        return Ed25519PrivateKey.from_private_bytes(_b64url_decode(text.removeprefix("ed25519:")))
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001 - cryptography raises several parse errors.
        raise LicenseError("License private key is invalid", code="license_private_key_invalid") from exc


def _sign_payload(payload: dict[str, Any], private_key: str, *, password: bytes | None = None) -> str:
    signer = _load_private_key(private_key, password=password)
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = signer.sign(body.encode("ascii"))
    return f"{body}.{_b64url_encode(signature)}"


def sign_license(payload: dict[str, Any], private_key: str, *, password: bytes | None = None) -> str:
    """Produce a ``<body>.<signature>`` Ed25519 license token (test/admin helper)."""
    return _sign_payload(payload, private_key, password=password)


def sign_revocation_manifest(
    payload: dict[str, Any],
    private_key: str,
    *,
    password: bytes | None = None,
) -> str:
    """Sign a revocation manifest with the same offline Ed25519 trust root."""
    return _sign_payload(payload, private_key, password=password)


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


def _parse_signed_payload(token: str, public_key: str, *, label: str) -> dict[str, Any]:
    if not token or not token.strip():
        raise LicenseError(f"{label} token is empty")
    body, _, signature = token.strip().partition(".")
    if not body or not signature:
        raise LicenseError(f"{label} token is malformed")
    verifier = _load_public_key(public_key)
    try:
        verifier.verify(_b64url_decode(signature), body.encode("ascii"))
    except InvalidSignature as exc:
        raise LicenseError(f"{label} signature does not match", code="license_signature_mismatch") from exc
    except LicenseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LicenseError(f"{label} signature is invalid", code="license_signature_invalid") from exc
    raw = _b64url_decode(body)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LicenseError(f"{label} payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LicenseError(f"{label} payload must be a JSON object")
    return payload


def parse_license(token: str, public_key: str) -> License:
    """Verify the token signature and decode it (raises :class:`LicenseError`)."""
    payload = _parse_signed_payload(token, public_key, label="License")
    seats_raw = payload.get("seats", 0)
    try:
        seats = max(0, int(seats_raw))
    except (TypeError, ValueError):
        seats = 0
    return License(
        plan=normalize_plan(payload.get("plan")),
        license_id=str(payload.get("license_id") or payload.get("jti") or "").strip(),
        subject=str(payload.get("subject") or payload.get("sub") or ""),
        issuer=str(payload.get("issuer") or payload.get("iss") or ""),
        replaces=str(payload.get("replaces") or "").strip(),
        issued_at=_parse_datetime(payload.get("issued_at") or payload.get("iat")),
        expires_at=_parse_datetime(payload.get("expires_at") or payload.get("exp")),
        seats=seats,
        payload=payload,
    )


def parse_revocation_manifest(token: str, public_key: str) -> RevocationManifest:
    payload = _parse_signed_payload(token, public_key, label="License revocation manifest")
    if payload.get("schema") != 1:
        raise LicenseError(
            "License revocation manifest schema is unsupported",
            code="license_revocation_schema_invalid",
        )
    raw_records = payload.get("revoked")
    if not isinstance(raw_records, list):
        raise LicenseError(
            "License revocation manifest must contain a revoked list",
            code="license_revocation_payload_invalid",
        )
    records: list[dict[str, Any]] = []
    revoked_ids: set[str] = set()
    for item in raw_records:
        if not isinstance(item, dict):
            raise LicenseError(
                "License revocation record must be an object",
                code="license_revocation_payload_invalid",
            )
        license_id = str(item.get("license_id") or "").strip()
        if not license_id:
            raise LicenseError(
                "License revocation record is missing license_id",
                code="license_revocation_payload_invalid",
            )
        records.append(dict(item))
        revoked_ids.add(license_id)
    return RevocationManifest(
        generated_at=_parse_datetime(payload.get("generated_at")),
        issuer=str(payload.get("issuer") or ""),
        revoked_license_ids=frozenset(revoked_ids),
        records=tuple(records),
        payload=payload,
    )


def verify_license(
    token: str,
    public_key: str,
    *,
    now: datetime | None = None,
    revocations: RevocationManifest | None = None,
) -> License:
    """Parse and ensure the license is currently active (raises on expiry)."""
    license_ = parse_license(token, public_key)
    if license_.is_expired(now=now):
        raise LicenseError("License has expired", code="license_expired", status_code=402)
    if revocations is not None and revocations.is_revoked(license_.license_id):
        raise LicenseError("License has been revoked", code="license_revoked", status_code=402)
    return license_


def _read_license_token(settings: Any | None) -> str:
    return _license_token_with_source(settings)[0]


def _read_public_key() -> str:
    return (os.getenv(LICENSE_PUBLIC_KEY_ENV_VAR) or "").strip()


def commercial_release_enabled() -> bool:
    return str(os.getenv(COMMERCIAL_RELEASE_ENV_VAR, "")).strip().lower() in _TRUE_VALUES


def load_revocation_manifest(
    settings: Any | None = None,
    *,
    public_key: str | None = None,
) -> tuple[RevocationManifest | None, str | None]:
    token, source = _revocation_token_with_source(settings)
    if not token:
        return None, None
    if len(token.encode("utf-8")) > MAX_REVOCATION_TOKEN_BYTES:
        raise LicenseError(
            "License revocation manifest is too large",
            code="license_revocation_token_too_large",
            status_code=413,
        )
    return parse_revocation_manifest(token, public_key or _read_public_key()), source


def license_status(settings: Any | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a renderer-safe diagnosis without exposing the token or verifier."""
    token, source = _license_token_with_source(settings)
    public_key = _read_public_key()
    base: dict[str, Any] = {
        "state": "absent",
        "present": bool(token),
        "active": False,
        "expired": False,
        "verifier_configured": bool(public_key),
        "managed_by": source,
    }
    if not token:
        return base
    if not public_key:
        return {**base, "state": "verifier_unconfigured", "error_code": "license_public_key_missing"}
    try:
        license_ = parse_license(token, public_key)
    except LicenseError as exc:
        return {**base, "state": "invalid", "error_code": exc.code}
    try:
        revocations, revocation_source = load_revocation_manifest(settings, public_key=public_key)
    except LicenseError as exc:
        return {
            **base,
            "state": "revocation_data_invalid",
            "error_code": exc.code,
            "license_id": license_.license_id or None,
            "revocation_capable": bool(license_.license_id),
        }
    expired = license_.is_expired(now=now)
    revoked = bool(revocations and revocations.is_revoked(license_.license_id))
    state = "revoked" if revoked else "expired" if expired else "active"
    return {
        **base,
        "state": state,
        "active": not expired and not revoked,
        "expired": expired,
        "revoked": revoked,
        "license_id": license_.license_id or None,
        "issuer": license_.issuer or None,
        "replaces": license_.replaces or None,
        "revocation_capable": bool(license_.license_id),
        "revocation_source": revocation_source,
        "revocation_generated_at": (
            revocations.generated_at.isoformat() if revocations and revocations.generated_at else None
        ),
        "plan": license_.plan.value,
        "subject": license_.subject,
        "seats": license_.seats,
        "issued_at": license_.issued_at.isoformat() if license_.issued_at else None,
        "expires_at": license_.expires_at.isoformat() if license_.expires_at else None,
    }


def install_license(token: str, settings: Any, *, now: datetime | None = None) -> License:
    """Verify and atomically persist a locally imported offline license."""
    if os.getenv(LICENSE_KEY_ENV_VAR, "").strip():
        raise LicenseError(
            "The active license is managed by deployment configuration and cannot be replaced in the app.",
            code="license_managed_externally",
            status_code=409,
        )
    normalized = str(token or "").strip()
    if not normalized:
        raise LicenseError("License token is empty")
    if len(normalized.encode("utf-8")) > MAX_LICENSE_TOKEN_BYTES:
        raise LicenseError("License token is too large", code="license_token_too_large", status_code=413)
    public_key = _read_public_key()
    revocations, _ = load_revocation_manifest(settings, public_key=public_key)
    license_ = verify_license(normalized, public_key, now=now, revocations=revocations)
    path = _license_file_path(settings)
    if path is None:
        raise LicenseError(
            "License storage directory is unavailable", code="license_storage_unavailable", status_code=503
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=f".{LICENSE_FILE_NAME}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(normalized)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    except OSError as exc:
        raise LicenseError("Unable to store the license", code="license_storage_failed", status_code=503) from exc
    return license_


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
        revocations, _ = load_revocation_manifest(settings, public_key=public_key)
        license_ = verify_license(token, public_key, now=now, revocations=revocations)
    except LicenseError as exc:
        logger.warning("Ignoring invalid license: %s", exc.message)
        return None
    return license_


def resolve_licensed_plan(settings: Any | None = None, *, now: datetime | None = None) -> Plan | None:
    license_ = load_license(settings, now=now)
    return license_.plan if license_ is not None else None


def apply_licensed_plan(settings: Any, *, now: datetime | None = None):
    """Return settings whose ``plan`` reflects a valid, active license (no-op otherwise)."""
    plan = resolve_licensed_plan(settings, now=now)
    if plan is None and not commercial_release_enabled():
        return settings
    resolved_plan = plan.value if plan is not None else Plan.FREE.value
    try:
        if hasattr(settings, "model_copy"):
            return settings.model_copy(update={"plan": resolved_plan})
        settings.plan = resolved_plan
        return settings
    except Exception:  # noqa: BLE001 - never let licensing break settings resolution
        try:
            settings.plan = resolved_plan
        except Exception:  # noqa: BLE001
            return settings
        return settings
