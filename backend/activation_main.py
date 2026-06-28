# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.api import routes_activation, routes_activation_admin
from app.core.errors import register_error_handlers
from app.observability import configure_logging, install_crash_handlers
from app.security.cors import configure_cors, is_cors_preflight
from app.security.lan import FORWARDED_HEADER_NAMES, resolve_client_transport


class ActivationTrustedProxyHeadersMiddleware:
    """Minimal proxy-header hardening for the public activation-only app."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("", 0)
        headers = _scope_headers(scope)
        transport = resolve_client_transport(
            client_host=client[0] if client else "",
            scheme=scope.get("scheme", ""),
            headers=headers,
        )
        if transport.proxy_error:
            response = JSONResponse(
                status_code=403,
                content={"detail": transport.proxy_error},
            )
            await response(scope, receive, send)
            return
        if transport.used_forwarded_headers:
            scope = dict(scope)
            scope["client"] = (transport.client_host, client[1] if len(client) > 1 else 0)
            scope["scheme"] = transport.scheme
            scope["headers"] = _strip_forwarded_scope_headers(scope)
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    configure_logging()
    install_crash_handlers()
    app = FastAPI(title="Lengrvis Activation Server", version="0.1.1")
    configure_cors(app)
    register_error_handlers(app)

    @app.middleware("http")
    async def activation_public_guard(request, call_next):
        if is_cors_preflight(request):
            return await call_next(request)
        return await call_next(request)

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "activation"}

    app.include_router(routes_activation.router, prefix="/api")
    app.include_router(routes_activation_admin.router)
    app.add_middleware(ActivationTrustedProxyHeadersMiddleware)
    return app


app = create_app()


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
