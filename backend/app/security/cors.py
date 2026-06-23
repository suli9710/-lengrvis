from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Browser origins allowed to call the backend: the local Vite dev server
# (localhost / 127.0.0.1) and the packaged desktop shell (app://local).
CORS_ALLOW_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "app://local"]
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
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )
