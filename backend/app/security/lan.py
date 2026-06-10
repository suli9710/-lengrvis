from __future__ import annotations

import ipaddress
import os

LAN_PUBLIC_HTTP_PATHS = {"/health", "/api/health"}
MOBILE_TOKEN_HTTP_PATHS = {"/api/pair", "/api/pair/confirm"}
MOBILE_SECURE_TRANSPORT_ERROR = "Remote mobile pairing and mobile APIs require HTTPS/WSS unless the client is on this computer."


def is_loopback_host(host: str | None) -> bool:
    # Fail closed: an absent/unknown client host must never be treated as local.
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized[1:normalized.index("]")]
    elif normalized.count(":") == 1:
        normalized = normalized.rsplit(":", 1)[0]
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def allow_lan_desktop_api() -> bool:
    return (os.environ.get("LENGRVIS_ALLOW_LAN_DESKTOP_API") or "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_mobile_lan_http_path(path: str) -> bool:
    return is_public_lan_http_path(path) or is_mobile_token_http_path(path)


def is_public_lan_http_path(path: str) -> bool:
    return path in LAN_PUBLIC_HTTP_PATHS


def is_mobile_token_http_path(path: str) -> bool:
    return (
        path in MOBILE_TOKEN_HTTP_PATHS
        or path.startswith("/api/mobile/")
    )


def is_mobile_token_websocket_path(path: str) -> bool:
    normalized = "/" + str(path or "").lstrip("/")
    return (
        normalized == "/ws/mobile"
        or normalized.startswith("/ws/mobile/")
        or normalized == "/api/ws/mobile"
        or normalized.startswith("/api/ws/mobile/")
        or normalized == "/ws/remote"
        or normalized.startswith("/ws/remote/")
        or normalized == "/api/ws/remote"
        or normalized.startswith("/api/ws/remote/")
    )


def is_secure_transport_scheme(scheme: str | None) -> bool:
    return str(scheme or "").strip().lower().rstrip(":") in {"https", "wss"}


def is_secure_mobile_transport(client_host: str | None, scheme: str | None) -> bool:
    return is_loopback_host(client_host) or is_secure_transport_scheme(scheme)
