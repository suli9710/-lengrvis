from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from fastapi import Request, WebSocket, WebSocketException, status

from app.config import get_base_settings, get_env
from app.security.lan import is_loopback_host


DESKTOP_API_TOKEN_HEADER = "x-lengrvis-desktop-token"
DESKTOP_API_TOKEN_FILE = "desktop_api.secret"
DESKTOP_API_WS_PROTOCOL_PREFIX = "lengrvis.desktop.token."
DESKTOP_SIGNED_RESOURCE_EXPIRES_QUERY = "expires"
DESKTOP_SIGNED_RESOURCE_SIGNATURE_QUERY = "signature"
DESKTOP_SIGNED_RESOURCE_MAX_TTL_SECONDS = 10 * 60
DESKTOP_SIGNED_RESOURCE_PATHS = {"/api/library/preview"}
logger = logging.getLogger(__name__)


def desktop_api_token() -> str:
    configured = str(get_env("LENGRVIS_DESKTOP_API_TOKEN") or "").strip()
    if configured:
        return configured
    return _local_desktop_api_token()


def desktop_api_token_headers() -> dict[str, str]:
    token = desktop_api_token()
    return {DESKTOP_API_TOKEN_HEADER: token} if token else {}


def should_require_desktop_api_token(request: Request) -> bool:
    if _desktop_api_token_optional():
        return False
    if _is_desktop_api_token_exempt_path(request.url.path):
        return False
    if _has_valid_signed_desktop_resource(request):
        return False
    return True


def has_valid_desktop_api_token(request: Request) -> bool:
    expected = desktop_api_token()
    for supplied in _desktop_api_header_tokens(request):
        if expected and supplied and hmac.compare_digest(supplied, expected):
            return True
    return False


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


def signed_desktop_resource_query(
    resource_path: str,
    payload: str,
    *,
    method: str = "GET",
    expires_in_seconds: int = DESKTOP_SIGNED_RESOURCE_MAX_TTL_SECONDS,
) -> dict[str, str]:
    expires_in = max(1, min(int(expires_in_seconds), DESKTOP_SIGNED_RESOURCE_MAX_TTL_SECONDS))
    expires_at = str(int(time.time()) + expires_in)
    return {
        DESKTOP_SIGNED_RESOURCE_EXPIRES_QUERY: expires_at,
        DESKTOP_SIGNED_RESOURCE_SIGNATURE_QUERY: _desktop_resource_signature(
            resource_path,
            payload,
            expires_at,
            method=method,
        ),
    }


async def close_unauthorized_desktop_websocket(websocket: WebSocket) -> bool:
    if is_authorized_desktop_websocket(websocket):
        return False
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="unauthorized")


def _is_desktop_api_token_exempt_path(path: str) -> bool:
    if path in {"/health", "/api/health", "/api/pair", "/api/pair/confirm"}:
        return True
    if path.startswith("/api/mobile/"):
        return True
    return False


def _has_valid_signed_desktop_resource(request: Request) -> bool:
    resource_path = request.url.path
    if resource_path not in DESKTOP_SIGNED_RESOURCE_PATHS:
        return False
    expires_at = request.query_params.get(DESKTOP_SIGNED_RESOURCE_EXPIRES_QUERY, "").strip()
    supplied = request.query_params.get(DESKTOP_SIGNED_RESOURCE_SIGNATURE_QUERY, "").strip()
    payload = _signed_desktop_resource_payload(resource_path, request.query_params)
    if not expires_at or not supplied or not payload:
        return False
    try:
        expires_ts = int(expires_at)
    except ValueError:
        return False
    now = int(time.time())
    if expires_ts < now or expires_ts > now + DESKTOP_SIGNED_RESOURCE_MAX_TTL_SECONDS:
        return False
    expected = _desktop_resource_signature(resource_path, payload, expires_at, method=request.method)
    return bool(expected and hmac.compare_digest(supplied, expected))


def _signed_desktop_resource_payload(resource_path: str, query_params: Mapping[str, str]) -> str:
    if resource_path == "/api/library/preview":
        return str(query_params.get("path") or "")
    return ""


def _desktop_resource_signature(resource_path: str, payload: str, expires_at: str, *, method: str = "GET") -> str:
    secret = desktop_api_token()
    if not secret:
        return ""
    body = f"{_normalize_http_method(method)}\n{resource_path}\n{payload}\n{expires_at}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()


def _normalize_http_method(method: str) -> str:
    return str(method or "GET").strip().upper() or "GET"


def _desktop_api_token_optional() -> bool:
    raw = get_env("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL")
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
        for name in (
            "LENGRVIS_TEST",
            "PYTEST_CURRENT_TEST",
            "APP_ENV",
            "LENGRVIS_ENV",
        )
    )


def _desktop_api_header_tokens(request: Request) -> list[str]:
    values: list[str] = []
    supplied = request.headers.get(DESKTOP_API_TOKEN_HEADER, "").strip()
    if supplied:
        values.append(supplied)
    return values


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
