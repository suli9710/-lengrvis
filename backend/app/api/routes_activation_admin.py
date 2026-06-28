"""Admin web/API routes for the subscription activation server."""
# ruff: noqa: E501

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.commerce.activation import (
    ActivationError,
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
_LOGIN_BUCKETS: dict[str, list[float]] = {}
_ADMIN_PLANS = {"free", "pro", "max"}
_ADMIN_STATUSES = {"active", "trialing", "past_due", "canceled", "expired", "revoked"}


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class AdminCreateSubscriptionRequest(BaseModel):
    plan: str = Field(default="pro", max_length=16)
    subscription_id: str = Field(min_length=1, max_length=128)
    status: str = Field(default="active", max_length=32)
    subject: str = Field(default="", max_length=256)
    seats: int = Field(default=1, ge=1, le=10_000)
    max_devices: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: str | None = Field(default=None, max_length=64)
    renews_at: str | None = Field(default=None, max_length=64)
    cancel_at_period_end: bool = False
    order_ref: str = Field(default="", max_length=128)


class AdminRenewSubscriptionRequest(BaseModel):
    status: str = Field(default="active", max_length=32)
    expires_at: str | None = Field(default=None, max_length=64)
    renews_at: str | None = Field(default=None, max_length=64)
    cancel_at_period_end: bool = False
    seats: int | None = Field(default=None, ge=1, le=10_000)
    max_devices: int | None = Field(default=None, ge=1, le=10_000)


class AdminAuthError(AppError):
    def __init__(self, message: str, *, code: str = "admin_unauthorized", status_code: int = 401) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


@router.get("/admin", response_class=HTMLResponse)
def admin_page() -> HTMLResponse:
    return HTMLResponse(_ADMIN_HTML)


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
    plan = _normalize_admin_plan(payload.plan)
    status = _normalize_admin_status(payload.status)
    activation_key = f"lgrv_{secrets.token_urlsafe(24)}"
    result = upsert_subscription_key(
        activation_key=activation_key,
        plan=plan,
        subscription_id=payload.subscription_id,
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
    return {"ok": True, "record": revoke_subscription_key(key_hash=key_hash)}


@router.post("/api/admin/subscriptions/{key_hash}/renew")
def admin_renew_subscription(
    key_hash: str,
    payload: AdminRenewSubscriptionRequest,
    request: Request,
) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
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


@router.delete("/api/admin/devices/{license_id}")
def admin_unbind_device(license_id: str, request: Request) -> dict[str, Any]:
    session = _require_admin_session(request)
    _require_csrf(request, session)
    return {"ok": True, **unbind_activation_device(license_id=license_id)}


def hash_admin_password(password: str, *, salt: bytes | None = None, iterations: int = _PASSWORD_HASH_ITERATIONS) -> str:
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
    expected = hmac.new(_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_decode(signature), expected):
        raise AdminAuthError("管理员会话无效。")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
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


def _enforce_login_rate_limit(scope: str) -> None:
    current = time.time()
    cutoff = current - 300
    bucket = [value for value in _LOGIN_BUCKETS.get(scope, []) if value > cutoff]
    if len(bucket) >= 10:
        _LOGIN_BUCKETS[scope] = bucket
        raise AdminAuthError("管理员登录尝试过多，请稍后再试。", code="admin_rate_limited", status_code=429)
    bucket.append(current)
    _LOGIN_BUCKETS[scope] = bucket


def _client_scope(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _secure_cookie(request: Request) -> bool:
    return str(request.url.scheme).lower() == "https"


def _normalize_admin_plan(value: str) -> str:
    plan = normalize_plan(value)
    if plan.value not in _ADMIN_PLANS:
        raise ActivationError("套餐必须是 Free、Pro 或 Max。", code="admin_plan_invalid", status_code=422)
    return plan.value


def _normalize_admin_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in _ADMIN_STATUSES:
        raise ActivationError("订阅状态无效。", code="admin_status_invalid", status_code=422)
    return status


def _empty_to_none(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(str(text or "") + padding)


_ADMIN_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lengrvis 激活管理后台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef1f4;
      --panel: #fbfcfd;
      --panel-strong: #ffffff;
      --line: #cfd6de;
      --line-soft: #e7ebef;
      --text: #151b23;
      --muted: #687381;
      --muted-2: #8a96a3;
      --accent: #116a5b;
      --accent-dark: #0b4c41;
      --accent-soft: #dcefe9;
      --danger: #b42318;
      --danger-soft: #fff0ee;
      --warn: #8a5a00;
      --warn-soft: #fff4d6;
      --ok: #087443;
      --ok-soft: #e1f4eb;
      --ink: #111820;
      --shadow: 0 16px 40px rgba(16, 24, 40, .08);
      --radius: 8px;
      font-family: "Microsoft YaHei UI", "Aptos", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(rgba(21, 27, 35, .035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(21, 27, 35, .03) 1px, transparent 1px),
        var(--bg);
      background-size: 28px 28px;
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 68px;
      padding: 0 28px;
      border-bottom: 1px solid #27313c;
      background: var(--ink);
      color: #f8fafc;
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border: 1px solid rgba(255, 255, 255, .24);
      border-radius: 7px;
      background: #18302c;
      color: #9ee2cf;
      font-weight: 800;
    }
    .brand-copy { min-width: 0; }
    h1 { margin: 0; font-size: 18px; font-weight: 760; letter-spacing: 0; }
    .eyebrow { margin-top: 2px; color: #9aa8b6; font-size: 12px; }
    h2 { margin: 0; font-size: 15px; font-weight: 760; letter-spacing: 0; }
    main { max-width: 1280px; margin: 0 auto; padding: 22px 18px 44px; }
    .hidden { display: none !important; }
    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 380px) minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .login-shell {
      display: grid;
      place-items: center;
      min-height: calc(100vh - 150px);
    }
    #loginPanel {
      width: min(420px, 100%);
      padding: 24px;
      background: var(--panel-strong);
    }
    .panel-body { padding: 16px; }
    .panel-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 52px;
      padding: 0 16px;
      border-bottom: 1px solid var(--line-soft);
      background: var(--panel-strong);
      border-radius: var(--radius) var(--radius) 0 0;
    }
    .panel-title span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    label {
      display: grid;
      gap: 6px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
      background: #fff;
      color: var(--text);
      outline: none;
    }
    input:focus, select:focus {
      border-color: #4a9184;
      box-shadow: 0 0 0 3px rgba(17, 106, 91, .14);
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .field-note {
      margin: -4px 0 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .checkbox-line {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
    }
    .checkbox-line input {
      width: 16px;
      min-height: 16px;
      height: 16px;
      padding: 0;
    }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    button {
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 7px 12px;
      background: #fff;
      color: var(--text);
      font: inherit;
      font-weight: 760;
      cursor: pointer;
      transition: transform .12s ease, border-color .12s ease, background .12s ease;
    }
    button:hover { border-color: #a9b4bf; transform: translateY(-1px); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.primary:hover { background: var(--accent-dark); }
    button.danger { color: var(--danger); border-color: #efb5ae; }
    button.compact { min-height: 30px; padding: 4px 9px; font-size: 12px; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .message { min-height: 20px; margin-top: 10px; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }
    .handoff {
      margin-top: 12px;
      border: 1px solid #9db4ad;
      border-radius: 8px;
      background: #f4fbf8;
      overflow: hidden;
    }
    .handoff-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      border-bottom: 1px solid #c9ddd6;
      color: var(--accent-dark);
      font-size: 12px;
      font-weight: 800;
    }
    .keybox {
      padding: 11px;
      font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 1px;
      margin-bottom: 14px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--line);
    }
    .metric {
      min-height: 76px;
      padding: 12px;
      background: var(--panel-strong);
    }
    .metric strong { display: block; font-size: 24px; line-height: 1.1; }
    .metric span { display: block; margin-top: 6px; color: var(--muted); font-size: 12px; font-weight: 760; }
    .table-wrap { overflow: auto; border-top: 1px solid var(--line-soft); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 840px; }
    th, td { border-bottom: 1px solid var(--line-soft); padding: 11px 10px; text-align: left; vertical-align: top; }
    th {
      position: sticky;
      top: 0;
      z-index: 1;
      color: var(--muted);
      font-size: 11px;
      font-weight: 820;
      background: #f8fafb;
    }
    tbody tr { background: #fff; }
    tbody tr:hover { background: #f7faf9; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border: 1px solid transparent;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      background: #eef2f5;
      white-space: nowrap;
    }
    .badge.free { color: #415062; background: #edf1f5; border-color: #d6dde5; }
    .badge.pro { color: #0b4c41; background: var(--accent-soft); border-color: #bbdcd2; }
    .badge.max { color: #1f4a83; background: #e8f1ff; border-color: #c8dbf6; }
    .badge.active, .badge.trialing { color: var(--ok); background: var(--ok-soft); border-color: #bde5d2; }
    .badge.revoked, .badge.expired, .badge.canceled {
      color: var(--danger);
      background: var(--danger-soft);
      border-color: #f1c1bb;
    }
    .badge.past_due { color: var(--warn); background: var(--warn-soft); border-color: #f0d38c; }
    .muted { color: var(--muted); }
    .mono { font-family: "Cascadia Mono", "SFMono-Regular", Consolas, monospace; font-size: 12px; }
    .subscription-cell div:first-child { font-weight: 760; }
    .device { display: flex; justify-content: space-between; gap: 8px; padding: 6px 0; border-top: 1px solid #eef1f4; }
    .device:first-child { border-top: 0; }
    .device-label { min-width: 0; overflow-wrap: anywhere; }
    .toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .list-panel { overflow: hidden; }
    .list-body { padding: 16px 16px 0; }
    .renew-panel {
      margin: 0 0 14px;
      border: 1px solid #9db4ad;
      border-radius: 8px;
      background: #f6fbf9;
      overflow: hidden;
    }
    .renew-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid #d5e5df;
      color: var(--accent-dark);
      font-weight: 800;
    }
    .renew-body { padding: 12px; }
    .device-meta {
      display: block;
      margin-top: 3px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }
    .device-risk {
      display: inline-flex;
      margin-top: 4px;
      padding: 2px 6px;
      border-radius: 999px;
      background: var(--warn-soft);
      color: var(--warn);
      font-size: 11px;
      font-weight: 800;
    }
    @media (max-width: 860px) {
      header { padding: 0 16px; }
      .layout { grid-template-columns: 1fr; }
      .row { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      table, thead, tbody, tr, th, td { display: block; }
      thead { display: none; }
      table { min-width: 0; }
      tr { border: 1px solid var(--line); border-radius: 8px; margin: 10px; background: #fff; }
      td { border-bottom: 0; padding: 8px 10px; }
      td::before { content: attr(data-label); display: block; color: var(--muted); font-size: 11px; font-weight: 800; margin-bottom: 3px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-mark">L</div>
      <div class="brand-copy">
        <h1>Lengrvis 激活管理后台</h1>
        <div class="eyebrow">订阅 Key 与设备授权</div>
      </div>
    </div>
    <button id="logout" class="hidden">退出登录</button>
  </header>
  <main>
    <div id="loginShell" class="login-shell">
      <section id="loginPanel" class="panel">
        <div class="panel-title">
          <h2>管理员登录</h2>
          <span>安全会话</span>
        </div>
        <div class="panel-body">
          <label>密码<input id="password" type="password" autocomplete="current-password"></label>
          <div class="actions"><button id="login" class="primary">登录</button></div>
          <div id="loginMessage" class="message"></div>
        </div>
      </section>
    </div>

    <section id="dashboard" class="hidden">
      <div class="layout">
        <section class="panel">
          <div class="panel-title">
            <h2>创建授权 Key</h2>
            <span>一次性显示</span>
          </div>
          <div class="panel-body">
            <label>套餐
              <select id="plan">
                <option value="free">免费版</option>
                <option value="pro" selected>Pro</option>
                <option value="max">Max</option>
              </select>
            </label>
            <label>订阅 ID<input id="subscriptionId" placeholder="sub_customer_001"></label>
            <label>客户标签<input id="subject" placeholder="customer_001"></label>
            <div class="row">
              <label>状态
                <select id="status">
                  <option value="active" selected>生效中</option>
                  <option value="trialing">试用中</option>
                </select>
              </label>
              <label>最大设备数<input id="maxDevices" type="number" min="1" value="1"></label>
            </div>
            <div class="row">
              <label>席位数<input id="seats" type="number" min="1" value="1"></label>
              <label>订单备注<input id="orderRef" placeholder="order-redacted"></label>
            </div>
            <label>有效期
              <select id="expiresPreset">
                <option value="7">7 天试用</option>
                <option value="14">14 天试用</option>
                <option value="30" selected>30 天月付</option>
                <option value="90">90 天季度</option>
                <option value="180">180 天半年</option>
                <option value="365">365 天年付</option>
                <option value="none">长期有效</option>
                <option value="custom">自定义日期</option>
              </select>
            </label>
            <div id="customExpiryWrap" class="hidden">
              <label>自定义到期日期<input id="expiresDate" type="date"></label>
            </div>
            <p id="expiryPreview" class="field-note"></p>
            <label class="checkbox-line">
              <input id="cancelAtPeriodEnd" type="checkbox">
              周期结束后取消，不自动续期
            </label>
            <div class="actions"><button id="createKey" class="primary">创建 Key</button></div>
            <div id="createMessage" class="message"></div>
            <div id="newKeyWrap" class="handoff hidden">
              <div class="handoff-head">
                <span>新授权 Key</span>
                <button id="copyKey" class="compact">复制</button>
              </div>
              <div id="newKey" class="keybox"></div>
            </div>
          </div>
        </section>

        <section class="panel list-panel">
          <div class="panel-title">
            <h2>订阅列表</h2>
            <div class="toolbar"><button id="refresh">刷新</button></div>
          </div>
          <div class="list-body">
            <div class="metrics">
              <div class="metric"><strong id="metricTotal">0</strong><span>订阅总数</span></div>
              <div class="metric"><strong id="metricActive">0</strong><span>可激活</span></div>
              <div class="metric"><strong id="metricPaid">0</strong><span>付费套餐</span></div>
              <div class="metric"><strong id="metricDevices">0</strong><span>已绑设备</span></div>
            </div>
            <div id="listMessage" class="message"></div>
            <div id="renewPanel" class="renew-panel hidden">
              <div class="renew-head">
                <span id="renewTitle">续期订阅</span>
                <button id="cancelRenew" class="compact">取消</button>
              </div>
              <div class="renew-body">
                <div class="row">
                  <label>状态
                    <select id="renewStatus">
                      <option value="active">生效中</option>
                      <option value="trialing">试用中</option>
                      <option value="past_due">逾期</option>
                      <option value="canceled">已取消</option>
                      <option value="expired">已过期</option>
                    </select>
                  </label>
                  <label>有效期
                    <select id="renewExpiresPreset">
                      <option value="30" selected>再续 30 天</option>
                      <option value="90">再续 90 天</option>
                      <option value="180">再续 180 天</option>
                      <option value="365">再续 365 天</option>
                      <option value="none">改为长期有效</option>
                      <option value="custom">自定义日期</option>
                    </select>
                  </label>
                </div>
                <div id="renewCustomExpiryWrap" class="hidden">
                  <label>自定义到期日期<input id="renewExpiresDate" type="date"></label>
                </div>
                <p id="renewExpiryPreview" class="field-note"></p>
                <div class="row">
                  <label>席位数<input id="renewSeats" type="number" min="1" value="1"></label>
                  <label>最大设备数<input id="renewMaxDevices" type="number" min="1" value="1"></label>
                </div>
                <label class="checkbox-line">
                  <input id="renewCancelAtPeriodEnd" type="checkbox">
                  周期结束后取消，不自动续期
                </label>
                <div class="actions">
                  <button id="submitRenew" class="primary">确认续期</button>
                </div>
              </div>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>套餐</th><th>状态</th><th>订阅</th><th>客户标签</th>
                  <th>设备</th><th>时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody id="subscriptions"></tbody>
            </table>
          </div>
        </section>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const state = { items: [], renewItem: null };
    function cookie(name) {
      return document.cookie.split('; ').find(v => v.startsWith(name + '='))?.split('=').slice(1).join('=') || '';
    }
    async function api(path, options = {}) {
      const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
      const csrf = cookie('lengrvis_admin_csrf');
      if (csrf) headers['X-Lengrvis-Admin-Csrf'] = decodeURIComponent(csrf);
      const res = await fetch(path, Object.assign({}, options, {headers}));
      const text = await res.text();
      let body = {};
      try { body = text ? JSON.parse(text) : {}; } catch { body = {detail: text}; }
      if (!res.ok) throw new Error(body?.error?.message || body?.detail || ('HTTP ' + res.status));
      return body;
    }
    function setMessage(id, text, kind = '') {
      const el = $(id);
      el.textContent = text || '';
      el.className = 'message' + (kind ? ' ' + kind : '');
    }
    function addDaysIso(days) {
      const date = new Date(Date.now() + Number(days) * 24 * 60 * 60 * 1000);
      date.setMilliseconds(0);
      return date.toISOString();
    }
    function dateInputToIso(value) {
      if (!value) return null;
      const date = new Date(value + 'T23:59:59.000Z');
      return Number.isNaN(date.getTime()) ? null : date.toISOString();
    }
    function expiryFromControls(selectId, dateId) {
      const preset = $(selectId).value;
      if (preset === 'none') return {expires_at: null, renews_at: null, label: '长期有效'};
      if (preset === 'custom') {
        const expires = dateInputToIso($(dateId).value);
        if (!expires) throw new Error('请选择自定义到期日期。');
        return {expires_at: expires, renews_at: expires, label: formatDate(expires)};
      }
      const days = Number(preset);
      if (!Number.isFinite(days) || days <= 0) throw new Error('有效期选项无效。');
      const expires = addDaysIso(days);
      return {expires_at: expires, renews_at: expires, label: days + ' 天后（' + formatDate(expires) + '）'};
    }
    function updateExpiryPreview(selectId, dateWrapId, dateId, previewId) {
      const custom = $(selectId).value === 'custom';
      $(dateWrapId).classList.toggle('hidden', !custom);
      try {
        const expiry = expiryFromControls(selectId, dateId);
        $(previewId).textContent = '将设置为：' + expiry.label;
      } catch (err) {
        $(previewId).textContent = err.message || '';
      }
    }
    function formatDate(value) {
      if (!value) return '未设置';
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN');
    }
    function showAuthed(authed) {
      $('loginPanel').classList.toggle('hidden', authed);
      $('loginShell').classList.toggle('hidden', authed);
      $('dashboard').classList.toggle('hidden', !authed);
      $('logout').classList.toggle('hidden', !authed);
    }
    async function checkSession() {
      const session = await api('/api/admin/session', {method: 'GET'});
      if (!session.configured) {
        setMessage('loginMessage', '服务器尚未配置管理员认证。', 'error');
        $('login').disabled = true;
      }
      showAuthed(session.authenticated);
      if (session.authenticated) await loadSubscriptions();
    }
    async function login() {
      setMessage('loginMessage', '');
      try {
        await api('/api/admin/login', {method: 'POST', body: JSON.stringify({password: $('password').value})});
        $('password').value = '';
        showAuthed(true);
        await loadSubscriptions();
      } catch (err) {
        setMessage('loginMessage', err.message, 'error');
      }
    }
    async function logout() {
      await api('/api/admin/logout', {method: 'POST'});
      showAuthed(false);
    }
    async function createKey() {
      setMessage('createMessage', '');
      $('newKeyWrap').classList.add('hidden');
      let expiry;
      try {
        expiry = expiryFromControls('expiresPreset', 'expiresDate');
      } catch (err) {
        setMessage('createMessage', err.message, 'error');
        return;
      }
      const payload = {
        plan: $('plan').value,
        subscription_id: $('subscriptionId').value,
        status: $('status').value,
        subject: $('subject').value,
        seats: Number($('seats').value || 1),
        max_devices: Number($('maxDevices').value || 1),
        expires_at: expiry.expires_at,
        renews_at: expiry.renews_at,
        order_ref: $('orderRef').value,
        cancel_at_period_end: $('cancelAtPeriodEnd').checked,
      };
      try {
        const result = await api('/api/admin/subscriptions', {method: 'POST', body: JSON.stringify(payload)});
        $('newKey').textContent = result.activation_key;
        $('newKeyWrap').classList.remove('hidden');
        setMessage('createMessage', 'Key 已创建。这个值只显示一次，请立即保存。', 'ok');
        await loadSubscriptions();
      } catch (err) {
        setMessage('createMessage', err.message, 'error');
      }
    }
    async function loadSubscriptions() {
      setMessage('listMessage', '加载中...');
      try {
        const result = await api('/api/admin/subscriptions', {method: 'GET'});
        state.items = result.items || [];
        renderSubscriptions();
        renderMetrics();
        setMessage('listMessage', state.items.length ? '' : '暂无订阅记录。');
      } catch (err) {
        setMessage('listMessage', err.message, 'error');
      }
    }
    function renderSubscriptions() {
      const tbody = $('subscriptions');
      tbody.textContent = '';
      for (const item of state.items) {
        const tr = document.createElement('tr');
        tr.className = 'row-' + String(item.status || '');
        tr.append(td('套餐', badge(displayPlan(item.plan), item.plan)));
        tr.append(td('状态', badge(displayStatus(item.status), item.status)));
        const subscriptionCell = textBlock([item.subscription_id, item.key_hash_prefix]);
        subscriptionCell.className = 'subscription-cell';
        tr.append(td('订阅', subscriptionCell));
        tr.append(td('客户标签', document.createTextNode(item.subject || '')));
        tr.append(td('设备', devicesCell(item)));
        tr.append(td('时间', textBlock([
          '到期 ' + formatDate(item.expires_at),
          '续费 ' + formatDate(item.renews_at),
          '更新 ' + formatDate(item.updated_at),
        ])));
        tr.append(td('操作', actionsCell(item)));
        tbody.append(tr);
      }
    }
    function renderMetrics() {
      const items = state.items || [];
      const active = items.filter(item => ['active', 'trialing'].includes(String(item.status || ''))).length;
      const paid = items.filter(item => ['pro', 'max'].includes(String(item.plan || ''))).length;
      const devices = items.reduce((sum, item) => sum + Number(item.device_count || 0), 0);
      $('metricTotal').textContent = String(items.length);
      $('metricActive').textContent = String(active);
      $('metricPaid').textContent = String(paid);
      $('metricDevices').textContent = String(devices);
    }
    function displayPlan(plan) {
      const labels = {free: '免费版', pro: 'Pro', max: 'Max'};
      return labels[String(plan || '').toLowerCase()] || plan || '';
    }
    function displayStatus(status) {
      const labels = {
        active: '生效中',
        trialing: '试用中',
        past_due: '逾期',
        canceled: '已取消',
        expired: '已过期',
        revoked: '已撤销',
      };
      return labels[String(status || '').toLowerCase()] || status || '';
    }
    function td(label, child) {
      const cell = document.createElement('td');
      cell.setAttribute('data-label', label);
      cell.append(child);
      return cell;
    }
    function badge(text, cls) {
      const span = document.createElement('span');
      span.className = 'badge ' + String(cls || '');
      span.textContent = text || '';
      return span;
    }
    function textBlock(lines) {
      const box = document.createElement('div');
      for (const line of lines) {
        const div = document.createElement('div');
        div.textContent = line || '';
        if (!line) div.className = 'muted';
        box.append(div);
      }
      return box;
    }
    function devicesCell(item) {
      const box = document.createElement('div');
      const top = document.createElement('div');
      top.textContent = String(item.device_count || 0) + ' / ' + String(item.max_devices || 1);
      box.append(top);
      for (const device of item.devices || []) {
        const row = document.createElement('div');
        row.className = 'device';
        const label = document.createElement('span');
        label.className = 'device-label';
        const title = document.createElement('span');
        title.textContent = device.device_label + (device.app_version ? ' · ' + device.app_version : '');
        label.append(title);
        const meta = document.createElement('small');
        meta.className = 'device-meta';
        const profile = device.device_profile || {};
        const profileText = [profile.os, profile.arch].filter(Boolean).join(' / ');
        meta.textContent = [
          device.device_fingerprint_label ? '指纹 ' + device.device_fingerprint_label : '未提交设备指纹',
          profileText,
          profile.signal_count !== undefined ? '信号 ' + String(profile.signal_count) : '',
        ].filter(Boolean).join(' · ');
        label.append(meta);
        if (device.risk_label === 'legacy_device_id_only') {
          const risk = document.createElement('span');
          risk.className = 'device-risk';
          risk.textContent = '旧版绑定';
          label.append(risk);
        }
        const btn = document.createElement('button');
        btn.textContent = '解绑';
        btn.className = 'compact';
        btn.onclick = () => unbindDevice(device.license_id);
        row.append(label, btn);
        box.append(row);
      }
      return box;
    }
    function actionsCell(item) {
      const box = document.createElement('div');
      box.className = 'actions';
      const renew = document.createElement('button');
      renew.textContent = '续期';
      renew.className = 'compact';
      renew.onclick = () => renewSubscription(item);
      const revoke = document.createElement('button');
      revoke.textContent = '撤销';
      revoke.className = 'danger compact';
      revoke.onclick = () => revokeSubscription(item.key_hash);
      box.append(renew, revoke);
      return box;
    }
    async function copyNewKey() {
      const value = $('newKey').textContent || '';
      if (!value) return;
      try {
        await navigator.clipboard.writeText(value);
        setMessage('createMessage', '已复制到剪贴板。', 'ok');
      } catch {
        setMessage('createMessage', '复制失败，请手动选中 Key。', 'error');
      }
    }
    async function renewSubscription(item) {
      state.renewItem = item;
      $('renewTitle').textContent = '续期订阅 · ' + (item.subscription_id || item.key_hash_prefix || '');
      $('renewStatus').value = item.status || 'active';
      $('renewExpiresPreset').value = '30';
      $('renewExpiresDate').value = '';
      $('renewSeats').value = String(item.seats || 1);
      $('renewMaxDevices').value = String(item.max_devices || 1);
      $('renewCancelAtPeriodEnd').checked = Boolean(item.cancel_at_period_end);
      updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview');
      $('renewPanel').classList.remove('hidden');
      $('renewPanel').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
    function cancelRenew() {
      state.renewItem = null;
      $('renewPanel').classList.add('hidden');
    }
    async function submitRenewal() {
      const item = state.renewItem;
      if (!item) return;
      let expiry;
      try {
        expiry = expiryFromControls('renewExpiresPreset', 'renewExpiresDate');
      } catch (err) {
        setMessage('listMessage', err.message, 'error');
        return;
      }
      try {
        await api('/api/admin/subscriptions/' + item.key_hash + '/renew', {
          method: 'POST',
          body: JSON.stringify({
            status: $('renewStatus').value,
            expires_at: expiry.expires_at,
            renews_at: expiry.renews_at,
            cancel_at_period_end: $('renewCancelAtPeriodEnd').checked,
            seats: Number($('renewSeats').value || item.seats || 1),
            max_devices: Number($('renewMaxDevices').value || item.max_devices || 1),
          }),
        });
        cancelRenew();
        await loadSubscriptions();
      } catch (err) {
        setMessage('listMessage', err.message, 'error');
      }
    }
    async function revokeSubscription(keyHash) {
      if (!confirm('确认撤销这个订阅 Key？')) return;
      try {
        const result = await api('/api/admin/subscriptions/' + keyHash + '/revoke', {method: 'POST'});
        const ids = result?.record?.revoked_license_ids || [];
        await loadSubscriptions();
        if (result?.record?.revocation_manifest_required) {
          setMessage('listMessage', '订阅 Key 已撤销；仍需为 ' + ids.length + ' 个已激活设备发布签名吊销清单或换发许可。', 'error');
        }
      } catch (err) {
        setMessage('listMessage', err.message, 'error');
      }
    }
    async function unbindDevice(licenseId) {
      if (!confirm('确认解绑这个设备？')) return;
      try {
        await api('/api/admin/devices/' + licenseId, {method: 'DELETE'});
        await loadSubscriptions();
      } catch (err) {
        setMessage('listMessage', err.message, 'error');
      }
    }
    $('login').onclick = login;
    $('password').addEventListener('keydown', (event) => { if (event.key === 'Enter') login(); });
    $('logout').onclick = logout;
    $('createKey').onclick = createKey;
    $('copyKey').onclick = copyNewKey;
    $('refresh').onclick = loadSubscriptions;
    $('expiresPreset').onchange = () => updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    $('expiresDate').onchange = () => updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    $('renewExpiresPreset').onchange = () => updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview');
    $('renewExpiresDate').onchange = () => updateExpiryPreview('renewExpiresPreset', 'renewCustomExpiryWrap', 'renewExpiresDate', 'renewExpiryPreview');
    $('cancelRenew').onclick = cancelRenew;
    $('submitRenew').onclick = submitRenewal;
    updateExpiryPreview('expiresPreset', 'customExpiryWrap', 'expiresDate', 'expiryPreview');
    checkSession().catch(err => setMessage('loginMessage', err.message, 'error'));
  </script>
</body>
</html>
"""
