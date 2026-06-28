from __future__ import annotations

from fastapi import FastAPI

from app.api.routes_guardian import proxy_router, router, ws_router
from app.api.routes_pair import router as pair_router
from app.core.errors import register_error_handlers
from app.lazy import LazyASGIApp
from app.lifespan import guardian_lifespan
from app.security.cors import configure_cors
from app.security.desktop_api import assert_no_production_test_escape_hatches
from app.security.middleware import register_security_middleware
from app.security.mobile_jwt import decode_mobile_token

__all__ = ["app", "create_guardian_app", "decode_mobile_token", "pair_router"]


def create_guardian_app() -> FastAPI:
    assert_no_production_test_escape_hatches()
    app = FastAPI(title="Lengrvis Guardian Backend", version="0.1.1", lifespan=guardian_lifespan)
    # Hardened CORS shared with the full backend (app/main.py) via
    # app.security.cors.configure_cors so the two apps can never drift.
    configure_cors(app)
    register_security_middleware(app)
    register_error_handlers(app)

    app.include_router(router)
    # Pairing endpoints are the exact same router as the full backend so the
    # two processes can never drift (single-source convergence of the old
    # guardian mirror copies).
    app.include_router(pair_router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(proxy_router)
    return app


app = LazyASGIApp(create_guardian_app, title="Lengrvis Guardian Backend", version="0.1.1")
