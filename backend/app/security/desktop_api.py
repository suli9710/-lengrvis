from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

from fastapi import Request, WebSocket, WebSocketException, status

from app.config import get_base_settings
from app.security.lan import is_loopback_host


DESKTOP_API_TOKEN_HEADER = "x-mavris-desktop-token"
DESKTOP_API_TOKEN_FILE = "desktop_api.secret"
DESKTOP_API_WS_PROTOCOL_PREFIX = "mavris.desktop.token."
logger = logging.getLogger(__name__)


def desktop_api_token() -> str:
    configured = (os.environ.get("MAVRIS_DESKTOP_API_TOKEN") or os.environ.get("MARVIS_DESKTOP_API_TOKEN") or "").strip()
    if configured:
        return configured
    return _local_desktop_api_token()


def should_require_desktop_api_token(request: Request) -> bool:
    if _desktop_api_token_optional():
        return False
    if _is_desktop_api_token_exempt_path(request.url.path):
        return False
    client_host = request.client.host if request.client else ""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"} and is_loopback_host(client_host):
        return False
    return True


def has_valid_desktop_api_token(request: Request) -> bool:
    expected = desktop_api_token()
    supplied = request.headers.get(DESKTOP_API_TOKEN_HEADER, "").strip()
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def has_valid_desktop_websocket_token(websocket: WebSocket) -> bool:
    expected = desktop_api_token()
    for supplied in _desktop_websocket_tokens(websocket):
        if expected and supplied and hmac.compare_digest(supplied, expected):
            return True
    return False


def is_authorized_desktop_websocket(websocket: WebSocket) -> bool:
    client_host = websocket.client.host if websocket.client else ""
    if not is_loopback_host(client_host):
        return has_valid_desktop_websocket_token(websocket)
    if _desktop_api_token_optional():
        return True
    return has_valid_desktop_websocket_token(websocket)


async def close_unauthorized_desktop_websocket(websocket: WebSocket) -> bool:
    if is_authorized_desktop_websocket(websocket):
        return False
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)


def _is_desktop_api_token_exempt_path(path: str) -> bool:
    if path in {"/health", "/api/health", "/api/pair", "/api/pair/confirm"}:
        return True
    if path.startswith("/api/mobile/"):
        return True
    return False


def _desktop_api_token_optional() -> bool:
    raw = os.environ.get("MAVRIS_DESKTOP_API_TOKEN_OPTIONAL") or os.environ.get("MARVIS_DESKTOP_API_TOKEN_OPTIONAL")
    if str(raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if _is_test_environment():
        return True
    logger.warning("Ignoring desktop API token optional mode outside test environment")
    return False


def desktop_api_token_optional_for_test() -> bool:
    return _desktop_api_token_optional()


def _is_test_environment() -> bool:
    return any(
        str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on", "test", "testing"}
        for name in ("MAVRIS_TEST", "MARVIS_TEST", "PYTEST_CURRENT_TEST", "APP_ENV", "MAVRIS_ENV", "MARVIS_ENV")
    )


def _desktop_websocket_tokens(websocket: WebSocket) -> list[str]:
    values: list[str] = []
    supplied_header = websocket.headers.get(DESKTOP_API_TOKEN_HEADER, "").strip()
    if supplied_header:
        values.append(supplied_header)
    for protocol in _websocket_protocols(websocket):
        if protocol.startswith(DESKTOP_API_WS_PROTOCOL_PREFIX):
            values.append(protocol[len(DESKTOP_API_WS_PROTOCOL_PREFIX) :])
    return values


def _websocket_protocols(websocket: WebSocket) -> list[str]:
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _local_desktop_api_token() -> str:
    try:
        data_dir = Path(get_base_settings().data_dir)
        secret_path = data_dir / DESKTOP_API_TOKEN_FILE
        if secret_path.exists():
            value = secret_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        secret_path.write_text(value, encoding="utf-8")
        try:
            secret_path.chmod(0o600)
        except OSError as exc:
            logger.debug("could not restrict desktop API token permissions at %s: %s", secret_path, exc)
        return value
    except OSError as exc:
        logger.warning("desktop API token is unavailable; state-changing desktop APIs will reject requests: %s", exc)
        return ""
