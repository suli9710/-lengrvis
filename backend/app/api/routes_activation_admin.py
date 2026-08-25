"""Admin web/API routes for the subscription activation server."""
# ruff: noqa: E501

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.api.activation_admin_template import ADMIN_HTML
from app.commerce.activation import (
    ActivationError,
    delete_subscription_key,
    enforce_activation_rate_limit,
    list_subscription_keys,
    renew_subscription_key,
    revoke_subscription_key,
    unbind_activation_device,
    upsert_subscription_key,
)
from app.commerce.entitlements import normalize_plan
from app.core.errors import AppError

router = APIRouter()

ADMIN_PASSWORD_HASH_ENV_VAR = "LENGRVIS_ADMIN_PASSWORD_HASH"  # noqa: S105 - env var name only.
ADMIN_SESSION_SECRET_ENV_VAR = "LENGRVIS_ADMIN_SESSION_SECRET"  # noqa: S105 - env var name only.
ADMIN_SESSION_TTL_SECONDS_ENV_VAR = "LENGRVIS_ADMIN_SESSION_TTL_SECONDS"
ADMIN_SESSION_COOKIE = "lengrvis_admin_session"  # noqa: S105 - cookie name only.
ADMIN_CSRF_COOKIE = "lengrvis_admin_csrf"  # noqa: S105 - cookie name only.

_PASSWORD_HASH_SCHEME = "pbkdf2_sha256"  # noqa: S105 - password hash algorithm label.
_PASSWORD_HASH_ITERATIONS = 390_000
_SESSION_TTL_SECONDS = 60 * 60 * 8
_ADMIN_PLANS = {"free", "plus", "pro", "max", "team"}
_ADMIN_STATUSES = {"active", "trialing", "past_due", "canceled", "expired", "revoked"}


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class AdminCreateSubscriptionRequest(BaseModel):
    plan: str = Field(default="plus", max_length=16)
    subscription_id: str = Field(default="", max_length=128)
    status: str = Field(default="active", max_length=32)
    subject: str = Field(default="", max_length=256)
    seats: int = Field(default=1, ge=1, le=10_000)
    max_devices: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: str | None = Field(default=None, max_length=64)
    renews_at: str | None = Field(default=None, max_length=64)
    cancel_at_period_end: bool = False
    order_ref: str = Field(default="", max_length=128)

    @field_validator("expires_at", "renews_at")
    @classmethod
    def _validate_iso_datetime(cls, value: str | None) -> str | None:
        return _validate_optional_iso_datetime(value)


class AdminRenewSubscriptionRequest(BaseModel):
    status: str = Field(default="active", max_length=32)
    expires_at: str | None = Field(default=None, max_length=64)
    renews_at: str | None = Field(default=None, max_length=64)
    cancel_at_period_end: bool = False
    seats: int | None = Field(default=None, ge=1, le=10_000)
    max_devices: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("expires_at", "renews_at")
    @classmethod
    def _validate_iso_datetime(cls, value: str | None) -> str | None:
        return _validate_optional_iso_datetime(value)


def _validate_optional_iso_datetime(value: str | None) -> str | None:
    # Reject non-ISO datetime strings at the request boundary so a bad value
    # returns HTTP 422 (validation error) instead of an unhandled ValueError ->
    # 500 deeper in parse_activation_datetime.
    if value is None or not str(value).strip():
        return value
    from app.commerce.activation_store import parse_activation_datetime

    try:
        parse_activation_datetime(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("must be an ISO 8601 datetime") from exc
    return value


class AdminAuthError(AppError):
    def __init__(self, message: str, *, code: str = "admin_unauthorized", status_code: int = 401) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@router.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_HTML)


@router.get("/admin/")
def admin_page_slash() -> RedirectResponse:
    return RedirectResponse("/admin", status_code=307)


@router.get("/api/admin/session")
def admin_session(request: Request) -> dict[str, Any]:
    configured = admin_auth_configured()
    authenticated = False
    try:
        _require_admin_session(request)
        authenticated = True
    except AdminAuthError:
        authenticated = False
    return {"configured": configured, "authenticated": authenticated}


