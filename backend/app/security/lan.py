from __future__ import annotations

import ipaddress
import os


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return True
    normalized = host.strip().lower()
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def allow_lan_desktop_api() -> bool:
    return (os.environ.get("MARVIS_ALLOW_LAN_DESKTOP_API") or os.environ.get("MAVRIS_ALLOW_LAN_DESKTOP_API") or "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_mobile_lan_http_path(path: str) -> bool:
    return (
        path in {"/health", "/api/health", "/api/pair", "/api/pair/request", "/api/pair/confirm"}
        or path.startswith("/api/mobile/")
        or path.startswith("/ws/mobile/")
        or path.startswith("/api/ws/mobile/")
        or path.startswith("/ws/remote/")
        or path.startswith("/api/ws/remote/")
    )
