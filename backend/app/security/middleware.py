from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import unified_error_body
from app.security.cors import is_cors_preflight
from app.security.desktop_api import has_valid_desktop_api_token, should_require_desktop_api_token
from app.security.lan import (
    DESKTOP_SECURE_TRANSPORT_ERROR,
    FORWARDED_HEADER_NAMES,
    LAN_PUBLIC_HTTP_PATHS,
    MOBILE_SECURE_TRANSPORT_ERROR,
    allow_lan_desktop_api,
    allow_remote_lan_desktop_api,
    client_transport_from_request,
    is_loopback_host,
    is_mobile_token_http_path,
    resolve_client_transport,
)
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE, TOKEN_SCOPE, decode_mobile_token


class TrustedProxyHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("", 0)
        client_host = client[0] if client else ""
        headers = _scope_headers(scope)
        transport = resolve_client_transport(
            client_host=client_host,
            scheme=scope.get("scheme", ""),
            headers=headers,
        )
        if transport.proxy_error:
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "untrusted proxy headers"})
                return
            response = JSONResponse(
                status_code=403,
                content=unified_error_body(transport.proxy_error, code="untrusted_proxy_headers"),
            )
            await response(scope, receive, send)
            return
        if transport.used_forwarded_headers:
            scope = dict(scope)
            scope["client"] = (transport.client_host, client[1] if len(client) > 1 else 0)
            scope["scheme"] = transport.scheme
            scope["headers"] = _strip_forwarded_scope_headers(scope)
        await self.app(scope, receive, send)


def register_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def lan_api_guard(request: Request, call_next):
        if is_cors_preflight(request):
            return await call_next(request)
        transport = client_transport_from_request(request)
        if transport.proxy_error:
            return JSONResponse(
                status_code=403,
                content=unified_error_body(transport.proxy_error, code="untrusted_proxy_headers"),
            )
        client_host = transport.client_host
        path = request.url.path
        if is_loopback_host(client_host):
            return await call_next(request)
        if is_mobile_token_http_path(path):
            if is_loopback_host(client_host) or transport.scheme in {"https", "wss"}:
                return await call_next(request)
            return JSONResponse(status_code=403, content=unified_error_body(MOBILE_SECURE_TRANSPORT_ERROR))
        if path in LAN_PUBLIC_HTTP_PATHS:
            return await call_next(request)
        if allow_lan_desktop_api():
            if allow_remote_lan_desktop_api(client_host, transport.scheme):
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
        transport = client_transport_from_request(request)
        if transport.proxy_error:
            return JSONResponse(
                status_code=403,
                content=unified_error_body(transport.proxy_error, code="untrusted_proxy_headers"),
            )
        if not (is_loopback_host(transport.client_host) or transport.scheme in {"https", "wss"}):
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

    app.add_middleware(TrustedProxyHeadersMiddleware)


def _scope_headers(scope) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if name in result:
            result[name] = f"{result[name]},{value}"
        else:
            result[name] = value
    return result


def _strip_forwarded_scope_headers(scope) -> list[tuple[bytes, bytes]]:
    forwarded = set(FORWARDED_HEADER_NAMES)
    return [
        (raw_name, raw_value)
        for raw_name, raw_value in scope.get("headers") or []
        if raw_name.decode("latin-1").lower() not in forwarded
    ]
