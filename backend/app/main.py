from __future__ import annotations

from fastapi import FastAPI

from app.api import (
    routes_agents,
    routes_approvals,
    routes_apps,
    routes_audit,
    routes_browser,
    routes_chat,
    routes_commands,
    routes_commerce,
    routes_context,
    routes_documents,
    routes_files,
    routes_mcp,
    routes_memories,
    routes_metrics,
    routes_mobile,
    routes_observability,
    routes_pair,
    routes_perception,
    routes_remote,
    routes_runs,
    routes_runtime,
    routes_schedules,
    routes_settings,
    routes_skills,
    routes_system,
    routes_tasks,
    routes_ui_automation,
)
from app.config import AppSettings, get_env
from app.core import db
from app.core.errors import register_error_handlers
from app.lazy import LazyASGIApp
from app.lifespan import full_backend_lifespan
from app.llm.local_provider import health_snapshot
from app.llm.registry import get_effective_settings
from app.observability import (
    configure_logging,
    install_crash_handlers,
    register_observability_middleware,
)
from app.security.cors import configure_cors
from app.security.desktop_api import assert_no_production_test_escape_hatches
from app.security.middleware import register_security_middleware


def _dev_api_enabled(settings: AppSettings) -> bool:
    return (settings.mode or "").lower() == "dev" or str(get_env("LENGRVIS_DEV") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def create_app() -> FastAPI:
    configure_logging()
    install_crash_handlers()
    assert_no_production_test_escape_hatches()
    db.init_db()
    settings = get_effective_settings()
    app = FastAPI(title="Lengrvis Agent EXE Backend", version="0.1.1", lifespan=full_backend_lifespan)
    # Hardened CORS shared with the guardian backend (app/guardian.py) via
    # app.security.cors.configure_cors so the two apps can never drift.
    configure_cors(app)
    register_security_middleware(app)
    register_error_handlers(app)

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
        routes_observability.router,
        routes_mcp.router,
        routes_commands.router,
        routes_commerce.router,
        routes_context.router,
        routes_documents.router,
        routes_perception.router,
        routes_skills.router,
    ]:
        app.include_router(router, prefix="/api")
    if _dev_api_enabled(settings):
        from app.api.routes_prompts import router as prompts_router

        app.include_router(prompts_router, prefix="/api")
    for router, prefixes in [
        (routes_chat.ws_router, ("", "/api")),
        (routes_mobile.ws_router, ("", "/api")),
        (routes_remote.ws_router, ("", "/api")),
        (routes_browser.ws_router, ("/api",)),
        (routes_runs.ws_router, ("", "/api")),
        (routes_settings.ws_router, ("", "/api")),
    ]:
        for prefix in prefixes:
            app.include_router(router, prefix=prefix)

    register_observability_middleware(app)

    return app


app = LazyASGIApp(create_app, title="Lengrvis Agent EXE Backend", version="0.1.1")
