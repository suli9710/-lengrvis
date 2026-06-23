from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes_guardian import proxy_router, router, ws_router
from app.api.routes_pair import router as pair_router
from app.core import db
from app.core.errors import AppError
from app.security.cors import configure_cors
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
from app.services.guardian_runtime import runtime
from app.services.guardian_scheduler import get_guardian_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    await runtime.start()
    scheduler = get_guardian_scheduler()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await runtime.stop()


def create_guardian_app() -> FastAPI:
    app = FastAPI(title="Lengrvis Guardian Backend", version="0.1.0", lifespan=lifespan)
    # Hardened CORS shared with the full backend (app/main.py) via
    # app.security.cors.configure_cors so the two apps can never drift.
    configure_cors(app)

    @app.middleware("http")
    async def lan_api_guard(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        path = request.url.path
        if is_loopback_host(client_host):
            return await call_next(request)
        if is_mobile_token_http_path(path):
            if is_secure_mobile_transport(client_host, request.url.scheme):
                return await call_next(request)
            return JSONResponse(status_code=403, content={"detail": MOBILE_SECURE_TRANSPORT_ERROR})
        if path in LAN_PUBLIC_HTTP_PATHS:
            return await call_next(request)
        if allow_lan_desktop_api():
            if allow_remote_lan_desktop_api(client_host, request.url.scheme):
                return await call_next(request)
            return JSONResponse(status_code=403, content={"detail": DESKTOP_SECURE_TRANSPORT_ERROR})
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "lan_desktop_api_blocked",
                    "message": "Remote LAN clients may only redeem mobile pairing codes and use mobile APIs.",
                }
            },
        )

    @app.middleware("http")
    async def mobile_jwt_guard(request: Request, call_next):
        if not request.url.path.startswith("/api/mobile/"):
            return await call_next(request)
        client_host = request.client.host if request.client else ""
        if not is_secure_mobile_transport(client_host, request.url.scheme):
            return JSONResponse(status_code=403, content={"detail": MOBILE_SECURE_TRANSPORT_ERROR})
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(status_code=401, content={"detail": "Missing mobile bearer token"})
        try:
            decode_mobile_token(token, allowed_scopes={TOKEN_SCOPE, REMOTE_INPUT_SCOPE})
        except Exception as exc:  # noqa: BLE001
            status_code = getattr(exc, "status_code", 401)
            detail = getattr(exc, "detail", "Invalid mobile token")
            return JSONResponse(status_code=status_code, content={"detail": detail})
        return await call_next(request)

    @app.middleware("http")
    async def desktop_api_token_guard(request: Request, call_next):
        if should_require_desktop_api_token(request) and not has_valid_desktop_api_token(request):
            return JSONResponse(status_code=401, content={"detail": "Missing desktop API token"})
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    app.include_router(router)
    # Pairing endpoints are the exact same router as the full backend so the
    # two processes can never drift (single-source convergence of the old
    # guardian mirror copies).
    app.include_router(pair_router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(proxy_router)
    return app


app = create_guardian_app()
