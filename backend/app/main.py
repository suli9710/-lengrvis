from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    routes_agents,
    routes_approvals,
    routes_apps,
    routes_audit,
    routes_browser,
    routes_chat,
    routes_commands,
    routes_context,
    routes_documents,
    routes_files,
    routes_mcp,
    routes_memories,
    routes_metrics,
    routes_schedules,
    routes_mobile,
    routes_pair,
    routes_perception,
    routes_remote,
    routes_runs,
    routes_runtime,
    routes_settings,
    routes_skills,
    routes_system,
    routes_tasks,
    routes_ui_automation,
)
from app.config import AppSettings, get_env
from app.core import db
from app.core.audit import record
from app.core.errors import AppError, unified_error_body
from app.core.session_context import get_session_context_store
from app.llm.local_provider import health_snapshot
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.security.lan import (
    LAN_PUBLIC_HTTP_PATHS,
    MOBILE_SECURE_TRANSPORT_ERROR,
    allow_lan_desktop_api,
    is_loopback_host,
    is_mobile_token_http_path,
    is_secure_mobile_transport,
)
from app.security.desktop_api import has_valid_desktop_api_token, should_require_desktop_api_token
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE, TOKEN_SCOPE, decode_mobile_token
from app.services.scheduler_service import get_scheduler
from app.tools.registry import register_all_tools
from app.indexer.file_watcher import get_file_watcher
from app.orchestration.agent_bus import AgentBus
from app.orchestration.dispatcher import EventDispatcher
from app.perception.environment_stream import get_environment_stream


def _dev_api_enabled(settings: AppSettings) -> bool:
    return (settings.mode or "").lower() == "dev" or str(get_env("LENGRVIS_DEV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # A starting backend always accepts runs: shutdown_runs() below flips the
    # module-level gate off, and the same process can restart lifespan (tests,
    # embedded hosts), so reset it here rather than relying on import state.
    from app.services.run_service import enter_foreground_runtime, recover_interrupted_runs

    enter_foreground_runtime()
    # Crash recovery: rows stuck in RUNNING from a previous process have no
    # engine loop anymore; mark them PAUSED so they are resumable, not zombies.
    try:
        recovered = recover_interrupted_runs()
        if recovered:
            record("lifespan.runs_recovered", "lifespan", {"run_ids": recovered})
    except Exception as exc:  # noqa: BLE001
        record("lifespan.run_recovery_failed", "lifespan", {"error": str(exc)})
    settings = get_effective_settings()
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(settings)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except Exception as exc:  # noqa: BLE001
        mcp_definitions = []
        record("mcp.startup_load_failed", "lifespan", {"error": str(exc)})
    register_all_tools(extra_definitions=mcp_definitions, settings=settings)
    session_store = get_session_context_store()
    session_store.load_global_latest()
    scheduler = get_scheduler()
    await scheduler.start()
    watcher = get_file_watcher()
    environment_bus = AgentBus()
    environment_stream = get_environment_stream(
        dispatcher=EventDispatcher(environment_bus),
        bus=environment_bus,
        settings=settings,
        reset=True,
    )
    file_environment_sink = environment_stream.file_change_sink()
    watcher.subscribe_changes(file_environment_sink)
    await environment_stream.start()
    await watcher.start(settings.allowed_directories)
    try:
        yield
    finally:
        from app.llm.openai_compatible import close_shared_http_client
        from app.services.ollama_service import stop_spawned_server
        from app.services.run_service import shutdown_runs
        from app.services.task_pool import get_pool

        # Drain in-flight run engine loops first (R4-M3): they are plain
        # loop.create_task futures outside the TaskPool, so nothing else
        # stops them gracefully on shutdown.
        try:
            await shutdown_runs()
        except Exception as exc:  # noqa: BLE001
            record("lifespan.run_drain_failed", "lifespan", {"error": str(exc)})
        # Commit queued agent-message writes before exit (writer is a daemon
        # thread; without the flush, tail messages would be lost on shutdown).
        from app.orchestration.agent_bus import flush_agent_message_writes

        await asyncio.to_thread(flush_agent_message_writes)
        await close_shared_http_client()
        session_store.save()
        await watcher.stop()
        watcher.unsubscribe_changes(file_environment_sink)
        await environment_stream.stop()
        await scheduler.stop()
        await get_pool().shutdown()
        # Only stops the `ollama serve` process this backend itself spawned;
        # externally started Ollama instances are left untouched.
        stop_spawned_server()


def create_app() -> FastAPI:
    db.init_db()
    settings = get_effective_settings()
    app = FastAPI(title="Lengrvis Agent EXE Backend", version="0.1.0", lifespan=lifespan)
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
        path = request.url.path
        if is_loopback_host(client_host):
            return await call_next(request)
        if is_mobile_token_http_path(path):
            if is_secure_mobile_transport(client_host, request.url.scheme):
                return await call_next(request)
            return JSONResponse(status_code=403, content=unified_error_body(MOBILE_SECURE_TRANSPORT_ERROR))
        if path in LAN_PUBLIC_HTTP_PATHS or allow_lan_desktop_api():
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content=unified_error_body(
                "Remote LAN clients may only redeem mobile pairing codes and use mobile APIs.",
                code="lan_desktop_api_blocked",
            ),
        )

    @app.middleware("http")
    async def mobile_jwt_guard(request: Request, call_next):
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
        if should_require_desktop_api_token(request) and not has_valid_desktop_api_token(request):
            return JSONResponse(status_code=401, content=unified_error_body("Missing desktop API token"))
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=unified_error_body(exc.message, code=exc.code, message=exc.message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=unified_error_body(jsonable_encoder(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=unified_error_body(
                jsonable_encoder(exc.errors()),
                code="validation_error",
                message="Request validation failed",
            ),
        )

    @app.get("/health")
    @app.get("/api/health")
    def health():
        settings = get_effective_settings()
        payload = {"status": "ok", "mode": settings.mode}
        if (settings.mode or "efficiency").lower() in {"privacy", "hybrid"}:
            payload["local_llm"] = health_snapshot(settings, timeout=0.25)
        return payload

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
        routes_runs.router,
        routes_runtime.router,
        routes_settings.router,
        routes_audit.router,
        routes_browser.router,
        routes_ui_automation.router,
        routes_schedules.router,
        routes_memories.router,
        routes_metrics.router,
        routes_mcp.router,
        routes_commands.router,
        routes_context.router,
        routes_documents.router,
        routes_perception.router,
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
    app.include_router(routes_remote.ws_router)
    app.include_router(routes_remote.ws_router, prefix="/api")
    app.include_router(routes_browser.ws_router, prefix="/api")
    app.include_router(routes_runs.ws_router)
    app.include_router(routes_runs.ws_router, prefix="/api")
    app.include_router(routes_settings.ws_router)
    app.include_router(routes_settings.ws_router, prefix="/api")

    return app


app = create_app()
