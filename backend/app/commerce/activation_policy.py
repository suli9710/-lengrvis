"""Validation and derivation policy for subscription activation requests."""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from app.core.errors import AppError

ACTIVATION_KEY_PEPPER_ENV_VAR = "LENGRVIS_ACTIVATION_KEY_PEPPER"
ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR = "LENGRVIS_ACTIVATION_SERVER_DEVICE_SECRET"  # noqa: S105
ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR = "LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF"

MAX_ACTIVATION_KEY_CHARS = 256
MAX_DEVICE_ID_CHARS = 128
MAX_DEVICE_FINGERPRINT_CHARS = 128
MIN_DEVICE_FINGERPRINT_CHARS = 4
MAX_DEVICE_PROFILE_JSON_CHARS = 2048
MAX_APP_VERSION_CHARS = 64
MAX_NONCE_CHARS = 128
MIN_ACTIVATION_NONCE_CHARS = 16

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_DEVICE_PROFILE_KEYS = {
    "schema",
    "fingerprint_version",
    "fingerprint",
    "os",
    "arch",
    "os_release",
    "signal_count",
    "hardware_signal_count",
    "signals",
    "install_hash",
    "machine_id_hash",
    "hostname_hash",
    "node_hash",
    "secret_storage",
    "binding_strength",
}
_ACTIVATION_ERROR_MESSAGES = {
    "activation_failed": "激活失败。",
    "activation_service_unavailable": "激活服务暂时不可用，请稍后重试。",
    "activation_malformed_response": "激活服务返回的数据不完整。",
    "activation_unconfigured": "尚未配置激活服务器。",
    "activation_url_invalid": "激活服务器地址无效。",
    "activation_https_required": "激活服务器必须使用 HTTPS。",
    "activation_server_unconfigured": "激活服务器配置不完整。",
    "activation_storage_unavailable": "激活存储目录不可用。",
    "activation_device_identity_unavailable": "暂时无法读取本机设备身份。",
    "activation_key_required": "请输入订阅授权码。",
    "activation_key_invalid": "订阅授权码无效。",
    "activation_key_not_found": "订阅授权码不存在或已失效。",
    "activation_rate_limited": "激活尝试次数过多，请稍后再试。",
    "activation_device_required": "设备标识不能为空。",
    "activation_device_invalid": "设备标识无效。",
    "activation_device_limit": "已达到该订阅允许绑定的设备数量。",
    "activation_device_not_found": "未找到该激活设备。",
    "activation_device_mismatch": "设备与该许可证不匹配。",
    "activation_device_rebind_requires_unbind": "该设备指纹已绑定到其他激活记录，请先在后台解绑旧设备。",
    "activation_device_fingerprint_invalid": "设备指纹无效。",
    "activation_device_fingerprint_mismatch": "设备指纹与本次激活记录不一致。",
    "activation_device_profile_mismatch": "设备证明与设备指纹不一致。",
    "activation_fingerprint_required": "新设备激活必须提交设备指纹。",
    "activation_device_proof_weak": "设备绑定证明强度不足。",
    "subscription_delete_not_terminal": "只能删除已取消、已过期或已撤销且不再可激活的订阅记录。",
    "subscription_delete_has_devices": "该订阅仍有设备绑定，请先撤销并处理吊销清单或解绑设备后再删除记录。",
    "activation_nonce_required": "激活请求缺少安全随机数。",
    "activation_nonce_invalid": "激活请求安全随机数无效。",
    "activation_nonce_mismatch": "激活服务返回的许可证不是本次请求的结果。",
    "license_token_required": "许可证令牌不能为空。",
    "license_public_key_missing": "当前构建未配置许可证验签公钥。",
    "license_id_required": "许可证编号不能为空。",
    "license_device_mismatch": "许可证绑定到另一台设备。",
    "subscription_required": "该许可证不是订阅许可证。",
    "subscription_mismatch": "许可证订阅与激活记录不一致。",
    "subscription_active": "订阅当前可用。",
    "subscription_trialing": "订阅当前处于试用期。",
    "subscription_past_due": "订阅已逾期，请处理付款后重试。",
    "subscription_canceled": "订阅已取消。",
    "subscription_expired": "订阅已过期。",
    "subscription_revoked": "订阅已被撤销。",
}


