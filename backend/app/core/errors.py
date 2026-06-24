from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_GENERIC_HTTP_CODE = "http_error"


def _slugify_code(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:64] or _GENERIC_HTTP_CODE


def unified_error_body(detail: Any, *, code: str | None = None, message: str | None = None) -> dict[str, Any]:
    """Build the unified error body: {"detail": ..., "error": {"code", "message"}}.

    Keeps the legacy "detail" key for existing clients/tests while exposing the
    machine-readable "error" object. The code is derived from string details via
    slugification unless explicitly provided.
    """
    if message is None:
        message = detail if isinstance(detail, str) else "HTTP error"
    if code is None:
        code = _slugify_code(detail) if isinstance(detail, str) else _GENERIC_HTTP_CODE
    return {"detail": detail, "error": {"code": code, "message": message}}


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SecurityError(AppError):
    def __init__(self, message: str, code: str = "security_error") -> None:
        super().__init__(code=code, message=message, status_code=403)


class StateTransitionError(AppError, ValueError):
    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        super().__init__(
            code="invalid_state_transition",
            message=f"Invalid state transition {source} -> {target}",
            status_code=409,
        )


def register_error_handlers(app: FastAPI) -> None:
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
