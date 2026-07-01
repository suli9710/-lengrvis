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
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.commerce.device_identity import (
    DeviceIdentityError,
    collect_activation_device_identity,
)
from app.commerce.entitlements import PLAN_ENV_VAR, Plan, normalize_plan
from app.core.errors import AppError

logger = logging.getLogger(__name__)

LICENSE_KEY_ENV_VAR = "LENGRVIS_LICENSE_KEY"
LICENSE_PUBLIC_KEY_ENV_VAR = "LENGRVIS_LICENSE_PUBLIC_KEY"
LICENSE_SIGNING_KEY_ENV_VAR = "LENGRVIS_LICENSE_SIGNING_KEY"  # Deprecated HMAC-era name; ignored by runtime load.
LICENSE_FILE_NAME = "license.key"
LICENSE_REVOCATIONS_ENV_VAR = "LENGRVIS_LICENSE_REVOCATIONS"
LICENSE_REVOCATIONS_FILE_NAME = "license-revocations.key"
COMMERCIAL_RELEASE_ENV_VAR = "LENGRVIS_COMMERCIAL_RELEASE"
SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS_ENV_VAR = "LENGRVIS_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS"
DEFAULT_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS = 24 * 60 * 60
HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS_ENV_VAR = "LENGRVIS_HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS"
DEFAULT_HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS = 15 * 60
LICENSE_REVOCATION_MAX_AGE_SECONDS_ENV_VAR = "LENGRVIS_LICENSE_REVOCATION_MAX_AGE_SECONDS"
DEFAULT_LICENSE_REVOCATION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_LICENSE_TOKEN_BYTES = 64 * 1024
MAX_REVOCATION_TOKEN_BYTES = 1024 * 1024
_TRUE_VALUES = {"1", "true", "yes", "on"}
_DEVICE_BINDING_NOT_CHECKED = object()
_STRONG_DEVICE_SECRET_STORAGE = {"dpapi", "keyring"}

_SIGNED_PAYLOAD_LABELS = {
    "License": "许可证",
    "License revocation manifest": "许可证吊销清单",
}


def _zh_label(label: str) -> str:
    return _SIGNED_PAYLOAD_LABELS.get(label, label)


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
    subscription_id: str = ""
    subscription_status: str = ""
    renews_at: datetime | None = None
    cancel_at_period_end: bool = False
    device_id: str = ""
    device_fingerprint: str = ""
    order_ref: str = ""
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
        raise LicenseError("许可证令牌编码无效。") from exc


def _load_public_key(public_key: str) -> Ed25519PublicKey:
    text = str(public_key or "").strip()
    if not text:
        raise LicenseError("未配置许可证验签公钥。", code="license_public_key_missing")
    try:
        if "BEGIN PUBLIC KEY" in text:
            key = serialization.load_pem_public_key(text.encode("utf-8"))
            if not isinstance(key, Ed25519PublicKey):
                raise LicenseError("许可证验签公钥必须是 Ed25519。", code="license_public_key_invalid")
            return key
        return Ed25519PublicKey.from_public_bytes(_b64url_decode(text.removeprefix("ed25519:")))
    except LicenseError:
        raise
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise LicenseError("许可证验签公钥无效。", code="license_public_key_invalid") from exc