class JsonResponse(Protocol):
    def json(self) -> Any: ...


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
class ActivationRefreshRequest:
    license_token: str
    device_id: str
    app_version: str = ""
    nonce: str = ""
    device_fingerprint: str = ""
    device_profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActivationDevice:
    device_id: str
    fingerprint: str
    profile_json: str

    def binding_claim(self) -> dict[str, Any]:
        return device_binding_claim(self.profile_json, device_fingerprint=self.fingerprint)


@dataclass(frozen=True)
class PreparedActivation:
    activation_key: str
    key_hash: str
    device: ActivationDevice
    app_version: str
    nonce: str
    server_device_ref: str

    @property
    def license_id(self) -> str:
        return license_id_for_device_ref(self.key_hash, self.server_device_ref)

    def require_new_device_fingerprint(self) -> None:
        if not self.device.fingerprint:
            raise _error("activation_fingerprint_required", status_code=422)
        if len(self.device.fingerprint) < MIN_DEVICE_FINGERPRINT_CHARS:
            raise _error("activation_device_fingerprint_invalid", status_code=422)

    def ensure_existing_device_matches(self, *, fingerprint: str, server_device_ref: str) -> None:
        if fingerprint and self.device.fingerprint and not hmac.compare_digest(fingerprint, self.device.fingerprint):
            raise _error("activation_device_fingerprint_mismatch", status_code=409)
        if server_device_ref and not hmac.compare_digest(server_device_ref, self.server_device_ref):
            raise _error("activation_device_fingerprint_mismatch", status_code=409)


@dataclass(frozen=True)
class ResolvedDeviceBinding:
    fingerprint: str
    server_device_ref: str


@dataclass(frozen=True)
class PreparedRefresh:
    device: ActivationDevice
    app_version: str
    nonce: str
    _device_secret: str = field(repr=False)

    def resolve_binding(
        self,
        *,
        key_hash: str,
        stored_fingerprint: str,
        stored_server_device_ref: str,
        license_fingerprint: str,
    ) -> ResolvedDeviceBinding:
        if (
            stored_fingerprint
            and self.device.fingerprint
            and not hmac.compare_digest(stored_fingerprint, self.device.fingerprint)
        ):
            raise _error("activation_device_fingerprint_mismatch", status_code=409)
        next_fingerprint = self.device.fingerprint or stored_fingerprint or license_fingerprint
        next_server_device_ref = (
            _server_device_ref(
                key_hash=key_hash,
                device_fingerprint=next_fingerprint,
                device_secret=self._device_secret,
            )
            if next_fingerprint
            else stored_server_device_ref
        )
        if (
            stored_server_device_ref
            and next_server_device_ref
            and not hmac.compare_digest(stored_server_device_ref, next_server_device_ref)
        ):
            raise _error("activation_device_fingerprint_mismatch", status_code=409)
        return ResolvedDeviceBinding(
            fingerprint=next_fingerprint,
            server_device_ref=next_server_device_ref,
        )


