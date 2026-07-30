from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_env

# Browser origins allowed to call the backend: the local Vite dev server
# (localhost / 127.0.0.1) and the packaged desktop shell (app://local).
DESKTOP_CORS_ALLOW_ORIGINS = ["app://local"]
DEV_CORS_ALLOW_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
CORS_ALLOW_ORIGINS = [*DEV_CORS_ALLOW_ORIGINS, *DESKTOP_CORS_ALLOW_ORIGINS]
# P1-7 fix: hardened method/header allowlists instead of wildcard "*". Kept as
# the single source of truth for both the guardian and full backends so their
# CORS policy can never drift.
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Lengrvis-Desktop-Token"]


def configure_cors(app: FastAPI) -> None:
    """Apply the shared, hardened CORS policy to a FastAPI app.

    Both the guardian backend (app/guardian.py) and the full backend
    (app/main.py) call this so their CORS configuration stays in sync.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins(),
        allow_credentials=True,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )


def is_cors_preflight(request: Request) -> bool:
    return (
        request.method.upper() == "OPTIONS"
        and bool(request.headers.get("origin"))
        and bool(request.headers.get("access-control-request-method"))
    )


def cors_allow_origins() -> list[str]:
    if _is_production_environment():
        return list(DESKTOP_CORS_ALLOW_ORIGINS)
    return list(CORS_ALLOW_ORIGINS)


def _is_production_environment() -> bool:
    # Use the same release-profile signals as execution_isolation so a build
    # labelled ga/beta/rc (or flagged only via LENGRVIS_COMMERCIAL_RELEASE etc.)
    # does not keep trusting the Vite dev origins in a shipped product.
    from app.config import env_flag
    from app.security.execution_isolation import (
        RELEASE_BOOLEAN_NAMES,
        RELEASE_ENVIRONMENT_NAMES,
        RELEASE_ENVIRONMENT_VALUES,
    )

    for name in RELEASE_ENVIRONMENT_NAMES:
        if str(get_env(name) or "").strip().casefold() in RELEASE_ENVIRONMENT_VALUES:
            return True
    return any(env_flag(name) for name in RELEASE_BOOLEAN_NAMES)