@router.post("/api/admin/login")
def admin_login(payload: AdminLoginRequest, request: Request, response: Response) -> dict[str, Any]:
    _enforce_login_rate_limit(_client_scope(request))
    if not admin_auth_configured():
        raise AdminAuthError("管理员认证尚未配置。", code="admin_unconfigured", status_code=503)
    if not verify_admin_password(payload.password, _admin_password_hash()):
        raise AdminAuthError("管理员密码错误。", code="admin_invalid_credentials", status_code=401)
    csrf = secrets.token_urlsafe(24)
    token = _issue_session_token(csrf=csrf)
    max_age = _session_ttl_seconds()
    secure = _secure_cookie(request)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        ADMIN_CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@router.post("/api/admin/logout")
def admin_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    response.delete_cookie(ADMIN_CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/admin/subscriptions")
def admin_list_subscriptions(request: Request) -> dict[str, Any]:
    _require_admin_session(request)
    return {"items": list_subscription_keys()}


@router.post("/api/admin/subscriptions")
def admin_create_subscription(payload: AdminCreateSubscriptionRequest, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    _enforce_mutation_rate_limit(request)
    plan = _normalize_admin_plan(payload.plan)
    status = _normalize_admin_status(payload.status)
    activation_key = f"lgrv_{secrets.token_urlsafe(24)}"
    result = upsert_subscription_key(
        activation_key=activation_key,
        plan=plan,
        subscription_id=_subscription_id_or_generated(plan, payload.subscription_id),
        status=status,
        subject=payload.subject,
        seats=payload.seats,
        max_devices=payload.max_devices,
        expires_at=_empty_to_none(payload.expires_at),
        renews_at=_empty_to_none(payload.renews_at),
        cancel_at_period_end=payload.cancel_at_period_end,
        order_ref=payload.order_ref,
    )
    return {
        "ok": True,
        "activation_key": activation_key,
        "record": result,
    }


@router.post("/api/admin/subscriptions/{key_hash}/revoke")
def admin_revoke_subscription(key_hash: str, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    _enforce_mutation_rate_limit(request)
    return {"ok": True, "record": revoke_subscription_key(key_hash=key_hash)}


@router.post("/api/admin/subscriptions/{key_hash}/renew")
def admin_renew_subscription(
    key_hash: str,
    payload: AdminRenewSubscriptionRequest,
    request: Request,
) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    _enforce_mutation_rate_limit(request)
    record = renew_subscription_key(
        key_hash=key_hash,
        status=_normalize_admin_status(payload.status),
        expires_at=_empty_to_none(payload.expires_at),
        renews_at=_empty_to_none(payload.renews_at),
        cancel_at_period_end=payload.cancel_at_period_end,
        seats=payload.seats,
        max_devices=payload.max_devices,
    )
    return {"ok": True, "record": record}


@router.delete("/api/admin/subscriptions/{key_hash}")
def admin_delete_subscription(key_hash: str, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    _enforce_mutation_rate_limit(request)
    return {"ok": True, **delete_subscription_key(key_hash=key_hash)}


@router.delete("/api/admin/devices/{license_id}")
def admin_unbind_device(license_id: str, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    _enforce_mutation_rate_limit(request)
    return {"ok": True, **unbind_activation_device(license_id=license_id)}


def hash_admin_password(
    password: str, *, salt: bytes | None = None, iterations: int = _PASSWORD_HASH_ITERATIONS
) -> str:
    """Return a deployable PBKDF2 admin password hash."""
    text = str(password or "")
    if not text:
        raise ValueError("admin password must not be empty")
    salt_value = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), salt_value, iterations)
    return "$".join(
        (
            _PASSWORD_HASH_SCHEME,
            str(iterations),
            _b64url_encode(salt_value),
            _b64url_encode(digest),
        )
    )


def verify_admin_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, digest_raw = str(encoded_hash or "").split("$", 3)
        if scheme != _PASSWORD_HASH_SCHEME:
            return False
        iterations = int(iterations_raw)
        salt = _b64url_decode(salt_raw)
        expected = _b64url_decode(digest_raw)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def admin_auth_configured() -> bool:
    return bool(_admin_password_hash() and _session_secret())


def _require_admin_session(request: Request) -> dict[str, Any]:
    if not admin_auth_configured():
        raise AdminAuthError("管理员认证尚未配置。", code="admin_unconfigured", status_code=503)
    token = str(request.cookies.get(ADMIN_SESSION_COOKIE) or "")
    if not token:
        raise AdminAuthError("缺少管理员会话。")
    payload = _verify_session_token(token)
    if payload.get("sub") != "activation_admin":
        raise AdminAuthError("管理员会话无效。")
    return payload


def _require_csrf(request: Request, session: dict[str, Any]) -> None:
    expected = str(session.get("csrf") or "")
    header = str(request.headers.get("x-lengrvis-admin-csrf") or "")
    cookie = str(request.cookies.get(ADMIN_CSRF_COOKIE) or "")
    if not expected or not hmac.compare_digest(header, expected) or not hmac.compare_digest(cookie, expected):
        raise AdminAuthError("管理员安全校验失败，请刷新页面后重试。", code="admin_csrf_invalid", status_code=403)


def _issue_session_token(*, csrf: str) -> str:
    now = int(time.time())
    payload = {
        "sub": "activation_admin",
        "iat": now,
        "exp": now + _session_ttl_seconds(),
        "csrf": csrf,
    }
    body = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(signature)}"


def _verify_session_token(token: str) -> dict[str, Any]:
    body, sep, signature = str(token or "").partition(".")
    if not body or sep != "." or not signature:
        raise AdminAuthError("管理员会话无效。")
    try:
        expected = hmac.new(_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        supplied_signature = _b64url_decode(signature)
        if not hmac.compare_digest(supplied_signature, expected):
            raise AdminAuthError("管理员会话无效。")
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, AdminAuthError):
            raise
        raise AdminAuthError("管理员会话无效。") from exc
    if not isinstance(payload, dict):
        raise AdminAuthError("管理员会话无效。")
    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise AdminAuthError("管理员会话无效。") from exc
    if int(time.time()) >= expires_at:
        raise AdminAuthError("管理员会话已过期，请重新登录。")
    return payload


def _admin_password_hash() -> str:
    return str(os.getenv(ADMIN_PASSWORD_HASH_ENV_VAR, "")).strip()


def _session_secret() -> str:
    return str(os.getenv(ADMIN_SESSION_SECRET_ENV_VAR, "")).strip()


def _session_ttl_seconds() -> int:
    raw = str(os.getenv(ADMIN_SESSION_TTL_SECONDS_ENV_VAR, "")).strip()
    try:
        return max(900, min(86_400, int(raw))) if raw else _SESSION_TTL_SECONDS
    except ValueError:
        return _SESSION_TTL_SECONDS


_ADMIN_LOGIN_RATE_LIMIT_MAX = 10
_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300
_ADMIN_MUTATION_RATE_LIMIT_MAX = 30
_ADMIN_MUTATION_RATE_LIMIT_WINDOW_SECONDS = 300


def _enforce_admin_rate_limit(
    scope: str,
    *,
    maximum: int,
    window_seconds: int,
    message: str,
) -> None:
    try:
        enforce_activation_rate_limit(
            scope,
            maximum=maximum,
            window_seconds=window_seconds,
        )
    except ActivationError as exc:
        if exc.code == "activation_rate_limited":
            raise AdminAuthError(
                message,
                code="admin_rate_limited",
                status_code=429,
            ) from exc
        raise


def _enforce_login_rate_limit(scope: str) -> None:
    _enforce_admin_rate_limit(
        f"admin_login:{scope}",
        maximum=_ADMIN_LOGIN_RATE_LIMIT_MAX,
        window_seconds=_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
        message="管理员登录尝试过多，请稍后再试。",
    )


def _enforce_mutation_rate_limit(request: Request) -> None:
    _enforce_admin_rate_limit(
        f"admin_mutation:{_client_scope(request)}",
        maximum=_ADMIN_MUTATION_RATE_LIMIT_MAX,
        window_seconds=_ADMIN_MUTATION_RATE_LIMIT_WINDOW_SECONDS,
        message="管理员操作过于频繁，请稍后再试。",
    )


def _client_scope(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _secure_cookie(request: Request) -> bool:
    return str(request.url.scheme).lower() == "https"


def _normalize_admin_plan(value: str) -> str:
    plan = normalize_plan(value)
    if plan.value not in _ADMIN_PLANS:
        raise ActivationError("套餐必须是 Free、Plus 或 Pro。", code="admin_plan_invalid", status_code=422)
    return plan.value


def _normalize_admin_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in _ADMIN_STATUSES:
        raise ActivationError("订阅状态无效。", code="admin_status_invalid", status_code=422)
    return status


def _empty_to_none(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _subscription_id_or_generated(plan: str, value: str | None) -> str:
    text = str(value or "").strip()
    if text:
        return text
    timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    return f"sub_{plan}_{timestamp}_{secrets.token_hex(4)}"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    raw = (str(text or "") + padding).encode("ascii")
    return base64.b64decode(raw, altchars=b"-_", validate=True)