@dataclass(frozen=True)
class ActivationPolicy:
    """Prepare trustworthy activation inputs from untrusted request data."""

    key_pepper: str = field(default="", repr=False)
    device_secret: str = field(default="", repr=False)
    require_strong_device_proof: bool = False

    @classmethod
    def from_environment(cls) -> ActivationPolicy:
        return cls(
            key_pepper=str(os.getenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "")).strip(),
            device_secret=str(os.getenv(ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, "")).strip(),
            require_strong_device_proof=(
                str(os.getenv(ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR, "")).strip().lower() in _TRUE_VALUES
            ),
        )

    def prepare_activation(self, request: ActivationRequest) -> PreparedActivation:
        activation_key = clean_activation_key(request.activation_key)
        device = self._prepare_device(
            device_id=request.device_id,
            device_fingerprint=request.device_fingerprint,
            device_profile=request.device_profile,
        )
        app_version = safe_label(request.app_version, max_length=MAX_APP_VERSION_CHARS)
        nonce = _clean_activation_nonce(request.nonce)
        key_hash = self.hash_activation_key(activation_key)
        server_device_ref = self.server_device_ref(
            key_hash=key_hash,
            device_fingerprint=device.fingerprint,
        )
        return PreparedActivation(
            activation_key=activation_key,
            key_hash=key_hash,
            device=device,
            app_version=app_version,
            nonce=nonce,
            server_device_ref=server_device_ref,
        )

    def prepare_refresh(
        self,
        request: ActivationRefreshRequest,
        *,
        expected_device_id: str = "",
    ) -> PreparedRefresh:
        device_id = _clean_device_id(request.device_id)
        if expected_device_id and not hmac.compare_digest(expected_device_id, device_id):
            raise _error("license_device_mismatch", status_code=402)
        return PreparedRefresh(
            device=self._prepare_device(
                device_id=device_id,
                device_fingerprint=request.device_fingerprint,
                device_profile=request.device_profile,
            ),
            app_version=safe_label(request.app_version, max_length=MAX_APP_VERSION_CHARS),
            nonce=_clean_activation_nonce(request.nonce),
            _device_secret=self.device_secret,
        )

    def hash_activation_key(self, activation_key: str) -> str:
        key = clean_activation_key(activation_key)
        secret = str(self.key_pepper or "").strip()
        if not secret:
            raise _error("activation_server_unconfigured", status_code=503)
        return hmac.new(secret.encode("utf-8"), key.encode("utf-8"), sha256).hexdigest()

    def server_device_ref(
        self,
        *,
        key_hash: str,
        device_fingerprint: str,
        legacy_device_id: str = "",
    ) -> str:
        return _server_device_ref(
            key_hash=key_hash,
            device_fingerprint=device_fingerprint,
            legacy_device_id=legacy_device_id,
            device_secret=self.device_secret,
        )

    def _prepare_device(
        self,
        *,
        device_id: str,
        device_fingerprint: str,
        device_profile: Mapping[str, Any] | None,
    ) -> ActivationDevice:
        normalized_device_id = _clean_device_id(device_id)
        normalized_fingerprint = _clean_device_fingerprint(device_fingerprint)
        profile = safe_device_profile(device_profile if isinstance(device_profile, Mapping) else {})
        _enforce_device_profile_consistency(profile, device_fingerprint=normalized_fingerprint)
        if self.require_strong_device_proof:
            _enforce_strong_device_proof(profile)
        return ActivationDevice(
            device_id=normalized_device_id,
            fingerprint=normalized_fingerprint,
            profile_json=json.dumps(profile, sort_keys=True, separators=(",", ":")),
        )


def hash_activation_key(activation_key: str, *, pepper: str | None = None) -> str:
    configured_pepper = pepper if pepper is not None else os.getenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "")
    return ActivationPolicy(key_pepper=str(configured_pepper or "").strip()).hash_activation_key(activation_key)


def clean_activation_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise _error("activation_key_required", status_code=422)
    if len(text) > MAX_ACTIVATION_KEY_CHARS:
        raise _error("activation_key_invalid", status_code=422)
    return text


