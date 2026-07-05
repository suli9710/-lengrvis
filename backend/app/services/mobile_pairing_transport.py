from __future__ import annotations

import socket
import ssl
from pathlib import Path
from typing import Any

from app.config import AppSettings, get_env
from app.llm.registry import get_effective_settings
from app.security.lan_tls import certificate_fingerprint_sha256


def _server_info(transport: dict[str, Any] | None = None) -> dict[str, Any]:
    transport = transport or lan_transport_security()
    return {
        "host": _lan_ip(),
        "port": _backend_port(),
        "scheme": transport["scheme"],
        "origin": transport["origin"],
        "transport_security": transport,
    }


def lan_transport_security(settings: AppSettings | None = None) -> dict[str, Any]:
    settings = settings or get_effective_settings()
    https_enabled = bool(getattr(settings, "lan_tls_enabled", False))
    cert_file = str(getattr(settings, "lan_tls_cert_file", "") or "").strip()
    key_file = str(getattr(settings, "lan_tls_key_file", "") or "").strip()
    cert_present = bool(cert_file) and Path(cert_file).expanduser().exists()
    key_present = bool(key_file) and Path(key_file).expanduser().exists()
    tls_validation = (
        _validate_lan_tls_material(cert_file, key_file)
        if https_enabled and cert_present and key_present
        else {"ok": False, "error": "", "fingerprint_sha256": ""}
    )
    tls_ready = https_enabled and cert_present and key_present and bool(tls_validation["ok"])
    scheme = "https" if https_enabled else "http"
    origin = _configured_lan_origin(settings, scheme)

    if tls_ready:
        status = "https_ready"
        warning = ""
        next_action = "Pair mobile devices with the HTTPS address and trust the local certificate when prompted."
    elif https_enabled:
        status = "https_misconfigured"
        missing = []
        if not cert_present:
            missing.append("certificate file")
        if not key_present:
            missing.append("private key file")
        if missing:
            warning = f"LAN HTTPS is enabled but the {' and '.join(missing)} is missing."
            next_action = "Create or point Lengrvis at a local TLS certificate and key, then restart the backend."
        else:
            warning = f"LAN HTTPS certificate/key validation failed: {tls_validation['error']}"
            next_action = (
                "Point Lengrvis at a parseable certificate and matching private key, then restart the backend."
            )
    else:
        status = "http_lan_insecure"
        warning = "LAN mobile pairing uses HTTP/ws transport unless HTTPS is explicitly configured."
        next_action = (
            "Use loopback for local testing, or configure LAN TLS before pairing phones on an untrusted network."
        )

    return {
        "status": status,
        "scheme": scheme,
        "origin": origin,
        "https_enabled": https_enabled,
        "tls_ready": tls_ready,
        "cert_configured": bool(cert_file),
        "key_configured": bool(key_file),
        "cert_present": cert_present,
        "key_present": key_present,
        "tls_material_valid": bool(tls_validation["ok"]),
        "requires_trust": https_enabled,
        "trust_required": https_enabled,
        "trust_model": "local_certificate" if https_enabled else "none",
        "fingerprint_sha256": str(tls_validation.get("fingerprint_sha256") or ""),
        "certificate_fingerprint_sha256": str(tls_validation.get("fingerprint_sha256") or ""),
        "warning": warning,
        "next_action": next_action,
    }


def _validate_lan_tls_material(cert_file: str, key_file: str) -> dict[str, Any]:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_path = Path(cert_file).expanduser()
        context.load_cert_chain(str(cert_path), str(Path(key_file).expanduser()))
        fingerprint = _certificate_fingerprint_sha256(cert_path)
    except Exception as exc:  # noqa: BLE001 - readiness should report a structured status. broad-exception-boundary
        return {"ok": False, "error": _safe_tls_error(exc), "fingerprint_sha256": ""}
    return {"ok": True, "error": "", "fingerprint_sha256": fingerprint}


def _certificate_fingerprint_sha256(cert_path: Path) -> str:
    return certificate_fingerprint_sha256(cert_path)


def _safe_tls_error(error: Exception) -> str:
    if isinstance(error, ssl.SSLError):
        return "certificate or private key could not be parsed or do not match"
    if isinstance(error, OSError):
        return "certificate or private key file could not be opened"
    return "certificate or private key validation failed"


def _configured_lan_origin(settings: AppSettings, scheme: str) -> str:
    configured = str(getattr(settings, "lan_public_base_url", "") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"{scheme}://{_lan_ip()}:{_backend_port()}"


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _backend_port() -> int:
    return int(get_env("LENGRVIS_BACKEND_PORT") or "8000")


def _iso(value: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, UTC).isoformat()


def _parse_iso(value: str) -> float:
    from datetime import datetime

    if not value:
        return 0.0
    return datetime.fromisoformat(value).timestamp()
