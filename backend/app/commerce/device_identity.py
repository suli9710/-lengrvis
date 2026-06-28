"""Redacted local device identity used by subscription activation."""

from __future__ import annotations

import json
import platform
import socket
import sys
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.security.local_secret import load_or_create_local_secret

ACTIVATION_INSTALL_SECRET_FILE = "activation_install.secret"  # noqa: S105 - file name, not a secret value.

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
    secret = _activation_install_secret(settings)
    return f"dev_{sha256(secret.encode('utf-8')).hexdigest()[:32]}"


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


def _activation_install_secret(settings: Any) -> str:
    data_dir = Path(str(getattr(settings, "data_dir", "") or "")).expanduser()
    if not data_dir:
        raise DeviceIdentityError(
            "Activation storage directory is unavailable.",
            code="activation_storage_unavailable",
            status_code=503,
        )
    return load_or_create_local_secret(
        data_dir / ACTIVATION_INSTALL_SECRET_FILE,
        unavailable_message="Activation install secret is unavailable.",
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
