from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    routes_agents,
    routes_approvals,
    routes_apps,
    routes_audit,
    routes_browser,
    routes_chat,
    routes_files,
    routes_mcp,
    routes_memories,
    routes_schedules,
    routes_mobile,
    routes_pair,
    routes_settings,
    routes_skills,
    routes_system,
    routes_tasks,
)
from app.config import AppSettings
from app.core import db
from app.core.audit import record
from app.core.errors import AppError
from app.llm.local_provider import health_snapshot
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.security.lan import allow_lan_desktop_api, is_loopback_host, is_mobile_lan_http_path
from app.services.scheduler_service import get_scheduler
from app.tools.registry import register_all_tools
from app.indexer.file_watcher import get_file_watcher


def _dev_api_enabled(settings: AppSettings) -> bool:
    return (settings.mode or "").lower() == "dev" or str(os.getenv("MAVRIS_DEV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    settings = get_effective_settings()
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(settings)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except Exception as exc:  # noqa: BLE001
        mcp_definitions = []
        record("mcp.startup_load_failed", "lifespan", {"error": str(exc)})
    register_all_tools(extra_definitions=mcp_definitions, settings=settings)
    scheduler = get_scheduler()
    await scheduler.start()
    watcher = get_file_watcher()
    await watcher.start(settings.allowed_directories)
    try:
        yield
    finally:
        await watcher.stop()
        await scheduler.stop()


def create_app() -> FastAPI:
    db.init_db()
    settings = get_effective_settings()
    app = FastAPI(title="Marvis Agent EXE Backend", version="0.1.0", lifespan=lifespan)
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
            content={"error": {"code": "lan_desktop_api_blocked", "message": "Remote LAN clients may only use mobile pairing and approval APIs."}},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.get("/health")
    @app.get("/api/health")
    def health():
        return {"status": "ok", "local_llm": health_snapshot(get_effective_settings(), timeout=0.25)}

    for router in [
        routes_chat.router,
        routes_tasks.router,
        routes_agents.router,
        routes_apps.router,
        routes_pair.router,
        routes_mobile.router,
        routes_approvals.router,
        routes_files.router,
        routes_system.router,
        routes_settings.router,
        routes_audit.router,
        routes_browser.router,
        routes_schedules.router,
        routes_memories.router,
        routes_mcp.router,
        routes_skills.router,
    ]:
        app.include_router(router, prefix="/api")
    if _dev_api_enabled(settings):
        from app.api.routes_prompts import router as prompts_router

        app.include_router(prompts_router, prefix="/api")
    app.include_router(routes_chat.ws_router)
    app.include_router(routes_chat.ws_router, prefix="/api")
    app.include_router(routes_mobile.ws_router)
    app.include_router(routes_mobile.ws_router, prefix="/api")

    return app


app = create_app()