def _load_private_key(private_key: str, *, password: bytes | None = None) -> Ed25519PrivateKey:
    text = str(private_key or "").strip()
    if not text:
        raise LicenseError("签发许可证需要配置私钥。")
    try:
        if "PRIVATE KEY" in text:
            key = serialization.load_pem_private_key(text.encode("utf-8"), password=password)
            if not isinstance(key, Ed25519PrivateKey):
                raise LicenseError("许可证签名私钥必须是 Ed25519。", code="license_private_key_invalid")
            return key
        return Ed25519PrivateKey.from_private_bytes(_b64url_decode(text.removeprefix("ed25519:")))
    except LicenseError:
        raise
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise LicenseError("许可证签名私钥无效。", code="license_private_key_invalid") from exc


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
        raise LicenseError(f"许可证时间字段无效：{value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_signed_payload(token: str, public_key: str, *, label: str) -> dict[str, Any]:
    label_zh = _zh_label(label)
    if not token or not token.strip():
        raise LicenseError(f"{label_zh}令牌为空。")
    body, _, signature = token.strip().partition(".")
    if not body or not signature:
        raise LicenseError(f"{label_zh}令牌格式不正确。")
    verifier = _load_public_key(public_key)
    try:
        verifier.verify(_b64url_decode(signature), body.encode("ascii"))
    except InvalidSignature as exc:
        raise LicenseError(f"{label_zh}签名不匹配。", code="license_signature_mismatch") from exc
    except LicenseError:
        raise
    except (TypeError, UnicodeError, ValueError) as exc:
        raise LicenseError(f"{label_zh}签名无效。", code="license_signature_invalid") from exc
    raw = _b64url_decode(body)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LicenseError(f"{label_zh}内容不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise LicenseError(f"{label_zh}内容必须是 JSON 对象。")
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
        subscription_id=str(payload.get("subscription_id") or "").strip(),
        subscription_status=str(payload.get("subscription_status") or "").strip().lower(),
        renews_at=_parse_datetime(payload.get("renews_at")),
        cancel_at_period_end=bool(payload.get("cancel_at_period_end")),
        device_id=str(payload.get("device_id") or "").strip(),
        device_fingerprint=str(payload.get("device_fingerprint") or "").strip(),
        order_ref=str(payload.get("order_ref") or "").strip(),
        issued_at=_parse_datetime(payload.get("issued_at") or payload.get("iat")),
        expires_at=_parse_datetime(payload.get("expires_at") or payload.get("exp")),
        seats=seats,
        payload=payload,
    )


def parse_revocation_manifest(token: str, public_key: str) -> RevocationManifest:
    payload = _parse_signed_payload(token, public_key, label="License revocation manifest")
    if payload.get("schema") != 1:
        raise LicenseError(
            "许可证吊销清单版本不受支持。",
            code="license_revocation_schema_invalid",
        )
    raw_records = payload.get("revoked")
    if not isinstance(raw_records, list):
        raise LicenseError(
            "许可证吊销清单必须包含吊销列表。",
            code="license_revocation_payload_invalid",
        )
    records: list[dict[str, Any]] = []
    revoked_ids: set[str] = set()
    for item in raw_records:
        if not isinstance(item, dict):
            raise LicenseError(
                "许可证吊销记录必须是对象。",
                code="license_revocation_payload_invalid",
            )
        license_id = str(item.get("license_id") or "").strip()
        if not license_id:
            raise LicenseError(
                "许可证吊销记录缺少许可证编号。",
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
    expected_device_id: str | None | object = _DEVICE_BINDING_NOT_CHECKED,
    expected_device_fingerprint: str | None | object = _DEVICE_BINDING_NOT_CHECKED,
) -> License:
    """Parse and ensure the license is currently active (raises on expiry)."""
    license_ = parse_license(token, public_key)
    if license_.device_id and expected_device_id is not _DEVICE_BINDING_NOT_CHECKED:
        if not expected_device_id:
            raise LicenseError(
                "无法核验许可证绑定的设备。",
                code="license_device_unverified",
                status_code=402,
            )
        if not hmac.compare_digest(license_.device_id, str(expected_device_id)):
            raise LicenseError("许可证绑定到另一台设备。", code="license_device_mismatch", status_code=402)
    if license_.device_fingerprint and expected_device_fingerprint is not _DEVICE_BINDING_NOT_CHECKED:
        if not expected_device_fingerprint:
            raise LicenseError(
                "无法核验许可证绑定的设备指纹。",
                code="license_device_fingerprint_unverified",
                status_code=402,
            )
        if not hmac.compare_digest(license_.device_fingerprint, str(expected_device_fingerprint)):
            raise LicenseError(
                "许可证绑定到另一台设备的硬件指纹。",
                code="license_device_fingerprint_mismatch",
                status_code=402,
            )
    if commercial_release_enabled() and license_.device_id and not license_.device_fingerprint:
        raise LicenseError(
            "商业发行版要求许可证包含设备硬件指纹。",
            code="license_device_fingerprint_missing",
            status_code=402,
        )
    activation_binding_error = _commercial_activation_device_binding_error(license_)
    if activation_binding_error:
        raise LicenseError(
            "商业发行版要求激活许可证包含强设备绑定证明。",
            code=activation_binding_error,
            status_code=402,
        )
    if license_.is_expired(now=now):
        raise LicenseError("许可证已过期。", code="license_expired", status_code=402)
    if license_.subscription_status and license_.subscription_status not in {"active", "trialing"}:
        raise LicenseError(
            "订阅当前不可用。",
            code=f"subscription_{license_.subscription_status}",
            status_code=402,
        )
    if revocations is not None and revocations.is_revoked(license_.license_id):
        raise LicenseError("许可证已被吊销。", code="license_revoked", status_code=402)
    return license_


def _read_license_token(settings: Any | None) -> str:
    return _license_token_with_source(settings)[0]


def _read_public_key() -> str:
    return (os.getenv(LICENSE_PUBLIC_KEY_ENV_VAR) or "").strip()


def _expected_device_binding(settings: Any | None) -> tuple[str | None, str | None]:
    if settings is None:
        return None, None
    try:
        identity = collect_activation_device_identity(settings)
        return identity.device_id, identity.fingerprint
    except DeviceIdentityError as exc:
        if commercial_release_enabled() or exc.code == "activation_secret_insecure_permissions":
            raise LicenseError(
                exc.message,
                code=exc.code,
                status_code=exc.status_code,
            ) from exc
        return None, None
    except (RuntimeError, OSError):
        if commercial_release_enabled():
            raise LicenseError(
                "无法收集设备指纹。",
                code="activation_device_identity_unavailable",
                status_code=503,
            ) from None
        return None, None


def commercial_release_enabled() -> bool:
    return str(os.getenv(COMMERCIAL_RELEASE_ENV_VAR, "")).strip().lower() in _TRUE_VALUES


def _activation_source(license_: License) -> str:
    activation = license_.payload.get("activation")
    if not isinstance(activation, dict):
        return ""
    return str(activation.get("source") or "").strip().lower()


def _commercial_activation_device_binding_error(license_: License) -> str | None:
    if not commercial_release_enabled() or _activation_source(license_) != "activation_server":
        return None
    activation = license_.payload.get("activation")
    binding = activation.get("device_binding") if isinstance(activation, dict) else None
    if not isinstance(binding, dict):
        return "license_device_proof_missing"
    strength = str(binding.get("strength") or "").strip().lower()
    storage = str(binding.get("secret_storage") or "").strip().lower()
    binding_fingerprint = str(binding.get("fingerprint") or "").strip()
    try:
        hardware_signal_count = int(binding.get("hardware_signal_count") or 0)
    except (TypeError, ValueError):
        hardware_signal_count = 0
    if not binding_fingerprint or not hmac.compare_digest(binding_fingerprint, license_.device_fingerprint):
        return "license_device_proof_mismatch"
    if strength != "strong" or storage not in _STRONG_DEVICE_SECRET_STORAGE or hardware_signal_count < 1:
        return "license_device_proof_weak"
    return None


def _plan_env_override_allowed() -> bool:
    """Dev/test escape hatch: allow ``LENGRVIS_PLAN`` to select paid tiers without a license."""
    if commercial_release_enabled():
        return False
    if str(os.getenv("LENGRVIS_TEST", "")).strip().lower() in _TRUE_VALUES:
        return True
    return False


def _subscription_license_requires_online_confirmation(license_: License) -> bool:
    activation = license_.payload.get("activation")
    activation_source = ""
    if isinstance(activation, dict):
        activation_source = str(activation.get("source") or "").strip().lower()
    return bool(license_.subscription_id or activation_source == "activation_server")


def _subscription_license_refresh_ttl_seconds(*, ttl_seconds: int | None = None) -> int:
    if ttl_seconds is not None:
        return max(1, min(7 * 24 * 60 * 60, int(ttl_seconds)))
    raw = str(os.getenv(SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS_ENV_VAR, "")).strip()
    if not raw:
        return DEFAULT_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS
    try:
        return max(1, min(7 * 24 * 60 * 60, int(raw)))
    except ValueError:
        return DEFAULT_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS


def high_risk_subscription_refresh_ttl_seconds() -> int:
    raw = str(os.getenv(HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS_ENV_VAR, "")).strip()
    if not raw:
        return DEFAULT_HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS
    try:
        return max(1, min(DEFAULT_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS, int(raw)))
    except ValueError:
        return DEFAULT_HIGH_RISK_SUBSCRIPTION_REFRESH_TTL_SECONDS


def revocation_manifest_max_age_seconds() -> int:
    raw = str(os.getenv(LICENSE_REVOCATION_MAX_AGE_SECONDS_ENV_VAR, "")).strip()
    if not raw:
        return DEFAULT_LICENSE_REVOCATION_MAX_AGE_SECONDS
    try:
        return max(60, min(30 * 24 * 60 * 60, int(raw)))
    except ValueError:
        return DEFAULT_LICENSE_REVOCATION_MAX_AGE_SECONDS


def _commercial_revocation_freshness_error(
    license_: License,
    revocations: RevocationManifest | None,
    *,
    now: datetime | None = None,
) -> str | None:
    if (
        not commercial_release_enabled()
        or license_.plan is Plan.FREE
        or _subscription_license_requires_online_confirmation(license_)
    ):
        return None
    if not license_.license_id:
        return "license_revocation_id_missing"
    if revocations is None:
        return "license_revocation_required"
    generated_at = revocations.generated_at
    if generated_at is None:
        return "license_revocation_stale"
    moment = now or datetime.now(UTC)
    if generated_at > moment + timedelta(minutes=5):
        return "license_revocation_time_invalid"
    if moment - generated_at > timedelta(seconds=revocation_manifest_max_age_seconds()):
        return "license_revocation_stale"
    return None


def _enforce_commercial_revocation_freshness(
    license_: License,
    revocations: RevocationManifest | None,
    *,
    now: datetime | None = None,
) -> None:
    error = _commercial_revocation_freshness_error(license_, revocations, now=now)
    if error:
        raise LicenseError(
            "商业发行版要求离线付费许可证配套新鲜的签名吊销清单。",
            code=error,
            status_code=402,
        )


def _subscription_confirmation_expires_at(
    license_: License,
    *,
    ttl_seconds: int | None = None,
) -> datetime | None:
    if not _subscription_license_requires_online_confirmation(license_):
        return None
    if license_.issued_at is None:
        return None
    return license_.issued_at + timedelta(seconds=_subscription_license_refresh_ttl_seconds(ttl_seconds=ttl_seconds))


def _subscription_confirmation_status(
    license_: License,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    required = _subscription_license_requires_online_confirmation(license_)
    expires_at = _subscription_confirmation_expires_at(license_, ttl_seconds=ttl_seconds)
    moment = now or datetime.now(UTC)
    fresh = not required or (expires_at is not None and moment < expires_at)
    activation = license_.payload.get("activation")
    activation_source = ""
    if isinstance(activation, dict):
        activation_source = str(activation.get("source") or "")
    return {
        "required": required,
        "fresh": fresh,
        "checked_at": license_.issued_at.isoformat() if license_.issued_at else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "ttl_seconds": _subscription_license_refresh_ttl_seconds(ttl_seconds=ttl_seconds) if required else None,
        "activation_source": activation_source or None,
    }


def _subscription_license_needs_refresh(
    license_: License,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> bool:
    if not _subscription_license_requires_online_confirmation(license_):
        return False
    if license_.is_expired(now=now):
        return True
    if license_.subscription_status and license_.subscription_status not in {"active", "trialing"}:
        return True
    return not bool(_subscription_confirmation_status(license_, now=now, ttl_seconds=ttl_seconds)["fresh"])


def _enforce_subscription_confirmation(
    license_: License,
    *,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> None:
    confirmation = _subscription_confirmation_status(license_, now=now, ttl_seconds=ttl_seconds)
    if confirmation["required"] and not confirmation["fresh"]:
        raise LicenseError(
            "订阅许可证需要重新联网确认。",
            code="subscription_confirmation_required",
            status_code=402,
        )


def require_activation_response_nonce(license_: License, expected_nonce_sha256: str) -> None:
    """Ensure an activation-server license belongs to the current request.

    The activation client sends a fresh nonce and the server signs its SHA-256
    hash into the returned license. Verifying that hash before persistence
    prevents replay of an older still-valid token for the same device.
    """
    expected = str(expected_nonce_sha256 or "").strip().lower()
    activation = license_.payload.get("activation")
    actual = ""
    if isinstance(activation, dict):
        actual = str(activation.get("nonce_sha256") or "").strip().lower()
    if not expected or not actual or not hmac.compare_digest(actual, expected):
        raise LicenseError(
            "激活服务返回的许可证不是本次请求的结果。",
            code="activation_nonce_mismatch",
            status_code=502,
        )


def _refresh_subscription_license(
    token: str,
    settings: Any | None,
    *,
    now: datetime | None = None,
    source: str | None = None,
) -> tuple[License | None, str | None]:
    if settings is None:
        return None, "subscription_confirmation_unavailable"
    try:
        from app.commerce.activation import ActivationError, refresh_license_with_server

        refreshed = refresh_license_with_server(
            token,
            settings,
            now=now,
            persist=source != "environment",
        )
        return refreshed, None
    except ActivationError as exc:
        logger.warning("Subscription license online refresh failed: %s", exc.message)
        return None, exc.code
    except LicenseError as exc:
        logger.warning("Subscription license online refresh returned an invalid license: %s", exc.message)
        return None, exc.code
    except Exception as exc:  # noqa: BLE001 - runtime licensing must fail closed without crashing startup.
        logger.warning("Subscription license online refresh failed: %s", exc)
        return None, "subscription_confirmation_failed"


def _verify_runtime_license(
    token: str,
    public_key: str,
    *,
    settings: Any | None,
    now: datetime | None = None,
    revocations: RevocationManifest | None = None,
    source: str | None = None,
) -> License:
    parsed = parse_license(token, public_key)
    if revocations is not None and revocations.is_revoked(parsed.license_id):
        raise LicenseError("许可证已被吊销。", code="license_revoked", status_code=402)
    _enforce_commercial_revocation_freshness(parsed, revocations, now=now)
    if _subscription_license_needs_refresh(parsed, now=now):
        refreshed, _ = _refresh_subscription_license(token, settings, now=now, source=source)
        if refreshed is not None:
            return refreshed
    expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
    license_ = verify_license(
        token,
        public_key,
        now=now,
        revocations=revocations,
        expected_device_id=expected_device_id,
        expected_device_fingerprint=expected_device_fingerprint,
    )
    _enforce_subscription_confirmation(license_, now=now)
    return license_


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
            "许可证吊销清单过大。",
            code="license_revocation_token_too_large",
            status_code=413,
        )
    return parse_revocation_manifest(token, public_key or _read_public_key()), source


def license_status(settings: Any | None = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a renderer-safe diagnosis without exposing the token or verifier."""
    token, source = _license_token_with_source(settings)
    public_key = _read_public_key()
    requested_env_plan = normalize_plan(os.getenv(PLAN_ENV_VAR))
    plan_env_ignored = requested_env_plan != Plan.FREE and not _plan_env_override_allowed()
    base: dict[str, Any] = {
        "state": "absent",
        "present": bool(token),
        "active": False,
        "expired": False,
        "verifier_configured": bool(public_key),
        "managed_by": source,
        "requested_env_plan": requested_env_plan.value,
        "plan_env_ignored": plan_env_ignored,
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
    try:
        expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
    except LicenseError as exc:
        return {
            **base,
            "state": "device_unverified",
            "active": False,
            "error_code": exc.code,
            "license_id": license_.license_id or None,
            "device_id": license_.device_id or None,
            "device_fingerprint": license_.device_fingerprint or None,
            "plan": license_.plan.value,
        }
    refresh_error_code: str | None = None

    def _local_flags(current: License) -> tuple[bool, bool, bool, bool, bool, bool, dict[str, Any]]:
        expired_current = current.is_expired(now=now)
        device_unverified_current = bool(current.device_id and not expected_device_id)
        device_mismatch_current = bool(
            current.device_id and expected_device_id and not hmac.compare_digest(current.device_id, expected_device_id)
        )
        fingerprint_unverified_current = bool(current.device_fingerprint and not expected_device_fingerprint)
        fingerprint_mismatch_current = bool(
            current.device_fingerprint
            and expected_device_fingerprint
            and not hmac.compare_digest(current.device_fingerprint, expected_device_fingerprint)
        )
        subscription_inactive_current = bool(
            current.subscription_status and current.subscription_status not in {"active", "trialing"}
        )
        confirmation_current = _subscription_confirmation_status(current, now=now)
        return (
            expired_current,
            device_unverified_current,
            device_mismatch_current,
            fingerprint_unverified_current,
            fingerprint_mismatch_current,
            subscription_inactive_current,
            confirmation_current,
        )

    (
        expired,
        device_unverified,
        device_mismatch,
        fingerprint_unverified,
        fingerprint_mismatch,
        subscription_inactive,
        confirmation,
    ) = _local_flags(license_)
    revoked = bool(revocations and revocations.is_revoked(license_.license_id))
    revocation_freshness_error = _commercial_revocation_freshness_error(license_, revocations, now=now)
    if (
        not revoked
        and not revocation_freshness_error
        and not device_unverified
        and not device_mismatch
        and not fingerprint_unverified
        and not fingerprint_mismatch
        and _subscription_license_needs_refresh(license_, now=now)
    ):
        refreshed, refresh_error_code = _refresh_subscription_license(token, settings, now=now, source=source)
        if refreshed is not None:
            license_ = refreshed
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
            try:
                expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
            except LicenseError as exc:
                return {
                    **base,
                    "state": "device_unverified",
                    "active": False,
                    "error_code": exc.code,
                    "license_id": license_.license_id or None,
                    "device_id": license_.device_id or None,
                    "device_fingerprint": license_.device_fingerprint or None,
                    "plan": license_.plan.value,
                }
            (
                expired,
                device_unverified,
                device_mismatch,
                fingerprint_unverified,
                fingerprint_mismatch,
                subscription_inactive,
                confirmation,
            ) = _local_flags(license_)
            revoked = bool(revocations and revocations.is_revoked(license_.license_id))
            revocation_freshness_error = _commercial_revocation_freshness_error(license_, revocations, now=now)
            refresh_error_code = None
    confirmation_stale = bool(confirmation["required"] and not confirmation["fresh"])
    confirmation_failed = bool(refresh_error_code and confirmation_stale and not expired and not subscription_inactive)
    fingerprint_missing = bool(commercial_release_enabled() and license_.device_id and not license_.device_fingerprint)
    device_binding_error = _commercial_activation_device_binding_error(license_)
    state = (
        "revoked"
        if revoked
        else "revocation_required"
        if revocation_freshness_error == "license_revocation_required"
        else "revocation_stale"
        if revocation_freshness_error
        else "subscription_confirmation_failed"
        if confirmation_failed
        else "expired"
        if expired
        else "device_fingerprint_missing"
        if fingerprint_missing
        else "device_proof_missing"
        if device_binding_error == "license_device_proof_missing"
        else "device_proof_mismatch"
        if device_binding_error == "license_device_proof_mismatch"
        else "device_proof_weak"
        if device_binding_error
        else "device_mismatch"
        if device_mismatch
        else "device_fingerprint_mismatch"
        if fingerprint_mismatch
        else "device_unverified"
        if device_unverified or fingerprint_unverified
        else "subscription_inactive"
        if subscription_inactive
        else "subscription_confirmation_required"
        if confirmation_stale
        else "active"
    )
    active = not any(
        (
            expired,
            revoked,
            bool(revocation_freshness_error),
            fingerprint_missing,
            bool(device_binding_error),
            device_mismatch,
            device_unverified,
            fingerprint_mismatch,
            fingerprint_unverified,
            subscription_inactive,
            confirmation_stale,
            confirmation_failed,
        )
    )
    return {
        **base,
        "state": state,
        "active": active,
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
        "plan_env_ignored": False,
        "subject": license_.subject,
        "seats": license_.seats,
        "subscription_id": license_.subscription_id or None,
        "subscription_status": license_.subscription_status or None,
        "renews_at": license_.renews_at.isoformat() if license_.renews_at else None,
        "cancel_at_period_end": license_.cancel_at_period_end,
        "subscription_confirmation_required": confirmation["required"],
        "subscription_confirmation_fresh": confirmation["fresh"],
        "subscription_confirmation_checked_at": confirmation["checked_at"],
        "subscription_confirmation_expires_at": confirmation["expires_at"],
        "subscription_confirmation_ttl_seconds": confirmation["ttl_seconds"],
        "activation_source": confirmation["activation_source"],
        "device_id": license_.device_id or None,
        "device_fingerprint": license_.device_fingerprint or None,
        "order_ref": license_.order_ref or None,
        "issued_at": license_.issued_at.isoformat() if license_.issued_at else None,
        "expires_at": license_.expires_at.isoformat() if license_.expires_at else None,
        "error_code": (
            revocation_freshness_error
            if revocation_freshness_error
            else "license_device_fingerprint_missing"
            if fingerprint_missing
            else device_binding_error
            if device_binding_error
            else "license_device_mismatch"
            if device_mismatch
            else "license_device_fingerprint_mismatch"
            if fingerprint_mismatch
            else "license_device_unverified"
            if device_unverified
            else "license_device_fingerprint_unverified"
            if fingerprint_unverified
            else f"subscription_{license_.subscription_status}"
            if subscription_inactive
            else refresh_error_code
            if confirmation_failed
            else "subscription_confirmation_required"
            if confirmation_stale
            else None
        ),
    }


def subscription_confirmation_fresh_for_high_risk(settings: Any | None = None, *, now: datetime | None = None) -> bool:
    """Return whether subscription online confirmation is fresh for high-risk remote control."""
    return subscription_confirmation_fresh(
        settings,
        ttl_seconds=high_risk_subscription_refresh_ttl_seconds(),
        now=now,
    )


def subscription_confirmation_fresh(
    settings: Any | None = None,
    *,
    ttl_seconds: int,
    now: datetime | None = None,
) -> bool:
    token, source = _license_token_with_source(settings)
    public_key = _read_public_key()
    if not token or not public_key:
        return _plan_env_override_allowed()
    try:
        revocations, _ = load_revocation_manifest(settings, public_key=public_key)
        license_ = parse_license(token, public_key)
        if _commercial_revocation_freshness_error(license_, revocations, now=now):
            return False
        if revocations is not None and revocations.is_revoked(license_.license_id):
            return False
        if _subscription_license_needs_refresh(license_, now=now, ttl_seconds=ttl_seconds):
            refreshed, _ = _refresh_subscription_license(token, settings, now=now, source=source)
            if refreshed is None:
                return False
            license_ = refreshed
            revocations, _ = load_revocation_manifest(settings, public_key=public_key)
            expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
            if revocations is not None and revocations.is_revoked(license_.license_id):
                return False
            if license_.device_id and (
                not expected_device_id or not hmac.compare_digest(license_.device_id, expected_device_id)
            ):
                return False
            if license_.device_fingerprint and (
                not expected_device_fingerprint
                or not hmac.compare_digest(license_.device_fingerprint, expected_device_fingerprint)
            ):
                return False
            if license_.is_expired(now=now):
                return False
            if license_.subscription_status and license_.subscription_status not in {"active", "trialing"}:
                return False
            return bool(_subscription_confirmation_status(license_, now=now, ttl_seconds=ttl_seconds)["fresh"])
        expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
        verified = verify_license(
            token,
            public_key,
            now=now,
            revocations=revocations,
            expected_device_id=expected_device_id,
            expected_device_fingerprint=expected_device_fingerprint,
        )
        return bool(_subscription_confirmation_status(verified, now=now, ttl_seconds=ttl_seconds)["fresh"])
    except LicenseError:
        return False


def install_license(
    token: str,
    settings: Any,
    *,
    now: datetime | None = None,
    expected_activation_nonce_sha256: str | None = None,
) -> License:
    """Verify and atomically persist a locally imported offline license."""
    if os.getenv(LICENSE_KEY_ENV_VAR, "").strip():
        raise LicenseError(
            "当前许可证由部署配置托管，不能在应用内替换。",
            code="license_managed_externally",
            status_code=409,
        )
    normalized = str(token or "").strip()
    if not normalized:
        raise LicenseError("许可证令牌为空。")
    if len(normalized.encode("utf-8")) > MAX_LICENSE_TOKEN_BYTES:
        raise LicenseError("许可证令牌过大。", code="license_token_too_large", status_code=413)
    public_key = _read_public_key()
    revocations, _ = load_revocation_manifest(settings, public_key=public_key)
    expected_device_id, expected_device_fingerprint = _expected_device_binding(settings)
    license_ = verify_license(
        normalized,
        public_key,
        now=now,
        revocations=revocations,
        expected_device_id=expected_device_id,
        expected_device_fingerprint=expected_device_fingerprint,
    )
    _enforce_commercial_revocation_freshness(license_, revocations, now=now)
    _enforce_subscription_confirmation(license_, now=now)
    if expected_activation_nonce_sha256 is not None:
        require_activation_response_nonce(license_, expected_activation_nonce_sha256)
    path = _license_file_path(settings)
    if path is None:
        raise LicenseError("许可证存储目录不可用。", code="license_storage_unavailable", status_code=503)

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
        raise LicenseError("无法保存许可证。", code="license_storage_failed", status_code=503) from exc
    return license_


def load_license(settings: Any | None = None, *, now: datetime | None = None) -> License | None:
    """Best-effort license load. Returns ``None`` when absent/invalid/expired; never raises."""
    token, source = _license_token_with_source(settings)
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
        license_ = _verify_runtime_license(
            token,
            public_key,
            settings=settings,
            now=now,
            revocations=revocations,
            source=source,
        )
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
    if plan is not None:
        resolved_plan = plan.value
    elif _plan_env_override_allowed():
        return settings
    elif normalize_plan(getattr(settings, "plan", None)) is Plan.FREE:
        return settings
    else:
        resolved_plan = Plan.FREE.value
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
