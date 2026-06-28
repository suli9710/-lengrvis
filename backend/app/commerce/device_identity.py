"""Redacted local device identity used by subscription activation."""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.security.local_secret import (
    LOCAL_SECRET_DPAPI_PREFIX,
    LOCAL_SECRET_KEYRING_PREFIX,
    load_or_create_local_secret,
)

ACTIVATION_INSTALL_SECRET_FILE = "activation_install.secret"  # noqa: S105 - file name, not a secret value.

COMMERCIAL_RELEASE_ENV_VAR = "LENGRVIS_COMMERCIAL_RELEASE"
_TRUE_VALUES = {"1", "true", "yes", "on"}

_HASH_PREFIX = "lengrvis-device-v1:"


class DeviceIdentityError(AppError):
    """Raised when the local device identity cannot be collected."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "activation_device_identity_unavailable",
        status_code: int = 503,
    ) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@dataclass(frozen=True)
class LocalDeviceIdentity:
    device_id: str
    fingerprint: str
    profile: dict[str, Any]


def local_activation_device_id(settings: Any) -> str:
    """Return a stable install-bound id without exposing local host details."""
    return collect_activation_device_identity(settings).device_id


def local_activation_device_fingerprint(settings: Any) -> str:
    """Return a stable machine-bound fingerprint for license verification."""
    return collect_activation_device_identity(settings).fingerprint


def collect_activation_device_identity(settings: Any) -> LocalDeviceIdentity:
    """Collect a redacted device fingerprint for online activation.

    Raw machine ids, hostnames, MAC addresses, paths, and IP addresses are never
    returned. The server receives hashes and coarse platform labels only.
    """
    secret = _activation_install_secret(settings)
    device_id = f"dev_{sha256(secret.encode('utf-8')).hexdigest()[:32]}"
    signals: dict[str, str] = {}
    machine_id = _read_machine_id()
    if machine_id:
        signals["machine_id_hash"] = _digest_signal(machine_id)
    hostname = _safe_hostname()
    if hostname:
        signals["hostname_hash"] = _digest_signal(hostname.lower())
    node = _safe_node_id()
    if node:
        signals["node_hash"] = _digest_signal(node)
    install_hash = _digest_signal(secret)

    fingerprint_inputs = {
        key: signals[key]
        for key in ("machine_id_hash", "node_hash", "hostname_hash")
        if key in signals
    }
    if not fingerprint_inputs:
        if _commercial_release_enabled():
            raise DeviceIdentityError(
                "Commercial release requires a hardware-backed device fingerprint.",
                code="activation_device_fingerprint_weak",
                status_code=503,
            )
        fingerprint_inputs["install_hash"] = install_hash
    fingerprint_inputs["os"] = _safe_label(platform.system().lower() or sys.platform, max_length=24)
    fingerprint_inputs["arch"] = _safe_label(platform.machine().lower(), max_length=32)
    fingerprint_body = json.dumps(fingerprint_inputs, sort_keys=True, separators=(",", ":"))
    fingerprint = f"fp_{sha256(fingerprint_body.encode('utf-8')).hexdigest()[:48]}"

    profile: dict[str, Any] = {
        "schema": 1,
        "fingerprint_version": "local-v1",
        "fingerprint": fingerprint,
        "os": fingerprint_inputs["os"],
        "arch": fingerprint_inputs["arch"],
        "os_release": _safe_label(platform.release(), max_length=32),
        "signal_count": len(signals),
        "signals": sorted(signals.keys()),
        "install_hash": install_hash,
    }
    profile.update(signals)
    return LocalDeviceIdentity(device_id=device_id, fingerprint=fingerprint, profile=profile)


def activation_install_secret_path(settings: Any) -> Path:
    data_dir = Path(str(getattr(settings, "data_dir", "") or "")).expanduser()
    if not data_dir:
        raise DeviceIdentityError(
            "激活存储目录不可用。",
            code="activation_storage_unavailable",
            status_code=503,
        )
    return data_dir / ACTIVATION_INSTALL_SECRET_FILE


def _activation_install_secret(settings: Any) -> str:
    path = activation_install_secret_path(settings)
    secret = load_or_create_local_secret(
        path,
        unavailable_message="激活安装密钥不可用。",
    )
    _assert_restrictive_secret_file_permissions(path)
    return secret


def _assert_restrictive_secret_file_permissions(path: Path) -> None:
    """Reject world-readable activation secrets and commercial plaintext storage."""
    if not path.exists():
        return
    if os.name == "nt":
        if _commercial_release_enabled():
            stored = path.read_text(encoding="utf-8").strip()
            if stored and not (
                stored.startswith(LOCAL_SECRET_DPAPI_PREFIX) or stored.startswith(LOCAL_SECRET_KEYRING_PREFIX)
            ):
                raise DeviceIdentityError(
                    "商业发行版要求激活安装密钥使用 DPAPI 或系统密钥环存储。",
                    code="activation_secret_insecure_permissions",
                    status_code=403,
                )
        return
    mode = path.stat().st_mode
    if mode & 0o077:
        raise DeviceIdentityError(
            "激活安装密钥文件权限过宽。",
            code="activation_secret_insecure_permissions",
            status_code=403,
        )


def _digest_signal(value: str) -> str:
    return sha256(f"{_HASH_PREFIX}{value}".encode()).hexdigest()


def _read_machine_id() -> str:
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore[import-not-found]

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return _safe_label(value, max_length=256)
        except OSError:
            return ""
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return _safe_label(value, max_length=256)
    return ""


def _safe_hostname() -> str:
    try:
        return _safe_label(socket.gethostname(), max_length=256)
    except OSError:
        return ""


def _safe_node_id() -> str:
    node = uuid.getnode()
    if not node:
        return ""
    # uuid.getnode can return a random multicast value when no hardware id is
    # available. Do not use that as a stable fingerprint signal.
    if node & (1 << 40):
        return ""
    return f"{node:012x}"


def _safe_label(value: Any, *, max_length: int) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _commercial_release_enabled() -> bool:
    return str(os.getenv(COMMERCIAL_RELEASE_ENV_VAR, "")).strip().lower() in _TRUE_VALUES
