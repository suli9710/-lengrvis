from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import unified_error_body
from app.security.cors import is_cors_preflight
from app.security.desktop_api import has_valid_desktop_api_token, should_require_desktop_api_token
from app.security.lan import (
    DESKTOP_SECURE_TRANSPORT_ERROR,
    LAN_PUBLIC_HTTP_PATHS,
    MOBILE_SECURE_TRANSPORT_ERROR,
    allow_lan_desktop_api,
    allow_remote_lan_desktop_api,
    is_loopback_host,
    is_mobile_token_http_path,
    is_secure_mobile_transport,
)
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE, TOKEN_SCOPE, decode_mobile_token


def register_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def lan_api_guard(request: Request, call_next):
        if is_cors_preflight(request):
            return await call_next(request)
        client_host = request.client.host if request.client else ""
        path = request.url.path
        if is_loopback_host(client_host):
            return await call_next(request)
        if is_mobile_token_http_path(path):
            if is_secure_mobile_transport(client_host, request.url.scheme):
                return await call_next(request)
            return JSONResponse(status_code=403, content=unified_error_body(MOBILE_SECURE_TRANSPORT_ERROR))
        if path in LAN_PUBLIC_HTTP_PATHS:
            return await call_next(request)
        if allow_lan_desktop_api():
            if allow_remote_lan_desktop_api(client_host, request.url.scheme):
                return await call_next(request)
            return JSONResponse(status_code=403, content=unified_error_body(DESKTOP_SECURE_TRANSPORT_ERROR))
        return JSONResponse(
            status_code=403,
            content=unified_error_body(
                "Remote LAN clients may only redeem mobile pairing codes and use mobile APIs.",
                code="lan_desktop_api_blocked",
            ),
        )

    @app.middleware("http")
    async def mobile_jwt_guard(request: Request, call_next):
        if is_cors_preflight(request):
            return await call_next(request)
        if not request.url.path.startswith("/api/mobile/"):
            return await call_next(request)
        client_host = request.client.host if request.client else ""
        if not is_secure_mobile_transport(client_host, request.url.scheme):
            return JSONResponse(status_code=403, content=unified_error_body(MOBILE_SECURE_TRANSPORT_ERROR))
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(status_code=401, content=unified_error_body("Missing mobile bearer token"))
        try:
            decode_mobile_token(token, allowed_scopes={TOKEN_SCOPE, REMOTE_INPUT_SCOPE})
        except Exception as exc:  # noqa: BLE001
            status_code = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "Invalid mobile token")
            return JSONResponse(status_code=status_code, content=unified_error_body(detail))
        return await call_next(request)

    @app.middleware("http")
    async def desktop_api_token_guard(request: Request, call_next):
        if is_cors_preflight(request):
            return await call_next(request)
        if should_require_desktop_api_token(request) and not has_valid_desktop_api_token(request):
            return JSONResponse(status_code=401, content=unified_error_body("Missing desktop API token"))
        return await call_next(request)
