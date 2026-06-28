from __future__ import annotations

from urllib.parse import urlparse

from fastapi import WebSocket

from app.config import env_flag, get_env
from app.security.lan import is_loopback_host, normalize_host_for_security

TRUSTED_WEBSOCKET_ORIGINS_ENV = "LENGRVIS_TRUSTED_WEBSOCKET_ORIGINS"
STRICT_WEBSOCKET_ORIGIN_ENV = "LENGRVIS_STRICT_WEBSOCKET_ORIGIN"
_DEFAULT_TRUSTED_LOOPBACK_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}


def is_trusted_websocket_origin(websocket: WebSocket, *, allow_missing_origin_with_token: bool = False) -> bool:
    origin = str(websocket.headers.get("origin") or "").strip()
    if not origin:
        if not strict_websocket_origin_enabled():
            return True
        return bool(allow_missing_origin_with_token)
    if origin.lower() == "null":
        return False
    if origin in _configured_trusted_origins():
        return True
    parsed = urlparse(origin)
    scheme = parsed.scheme.lower()
    if scheme == "app" and parsed.hostname == "local":
        return True
    if scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if _same_http_origin_as_websocket(parsed, websocket):
        return True
    return origin in _DEFAULT_TRUSTED_LOOPBACK_ORIGINS


def strict_websocket_origin_enabled() -> bool:
    commercial = str(get_env("LENGRVIS_COMMERCIAL_RELEASE") or "").strip().lower()
    return env_flag(STRICT_WEBSOCKET_ORIGIN_ENV) or commercial in {"1", "true", "yes", "on"}


def _same_http_origin_as_websocket(origin, websocket: WebSocket) -> bool:
    ws_scheme = str(websocket.url.scheme or "").lower()
    expected_scheme = "https" if ws_scheme == "wss" else "http"
    if origin.scheme.lower() != expected_scheme:
        return False
    origin_host = normalize_host_for_security(origin.hostname)
    request_host = normalize_host_for_security(websocket.url.hostname)
    if origin_host != request_host:
        return False
    return _origin_port(origin) == _websocket_http_port(websocket)


def _origin_port(origin) -> int:
    if origin.port:
        return int(origin.port)
    return 443 if origin.scheme.lower() == "https" else 80


def _websocket_http_port(websocket: WebSocket) -> int:
    port = websocket.url.port
    if port:
        return int(port)
    return 443 if websocket.url.scheme == "wss" else 80


def _configured_trusted_origins() -> set[str]:
    origins = set(_DEFAULT_TRUSTED_LOOPBACK_ORIGINS)
    raw = str(get_env(TRUSTED_WEBSOCKET_ORIGINS_ENV) or "")
    for item in raw.replace(";", ",").split(","):
        origin = _normalized_origin(item)
        if origin:
            origins.add(origin)
    vite = str(get_env("VITE_DEV_SERVER_URL") or "").strip()
    origin = _normalized_origin(vite)
    if origin:
        origins.add(origin)
    return origins


def _normalized_origin(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme == "app" and parsed.hostname == "local":
        return "app://local"
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if not (is_loopback_host(host) or parsed.hostname in {"localhost", "127.0.0.1"}):
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{host}{port}"
