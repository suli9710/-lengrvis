from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_guardian import proxy_router, router, ws_router
from app.core import db
from app.core.errors import AppError
from app.security.lan import allow_lan_desktop_api, is_loopback_host, is_mobile_lan_http_path
from app.security.desktop_api import has_valid_desktop_api_token, should_require_desktop_api_token
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "app://local"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def lan_api_guard(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        if is_loopback_host(client_host) or allow_lan_desktop_api() or is_mobile_lan_http_path(request.url.path):
            return await call_next(request)
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
        if request.url.path.startswith("/api/mobile/pair"):
            return await call_next(request)
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
    app.include_router(ws_router)
    app.include_router(proxy_router)
    return app


app = create_guardian_app()