def safe_device_profile(value: Mapping[str, Any]) -> dict[str, Any]:
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
            result[name] = [safe_label(item, max_length=64) for item in raw[:16]]
        else:
            result[name] = safe_label(raw, max_length=128)
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_DEVICE_PROFILE_JSON_CHARS:
        return {
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
    return result


def decode_device_profile(value: Any) -> dict[str, Any]:
    try:
        profile = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(profile, dict):
        return {}
    return safe_device_profile(profile)


def device_binding_claim(device_profile: str, *, device_fingerprint: str) -> dict[str, Any]:
    profile = decode_device_profile(device_profile)
    try:
        hardware_signal_count = int(profile.get("hardware_signal_count") or 0)
    except (TypeError, ValueError):
        hardware_signal_count = 0
    return {
        "strength": safe_label(profile.get("binding_strength"), max_length=32),
        "secret_storage": safe_label(profile.get("secret_storage"), max_length=32),
        "hardware_signal_count": max(0, hardware_signal_count),
        "fingerprint": safe_label(device_fingerprint, max_length=MAX_DEVICE_FINGERPRINT_CHARS),
    }


def license_id_for_device_ref(key_hash: str, server_device_ref: str) -> str:
    digest = sha256(f"{key_hash}:{server_device_ref}".encode()).hexdigest()[:24]
    return f"lic_{digest}"


def activation_error_code(response: JsonResponse) -> str:
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


def activation_error_message(response: JsonResponse) -> str:
    code = activation_error_code(response)
    mapped = activation_message_for_code(code)
    if mapped:
        return mapped
    try:
        data = response.json()
    except ValueError:
        return activation_message_for_code("activation_failed")
    fallback = activation_message_for_code("activation_failed")
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return _safe_message(str(error["message"]), fallback=fallback)
        detail = data.get("detail")
        if isinstance(detail, dict) and detail.get("message"):
            return _safe_message(str(detail["message"]), fallback=fallback)
        if isinstance(detail, str) and detail:
            return _safe_message(detail, fallback=fallback)
    return fallback


def activation_message_for_code(code: str, fallback: str = "激活失败。") -> str:
    return _ACTIVATION_ERROR_MESSAGES.get(str(code or "").strip(), fallback)


def safe_label(value: Any, *, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _clean_device_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise _error("activation_device_required", status_code=422)
    if len(text) > MAX_DEVICE_ID_CHARS or any(char.isspace() for char in text):
        raise _error("activation_device_invalid", status_code=422)
    return text


def _clean_device_fingerprint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-:.")
    if len(text) > MAX_DEVICE_FINGERPRINT_CHARS or any(char not in allowed for char in text):
        raise _error("activation_device_fingerprint_invalid", status_code=422)
    return text


def _clean_activation_nonce(value: str) -> str:
    text = safe_label(value, max_length=MAX_NONCE_CHARS)
    if not text:
        raise _error("activation_nonce_required", status_code=422)
    if len(text) < MIN_ACTIVATION_NONCE_CHARS or any(char.isspace() for char in text):
        raise _error("activation_nonce_invalid", status_code=422)
    return text


def _enforce_device_profile_consistency(profile: Mapping[str, Any], *, device_fingerprint: str) -> None:
    if not profile or str(profile.get("binding_strength") or "").strip().lower() != "strong":
        return
    reported_fingerprint = str(profile.get("fingerprint") or "").strip()
    if (
        not device_fingerprint
        or not reported_fingerprint
        or not hmac.compare_digest(reported_fingerprint, device_fingerprint)
        or not str(profile.get("install_hash") or "").strip()
    ):
        raise _error("activation_device_profile_mismatch", status_code=422)
    try:
        hardware_signal_count = int(profile.get("hardware_signal_count") or 0)
    except (TypeError, ValueError):
        hardware_signal_count = 0
    signals_raw = profile.get("signals")
    signals = (
        {str(item or "").strip() for item in signals_raw if str(item or "").strip()}
        if isinstance(signals_raw, list)
        else set()
    )
    present_hardware_signals = {
        name for name in {"machine_id_hash", "node_hash"} if str(profile.get(name) or "").strip()
    }
    if signals:
        present_hardware_signals &= signals
    if hardware_signal_count > len(present_hardware_signals):
        raise _error("activation_device_profile_mismatch", status_code=422)


def _enforce_strong_device_proof(profile: Mapping[str, Any]) -> None:
    strength = str(profile.get("binding_strength") or "").strip().lower()
    storage = str(profile.get("secret_storage") or "").strip().lower()
    try:
        hardware_signal_count = int(profile.get("hardware_signal_count") or 0)
    except (TypeError, ValueError):
        hardware_signal_count = 0
    if strength != "strong" or storage not in {"dpapi", "keyring"} or hardware_signal_count < 1:
        raise _error("activation_device_proof_weak", status_code=422)


def _server_device_ref(
    *,
    key_hash: str,
    device_fingerprint: str,
    device_secret: str,
    legacy_device_id: str = "",
) -> str:
    fingerprint = str(device_fingerprint or "").strip()
    if not fingerprint and not legacy_device_id:
        raise _error("activation_fingerprint_required", status_code=422)
    secret = str(device_secret or "").strip()
    if not secret:
        raise _error("activation_server_unconfigured", status_code=503)
    subject = fingerprint if fingerprint else f"legacy-device-id:{legacy_device_id}"
    digest = hmac.new(secret.encode("utf-8"), f"{key_hash}\n{subject}".encode(), sha256).hexdigest()
    return f"sdev_{digest[:48]}"


def _safe_message(message: str, *, fallback: str) -> str:
    text = str(message or "").strip()
    if not text:
        return fallback
    return text if any("\u4e00" <= char <= "\u9fff" for char in text) else fallback


def _error(code: str, *, status_code: int) -> ActivationError:
    return ActivationError(activation_message_for_code(code), code=code, status_code=status_code)
