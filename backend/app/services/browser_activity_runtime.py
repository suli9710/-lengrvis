from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import env_flag
from app.core.audit import record
from app.core.outbound_url import pin_outbound_http_url
from app.core.schemas import new_id, now_iso
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.privacy import can_use_browser_network, can_use_browser_writes
from app.policy.risk import RiskLevel, SafetyVerdict
from app.security.pinned_http_proxy import PinnedHttpProxy
from app.services.browser_activity_safety import (
    BROWSER_CONTENT_PROMPT_INJECTION_WARNING as BROWSER_CONTENT_PROMPT_INJECTION_WARNING,
)
from app.services.browser_activity_safety import BROWSER_CONTENT_TRUST as BROWSER_CONTENT_TRUST
from app.services.browser_activity_safety import (
    artifact_ref as _artifact_ref,
)
from app.services.browser_activity_safety import (
    field_attributes_are_sensitive as _field_attributes_are_sensitive,
)
from app.services.browser_activity_safety import (
    redact_event_value as _redact_event_value,
)
from app.services.browser_activity_safety import (
    result_metadata as _result_metadata,
)
from app.services.browser_activity_safety import (
    safe_browser_error as _safe_browser_error,
)
from app.services.browser_activity_safety import (
    safe_result as _safe_result,
)
from app.services.browser_activity_safety import (
    safe_text as _safe_text,
)
from app.services.browser_activity_safety import (
    safe_url as _safe_url,
)
from app.services.browser_activity_safety import (
    sanitize_action as _sanitize_action,
)
from app.services.browser_activity_safety import (
    sensitive_selector as _sensitive_selector,
)
from app.services.browser_activity_safety import (
    sensitive_value as _sensitive_value,
)
from app.tools.tool_abort import raise_if_tool_aborted

BROWSER_ACTION_KINDS = {
    "open",
    "navigate",
    "click",
    "fill",
    "submit",
    "scroll",
    "wait",
    "screenshot",
    "observe",
    "cua",
}
WRITE_ACTION_KINDS = {"click", "fill", "submit", "scroll", "cua"}


def _playwright_error_types() -> tuple[type[BaseException], ...]:
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError:
        return ()
    return (PlaywrightError,)


def _playwright_adapter_error_types() -> tuple[type[BaseException], ...]:
    return (ImportError, *_playwright_error_types())


def _playwright_action_error_types() -> tuple[type[BaseException], ...]:
    return (ImportError, ValueError, *_playwright_error_types())


@dataclass(slots=True)
class BrowserSession:
    id: str = field(default_factory=lambda: new_id("browser_session"))
    task_id: str | None = None
    account_id: str | None = None
    current_url: str = ""
    title: str = ""
    status: str = "active"
    mode: str = "headless"
    allowed_origins: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    paused: bool = False
    takeover: bool = False
    last_observation: dict[str, Any] | None = None


@dataclass(slots=True)
class BrowserActivityEvent:
    id: str = field(default_factory=lambda: new_id("browser_event"))
    session_id: str = ""
    task_id: str | None = None
    account_id: str | None = None
    step_id: str | None = None
    type: str = ""
    action: dict[str, Any] | None = None
    url: str | None = None
    title: str | None = None
    risk_level: str | None = None
    verdict: str | None = None
    ok: bool = True
    error: str | None = None
    screenshot_url: str | None = None
    result: dict[str, Any] | None = None
    created_at: str = field(default_factory=now_iso)


class BrowserActivityAdapter(Protocol):
    def perform(self, session: BrowserSession, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]: ...


class LocalBrowserActivityAdapter:
    """Small local browser adapter used when no external browser host is present."""

    def perform(self, session: BrowserSession, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("kind") or "").lower()
        if kind in {"open", "navigate", "observe"}:
            return self._observe(action, context)
        if kind == "screenshot":
            return self._screenshot(action, context)
        if kind == "wait":
            return self._wait(action, context)
        if kind in {"click", "fill", "submit", "scroll", "cua"}:
            return self._write_like(action, context)
        return {"ok": False, "error": f"Unsupported browser action: {kind}"}

    def _observe(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise_if_tool_aborted(context)
        url = _validate_url(str(action.get("url") or ""))
        max_chars = max(
            1, int(action.get("max_chars") or getattr(_settings(context), "browser_max_page_bytes", 250000))
        )
        route_guard: _PlaywrightRouteGuard | None = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page, route_guard = _new_guarded_playwright_page(browser)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                raise_if_tool_aborted(context)
                _raise_if_playwright_route_guard_blocked(route_guard)
                html = page.content()
                final_url = _validate_final_url(
                    page.url,
                    expected_origin=url,
                    allowed_origins=_browser_allowed_origins(context),
                )
                browser.close()
            data = _extract_page(html, final_url, max_chars)
            data["adapter"] = "playwright"
        except ValueError:
            raise
        except _playwright_adapter_error_types() as exc:
            _raise_if_playwright_route_guard_blocked(route_guard)
            # follow_redirects=False: redirects are followed manually so every
            # hop is re-validated and IP-pinned (no rebinding / redirect SSRF).
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                html, final_url, response_truncated = _read_limited_http_response(
                    client,
                    url,
                    max_chars,
                    abort_context=context,
                    allowed_origins=_browser_allowed_origins(context),
                )
            final_url = _validate_final_url(
                final_url,
                expected_origin=url,
                allowed_origins=_browser_allowed_origins(context),
            )
            data = _extract_page(html, final_url, max_chars)
            data["adapter"] = "httpx"
            data["playwright_error"] = _safe_browser_error(exc)
            if response_truncated:
                data["response_truncated"] = True
        finally:
            if route_guard is not None:
                route_guard.close()
        return data

    def _screenshot(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise_if_tool_aborted(context)
        url = _validate_url(str(action.get("url") or ""))
        out_dir = Path(
            getattr(_settings(context), "browser_screenshot_dir", "")
            or Path.cwd() / ".lengrvis_data" / "browser_screenshots"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        # Unpredictable filename: a URL-derived name (sha256(url)) let any local
        # process that knows the URL compute the path and read a screenshot of a
        # potentially sensitive page. A random token breaks that predictability.
        filename = f"shot-{secrets.token_hex(16)}.png"
        out_path = out_dir / filename
        route_guard: _PlaywrightRouteGuard | None = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page, route_guard = _new_guarded_playwright_page(
                    browser,
                    viewport={"width": int(action.get("width", 1280)), "height": int(action.get("height", 800))},
                )
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                raise_if_tool_aborted(context)
                _raise_if_playwright_route_guard_blocked(route_guard)
                page.screenshot(path=str(out_path), full_page=bool(action.get("full_page", True)))
                _raise_if_playwright_route_guard_blocked(route_guard)
                title = page.title()
                final_url = _validate_final_url(
                    page.url,
                    expected_origin=url,
                    allowed_origins=_browser_allowed_origins(context),
                )
                browser.close()
        except _playwright_action_error_types() as exc:
            route_guard_error = _playwright_route_guard_error(route_guard)
            if route_guard_error:
                return {"ok": False, "error": f"Playwright screenshot failed: {route_guard_error}"}
            return {"ok": False, "error": f"Playwright screenshot failed: {_safe_browser_error(exc)}"}
        finally:
            if route_guard is not None:
                route_guard.close()
        return {"ok": True, "url": final_url, "title": title, "path": str(out_path), "screenshot_url": str(out_path)}

    def _wait(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise_if_tool_aborted(context)
        url = _validate_url(str(action.get("url") or ""))
        selector = str(action.get("selector") or "")
        timeout = int(action.get("timeout_ms") or 10000)
        if not selector:
            return {"ok": False, "error": "selector is required"}
        route_guard: _PlaywrightRouteGuard | None = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page, route_guard = _new_guarded_playwright_page(browser)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                raise_if_tool_aborted(context)
                _raise_if_playwright_route_guard_blocked(route_guard)
                page.wait_for_selector(selector, timeout=timeout)
                _raise_if_playwright_route_guard_blocked(route_guard)
                title = page.title()
                final_url = _validate_final_url(
                    page.url,
                    expected_origin=url,
                    allowed_origins=_browser_allowed_origins(context),
                )
                browser.close()
        except _playwright_action_error_types() as exc:
            route_guard_error = _playwright_route_guard_error(route_guard)
            if route_guard_error:
                return {"ok": False, "error": f"wait_for failed: {route_guard_error}"}
            return {"ok": False, "error": f"wait_for failed: {_safe_browser_error(exc)}"}
        finally:
            if route_guard is not None:
                route_guard.close()
        return {"ok": True, "url": final_url, "title": title, "present": True}

    def _write_like(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raise_if_tool_aborted(context)
        url = _validate_url(str(action.get("url") or ""))
        kind = str(action.get("kind") or "").lower()
        route_guard: _PlaywrightRouteGuard | None = None
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page, route_guard = _new_guarded_playwright_page(browser)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                raise_if_tool_aborted(context)
                _raise_if_playwright_route_guard_blocked(route_guard)
                # Validate the LANDED origin BEFORE any write: goto() may follow
                # a server-side redirect to another origin, and click/fill/submit
                # must never run off-origin. The post-action check below is too
                # late once the side effect (e.g. a form submit) has fired.
                _validate_final_url(
                    page.url,
                    expected_origin=url,
                    allowed_origins=_browser_allowed_origins(context),
                )
                if kind == "click":
                    page.click(str(action.get("selector") or ""), timeout=8000)
                    raise_if_tool_aborted(context)
                    _raise_if_playwright_route_guard_blocked(route_guard)
                elif kind == "fill":
                    fields = action.get("fields") or {}
                    for selector, value in fields.items():
                        raise_if_tool_aborted(context)
                        # Element-semantics guard: block credential/payment/OTP
                        # fields even when reached via a generic selector.
                        if _playwright_field_is_sensitive(page, str(selector)):
                            raise ValueError(
                                "target field is a sensitive credential/payment field; user must fill it manually."
                            )
                        page.fill(str(selector), str(value), timeout=8000)
                        _raise_if_playwright_route_guard_blocked(route_guard)
                elif kind == "submit":
                    selector = str(action.get("selector") or "form")
                    page.evaluate(
                        "(sel) => { const el = document.querySelector(sel); if (el && el.submit) el.submit(); }",
                        selector,
                    )
                    _raise_if_playwright_route_guard_blocked(route_guard)
                elif kind == "scroll":
                    page.evaluate("(y) => window.scrollBy(0, y)", int(action.get("delta_y") or action.get("y") or 500))
                    _raise_if_playwright_route_guard_blocked(route_guard)
                else:
                    return {"ok": False, "error": "CUA actions require a dedicated CUA provider."}
                final_url = _validate_final_url(
                    page.url,
                    expected_origin=url,
                    allowed_origins=_browser_allowed_origins(context),
                )
                title = page.title()
                browser.close()
        except _playwright_action_error_types() as exc:
            route_guard_error = _playwright_route_guard_error(route_guard)
            if route_guard_error:
                return {"ok": False, "error": f"{kind} failed: {route_guard_error}"}
            return {"ok": False, "error": f"{kind} failed: {_safe_browser_error(exc)}"}
        finally:
            if route_guard is not None:
                route_guard.close()
        return {"ok": True, "url": final_url, "title": title, "changed_paths": [], "rollback_info": {}}


@dataclass(slots=True)
class _PlaywrightRouteGuard:
    blocked_error: str | None = None
    proxy: PinnedHttpProxy | None = field(default=None, repr=False)

    def block(self, error: str) -> None:
        if self.blocked_error is None:
            self.blocked_error = str(error or "Blocked outbound browser request")

    def close(self) -> None:
        proxy = self.proxy
        self.proxy = None
        if proxy is not None:
            proxy.close()


def _new_guarded_playwright_page(browser: Any, **context_options: Any) -> tuple[Any, _PlaywrightRouteGuard]:
    """Create a Playwright page whose outbound HTTP(S) requests fail closed."""
    guard = _PlaywrightRouteGuard()
    proxy = PinnedHttpProxy(allow_private=_private_hosts_allowed(), on_block=guard.block).start()
    guard.proxy = proxy
    try:
        context = browser.new_context(
            service_workers="block",
            **context_options,
            proxy={"server": proxy.url, "bypass": "<-loopback>"},
        )
    except Exception:  # noqa: BLE001 - broad-exception-boundary: close the proxy guard before propagating setup failures.
        guard.close()
        raise
    on_event = getattr(context, "on", None)
    if callable(on_event):
        on_event("close", lambda: guard.close())
    context.route("**/*", lambda route: _guard_playwright_route(route, guard))
    return context.new_page(), guard


def _guard_playwright_route(route: Any, guard: _PlaywrightRouteGuard | None = None) -> None:
    request = getattr(route, "request", None)
    url = str(getattr(request, "url", "") or "")
    if urlparse(url).scheme not in {"http", "https"}:
        route.continue_()
        return
    try:
        _validate_url(url)
    except ValueError as exc:
        if guard is not None:
            guard.block(_safe_browser_error(exc))
        try:
            route.abort("blockedbyclient")
        except TypeError:
            route.abort()
        return
    route.continue_()


def _playwright_route_guard_error(guard: _PlaywrightRouteGuard | None) -> str:
    return str(getattr(guard, "blocked_error", "") or "")


def _raise_if_playwright_route_guard_blocked(guard: _PlaywrightRouteGuard | None) -> None:
    error = _playwright_route_guard_error(guard)
    if error:
        raise ValueError(error)


# Long-running processes accumulate sessions and events forever without a
# bound (R7-H1); events keep a rolling window and closed/stale sessions are
# pruned opportunistically on writes.
MAX_RETAINED_EVENTS = 2000
MAX_RETAINED_SESSIONS = 200
CLOSED_SESSION_TTL = timedelta(hours=1)
STALE_SESSION_TTL = timedelta(hours=24)


class BrowserActivityRuntime:
    def __init__(self, adapter: BrowserActivityAdapter | None = None) -> None:
        self.adapter = adapter or LocalBrowserActivityAdapter()
        self._sessions: dict[str, BrowserSession] = {}
        self._events: deque[BrowserActivityEvent] = deque(maxlen=MAX_RETAINED_EVENTS)
        self._lock = threading.RLock()

    def _prune_sessions_locked(self) -> None:
        now = datetime.now(UTC)
        expired: list[str] = []
        for session_id, session in self._sessions.items():
            updated = _parse_iso(session.updated_at)
            if updated is None:
                continue
            ttl = CLOSED_SESSION_TTL if session.status == "closed" else STALE_SESSION_TTL
            if now - updated > ttl:
                expired.append(session_id)
        for session_id in expired:
            self._sessions.pop(session_id, None)
        overflow = len(self._sessions) - MAX_RETAINED_SESSIONS
        if overflow > 0:
            # Evict closed sessions first, then the least recently updated.
            ordered = sorted(
                self._sessions.values(),
                key=lambda item: (item.status != "closed", item.updated_at),
            )
            for session in ordered[:overflow]:
                self._sessions.pop(session.id, None)

    def session_start(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        allowed, reason = _network_allowed(context)
        if not allowed:
            return {"ok": False, "error": reason}
        try:
            task_id, account_id = _requested_session_identity(args, {}, context)
            url = str(args.get("url") or "").strip()
            allowed_origins, allowed_actions = _session_scope(args, context, initial_url=url)
            if url:
                url = _validate_url(url)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc)}
        now = now_iso()
        session = BrowserSession(
            task_id=task_id,
            account_id=account_id,
            current_url=url,
            title="",
            mode=str(args.get("mode") or "headless"),
            allowed_origins=allowed_origins,
            allowed_actions=allowed_actions,
            created_at=now,
            updated_at=now,
            paused=bool(args.get("paused", False)),
            takeover=bool(args.get("takeover", False)),
        )
        with self._lock:
            self._prune_sessions_locked()
            self._sessions[session.id] = session
        event = self._append_event(
            session,
            type="session.start",
            action={"kind": "open", "url": url} if url else {"kind": "open"},
            step_id=_optional_text(args.get("step_id") or context.get("step_id")),
            ok=True,
            risk_level=RiskLevel.R1_OPEN_ONLY,
            verdict=SafetyVerdict.ALLOW,
        )
        return {"ok": True, "session": self._session_dict(session), "event": self._event_dict(event)}

    def session_close(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        try:
            session = self._require_session(str(args.get("session_id") or ""))
            self._bind_or_validate_session_identity(session, args, {}, context, bind_missing=False)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc)}
        session.status = "closed"
        session.updated_at = now_iso()
        event = self._append_event(
            session,
            type="session.close",
            action={"kind": "close"},
            step_id=_optional_text(args.get("step_id") or context.get("step_id")),
            ok=True,
            risk_level=RiskLevel.R0_READ_ONLY,
            verdict=SafetyVerdict.ALLOW,
        )
        return {"ok": True, "session": self._session_dict(session), "event": self._event_dict(event)}

    def cancel_task_sessions(self, task_id: str) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return {"ok": False, "error": "task_id is required", "closed": 0, "session_ids": []}
        with self._lock:
            sessions = [
                session
                for session in self._sessions.values()
                if session.task_id == normalized_task_id and session.status != "closed"
            ]
        closed: list[str] = []
        for session in sessions:
            session.status = "closed"
            session.paused = True
            session.updated_at = now_iso()
            self._append_event(
                session,
                type="session.cancelled",
                action={"kind": "close"},
                ok=True,
                risk_level=RiskLevel.R0_READ_ONLY,
                verdict=SafetyVerdict.ALLOW,
            )
            closed.append(session.id)
        return {"ok": True, "closed": len(closed), "session_ids": closed}

    def session_info(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            session = self._require_session(str(args.get("session_id") or ""))
            self._bind_or_validate_session_identity(session, args, {}, context or {}, bind_missing=False)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc)}
        return {"ok": True, "session": self._session_dict(session)}

    def sessions(self, args: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = args or {}
        effective_context = context or {}
        limit = int(payload.get("limit") or 200)
        try:
            task_id, account_id = _requested_session_identity(payload, {}, effective_context)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc), "sessions": []}
        with self._lock:
            sessions = list(self._sessions.values())
        if task_id:
            sessions = [session for session in sessions if session.task_id == task_id]
        if account_id:
            sessions = [session for session in sessions if session.account_id == account_id]
        sessions = sorted(sessions, key=lambda session: session.updated_at, reverse=True)[: max(1, limit)]
        return {"ok": True, "sessions": [self._session_dict(session) for session in sessions]}

    def events(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str(args.get("session_id") or "").strip()
        effective_context = context or {}
        try:
            task_id, account_id = _requested_session_identity(args, {}, effective_context)
            if session_id:
                session = self._require_session(session_id)
                self._bind_or_validate_session_identity(session, args, {}, effective_context, bind_missing=False)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc), "events": []}
        limit = int(args.get("limit") or 200)
        with self._lock:
            events = list(self._events)
        if session_id:
            events = [event for event in events if event.session_id == session_id]
        if task_id:
            events = [event for event in events if event.task_id == task_id]
        if account_id:
            events = [event for event in events if event.account_id == account_id]
        events = events[-max(1, limit) :]
        return {"ok": True, "events": [self._event_dict(event) for event in events]}

    def observe(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        action = {"kind": "observe"}
        for key in ("url", "max_chars"):
            if key in args:
                action[key] = args[key]
        return self.act({**args, "action": action, "dry_run": False}, context)

    def act(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        action = _normalize_action(args)
        try:
            session = self._session_for_action(args, action, context)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc)}
        kind = str(action.get("kind") or "").lower()
        dry_run = bool(action.get("dry_run", args.get("dry_run", kind in WRITE_ACTION_KINDS)))
        action["dry_run"] = dry_run
        step_id = _optional_text(args.get("step_id") or action.get("step_id") or context.get("step_id"))

        review = self._review_action(action, context)
        if not review["ok"]:
            event = self._append_event(
                session,
                type=f"act.{kind}",
                action=action,
                step_id=step_id,
                ok=False,
                error=review["error"],
                risk_level=review["risk_level"],
                verdict=review["verdict"],
            )
            return {"ok": False, "error": review["error"], "event": self._event_dict(event)}

        if dry_run:
            preview = self._dry_run_preview(action, review["risk_level"], review["verdict"])
            event = self._append_event(
                session,
                type=f"preview.{kind}",
                action=action,
                step_id=step_id,
                ok=True,
                risk_level=review["risk_level"],
                verdict=review["verdict"],
            )
            return {"ok": True, "dry_run": True, **preview, "event": self._event_dict(event)}

        if kind in WRITE_ACTION_KINDS and not _has_approval(args, action):
            error = f"browser.{kind} requires an approved approval_id after dry-run preview."
            event = self._append_event(
                session,
                type=f"act.{kind}",
                action=action,
                step_id=step_id,
                ok=False,
                error=error,
                risk_level=review["risk_level"],
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            )
            return {"ok": False, "error": error, "event": self._event_dict(event)}

        # Defense-in-depth (SEC-002): approved/approval_id in args are not enough.
        # A live write must run through a context validated by the orchestrator
        # (tool_runtime.execute_allowed) or a direct API route that claimed the
        # approval; both stamp the execution marker. This blocks any future caller
        # that reaches the runtime directly with forged approval flags.
        if kind in WRITE_ACTION_KINDS and not execution_is_marked_approved(context):
            error = f"browser.{kind} live execution must run through the validated approval gate."
            event = self._append_event(
                session,
                type=f"act.{kind}",
                action=action,
                step_id=step_id,
                ok=False,
                error=error,
                risk_level=review["risk_level"],
                verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            )
            return {"ok": False, "error": error, "event": self._event_dict(event)}

        try:
            adapter_context = dict(context)
            adapter_context["_browser_allowed_origins"] = tuple(session.allowed_origins)
            result = self.adapter.perform(session, action, adapter_context)
            self._validate_result_scope(session, action, result)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            result = {"ok": False, "error": _safe_browser_error(exc)}
        ok = bool(result.get("ok"))
        self._update_session_from_result(session, result)
        event = self._append_event(
            session,
            type="observe" if kind == "observe" else f"act.{kind}",
            action=action,
            step_id=step_id,
            ok=ok,
            error=str(result.get("error") or "") or None,
            risk_level=review["risk_level"],
            verdict=review["verdict"],
            screenshot_url=result.get("screenshot_url") or result.get("path"),
            result_metadata=_result_metadata(result),
        )
        safe_result = _safe_result(result)
        safe_result["event"] = self._event_dict(event)
        if session.id:
            safe_result.setdefault("session", self._session_dict(session))
        return safe_result

    def replay_export(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str(args.get("session_id") or "").strip()
        if not session_id:
            return {"ok": False, "error": "session_id is required"}
        try:
            session = self._require_session(session_id)
            self._bind_or_validate_session_identity(session, args, {}, context or {}, bind_missing=False)
        except ValueError as exc:
            return {"ok": False, "error": _safe_browser_error(exc)}
        events = self.events(
            {"session_id": session_id, "limit": int(args.get("limit") or 1000)},
            context,
        )["events"]
        return {
            "ok": True,
            "session": self._session_dict(session),
            "events": events,
            "replay": [
                {
                    "type": event.get("type"),
                    "action": event.get("action"),
                    "ok": event.get("ok"),
                    "created_at": event.get("created_at"),
                }
                for event in events
            ],
        }

    def ensure_session(self, args: dict[str, Any], context: dict[str, Any]) -> BrowserSession:
        session_id = str(args.get("session_id") or "").strip()
        if session_id:
            session = self._require_session(session_id)
            self._bind_or_validate_session_identity(session, args, {}, context, bind_missing=True)
            return session
        url = str(args.get("url") or "").strip()
        start_args = dict(args)
        start_args["url"] = url
        start_args.setdefault("mode", "headless")
        started = self.session_start(start_args, context)
        if not started.get("ok"):
            raise ValueError(str(started.get("error") or "Could not start browser session"))
        return self._require_session(str(started["session"]["id"]))

    def _session_for_action(
        self, args: dict[str, Any], action: dict[str, Any], context: dict[str, Any]
    ) -> BrowserSession:
        try:
            session = self.ensure_session({**args, "url": action.get("url") or args.get("url") or ""}, context)
        except ValueError as exc:
            raise exc
        self._bind_or_validate_session_identity(session, args, action, context, bind_missing=True)
        if not action.get("url") and session.current_url:
            action["url"] = session.current_url
        if action.get("url"):
            action["url"] = str(action.get("url") or "").strip()
        self._validate_action_scope(session, action)
        if action.get("url"):
            action["url"] = _validate_url(str(action.get("url") or ""))
            self._bind_validated_action_origin(session, str(action["url"]))
        return session

    def _review_action(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        kind = str(action.get("kind") or "").lower()
        if kind not in BROWSER_ACTION_KINDS:
            return {
                "ok": False,
                "error": f"Unsupported browser action: {kind}",
                "risk_level": RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                "verdict": SafetyVerdict.DENY,
            }
        network_allowed, network_reason = _network_allowed(context)
        if not network_allowed:
            return {
                "ok": False,
                "error": network_reason,
                "risk_level": RiskLevel.R1_OPEN_ONLY,
                "verdict": SafetyVerdict.DENY,
            }
        if kind in WRITE_ACTION_KINDS:
            write_allowed, write_reason = _writes_allowed(context)
            if not write_allowed:
                return {
                    "ok": False,
                    "error": write_reason,
                    "risk_level": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
                    "verdict": SafetyVerdict.DENY,
                }
            sensitive = _sensitive_action_error(action)
            if sensitive:
                return {
                    "ok": False,
                    "error": sensitive,
                    "risk_level": RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                    "verdict": SafetyVerdict.DENY,
                }
        return {
            "ok": True,
            "risk_level": _risk_for_action(kind),
            "verdict": SafetyVerdict.NEEDS_USER_APPROVAL if kind in WRITE_ACTION_KINDS else SafetyVerdict.ALLOW,
        }

    def _dry_run_preview(self, action: dict[str, Any], risk_level: RiskLevel, verdict: SafetyVerdict) -> dict[str, Any]:
        safe_action = _sanitize_action(action)
        return {
            "risk_level": risk_level.value if hasattr(risk_level, "value") else str(risk_level),
            "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
            "diff_preview": [
                {"action": safe_action.get("kind"), **{k: v for k, v in safe_action.items() if k != "kind"}}
            ],
        }

    def _require_session(self, session_id: str) -> BrowserSession:
        if not session_id:
            raise ValueError("session_id is required")
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown browser session: {session_id}")
        return session

    def _bind_or_validate_session_identity(
        self,
        session: BrowserSession,
        args: dict[str, Any],
        action: dict[str, Any],
        context: dict[str, Any],
        *,
        bind_missing: bool,
    ) -> None:
        task_id, account_id = _requested_session_identity(args, action, context)
        with self._lock:
            if session.task_id and task_id and session.task_id != task_id:
                raise ValueError("Browser session is bound to a different task.")
            if session.account_id and account_id and session.account_id != account_id:
                raise ValueError("Browser session is bound to a different account.")
            if bind_missing and not session.task_id and task_id:
                session.task_id = task_id
            if bind_missing and not session.account_id and account_id:
                session.account_id = account_id

    def _validate_action_scope(self, session: BrowserSession, action: dict[str, Any]) -> None:
        if session.status == "closed":
            raise ValueError("Browser session is closed.")
        kind = str(action.get("kind") or "").strip().casefold()
        if kind not in session.allowed_actions:
            raise ValueError(f"Browser action '{kind or 'unknown'}' is outside this session's allowed_actions.")
        url = str(action.get("url") or "").strip()
        if not url:
            return
        origin = _url_origin(url)
        with self._lock:
            if session.allowed_origins and origin not in session.allowed_origins:
                raise ValueError("Browser URL origin is outside this session's allowed_origins.")

    def _bind_validated_action_origin(self, session: BrowserSession, url: str) -> None:
        """Bind a URL-less session only after the candidate URL passes SSRF validation."""

        origin = _url_origin(url)
        with self._lock:
            # Re-check under the same lock used for the one-time binding so two
            # concurrent first actions cannot bind the session to different origins.
            if session.allowed_origins and origin not in session.allowed_origins:
                raise ValueError("Browser URL origin is outside this session's allowed_origins.")
            if not session.allowed_origins:
                # A session created without an initial URL is bound exactly once,
                # on its first URL-bearing action.
                session.allowed_origins = [origin]

    def _validate_result_scope(
        self,
        session: BrowserSession,
        action: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        result_url = str(result.get("url") or result.get("final_url") or "").strip()
        if not result_url:
            return
        expected_origin = str(action.get("url") or session.current_url or "") or None
        final_url = _validate_final_url(
            result_url,
            expected_origin=expected_origin,
            allowed_origins=session.allowed_origins,
        )
        result["url"] = final_url
        if result.get("final_url"):
            result["final_url"] = final_url
        if not session.allowed_origins:
            with self._lock:
                session.allowed_origins = [_url_origin(final_url)]

    def _update_session_from_result(self, session: BrowserSession, result: dict[str, Any]) -> None:
        if result.get("url"):
            session.current_url = str(result.get("url") or "")
        if result.get("title") is not None:
            session.title = str(result.get("title") or "")
        if result.get("ok") and any(key in result for key in ("text", "links", "screenshot_url", "path")):
            session.last_observation = _redact_event_value(
                {
                    "url": result.get("url") or session.current_url,
                    "title": result.get("title") or session.title,
                    "text": result.get("text") or "",
                    "links": result.get("links") or [],
                    "screenshot_url": result.get("screenshot_url") or result.get("path") or "",
                }
            )
        session.updated_at = now_iso()

    def _append_event(
        self,
        session: BrowserSession,
        *,
        type: str,
        action: dict[str, Any] | None = None,
        step_id: str | None = None,
        ok: bool,
        error: str | None = None,
        risk_level: RiskLevel | str | None = None,
        verdict: SafetyVerdict | str | None = None,
        screenshot_url: str | None = None,
        result_metadata: dict[str, Any] | None = None,
    ) -> BrowserActivityEvent:
        event = BrowserActivityEvent(
            session_id=session.id,
            task_id=session.task_id,
            account_id=session.account_id,
            step_id=step_id,
            type=type,
            action=_sanitize_action(action or {}) if action is not None else None,
            url=_safe_url(session.current_url) if session.current_url else None,
            title=_safe_text(session.title) if session.title else None,
            risk_level=_enum_text(risk_level),
            verdict=_enum_text(verdict),
            ok=ok,
            error=_safe_text(error or "") if error else None,
            screenshot_url=_artifact_ref(screenshot_url) if screenshot_url else None,
            result=_redact_event_value(result_metadata) if result_metadata else None,
        )
        with self._lock:
            self._events.append(event)
        self._record_audit(event, result_metadata=result_metadata)
        return event

    def _record_audit(self, event: BrowserActivityEvent, *, result_metadata: dict[str, Any] | None = None) -> None:
        try:
            payload = self._event_dict(event)
            if result_metadata:
                payload["result"] = _redact_event_value(result_metadata)
            record(
                f"browser_activity.{event.type}",
                "BrowserActivityRuntime",
                payload,
                task_id=event.task_id,
            )
        except Exception:  # noqa: BLE001 - broad-exception-boundary
            return

    def _session_dict(self, session: BrowserSession) -> dict[str, Any]:
        data = asdict(session)
        data["current_url"] = _safe_url(data.get("current_url") or "")
        data["title"] = _safe_text(data.get("title") or "")
        data["last_observation"] = _redact_event_value(data.get("last_observation"))
        return data

    def _event_dict(self, event: BrowserActivityEvent) -> dict[str, Any]:
        return _redact_event_value(asdict(event))


def _settings(context: dict[str, Any]):
    return context["settings"]


_TASK_ID_KEYS = ("task_id", "taskId", "browser_task_id")
_ACCOUNT_ID_KEYS = (
    "account_id",
    "accountId",
    "browser_account_id",
    "account",
    "principal_id",
    "principalId",
    "user_id",
    "userId",
    "tenant_id",
    "tenantId",
)
_ORIGIN_SCOPE_KEYS = ("allowed_origins", "browser_allowed_origins")
_ACTION_SCOPE_KEYS = ("allowed_actions", "browser_allowed_actions")


def _optional_identity(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mapping_values(mapping: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        if key in mapping:
            value = _optional_identity(mapping.get(key))
            if value:
                values.append(value)
    return values


def _coherent_identity(*mappings: dict[str, Any], keys: tuple[str, ...], label: str) -> str | None:
    values = _mapping_values(mappings[0], keys) if mappings else []
    for mapping in mappings[1:]:
        values.extend(_mapping_values(mapping, keys))
    unique = {value for value in values}
    if len(unique) > 1:
        raise ValueError(f"Conflicting {label} values cannot be used for one browser session.")
    return next(iter(unique), None)


def _requested_session_identity(
    args: dict[str, Any],
    action: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str | None, str | None]:
    return (
        _coherent_identity(args, action, context, keys=_TASK_ID_KEYS, label="task_id"),
        _coherent_identity(args, action, context, keys=_ACCOUNT_ID_KEYS, label="account_id"),
    )


def _scope_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, list | tuple | set | frozenset):
        raw = [str(part).strip() for part in value]
    else:
        raise ValueError("Browser session scope values must be strings or lists of strings.")
    return [part for part in raw if part]


def _scope_from_mapping(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, list[str]]:
    for key in keys:
        if key in mapping:
            return True, _scope_values(mapping.get(key))
    return False, []


def _normalise_origin_scope(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        origin = _url_origin(value)
        if origin not in normalized:
            normalized.append(origin)
    return normalized


def _normalise_action_scope(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        action = _canonical_action_kind(value)
        if action not in BROWSER_ACTION_KINDS:
            raise ValueError(f"Unsupported browser action in allowed_actions: {value}")
        if action not in normalized:
            normalized.append(action)
    return normalized


def _session_scope(
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    initial_url: str = "",
) -> tuple[list[str], list[str]]:
    context_has_origins, context_origins_raw = _scope_from_mapping(context, _ORIGIN_SCOPE_KEYS)
    args_has_origins, args_origins_raw = _scope_from_mapping(args, _ORIGIN_SCOPE_KEYS)
    context_origins = _normalise_origin_scope(context_origins_raw)
    args_origins = _normalise_origin_scope(args_origins_raw)
    if context_has_origins and args_has_origins and not set(args_origins).issubset(context_origins):
        raise ValueError("Browser session allowed_origins cannot exceed the task scope.")
    allowed_origins = args_origins if args_has_origins else context_origins

    if initial_url:
        initial_origin = _url_origin(initial_url)
        if allowed_origins and initial_origin not in allowed_origins:
            raise ValueError("Initial browser URL is outside allowed_origins.")
        if not allowed_origins:
            allowed_origins = [initial_origin]

    context_has_actions, context_actions_raw = _scope_from_mapping(context, _ACTION_SCOPE_KEYS)
    args_has_actions, args_actions_raw = _scope_from_mapping(args, _ACTION_SCOPE_KEYS)
    context_actions = _normalise_action_scope(context_actions_raw)
    args_actions = _normalise_action_scope(args_actions_raw)
    if context_has_actions and args_has_actions and not set(args_actions).issubset(context_actions):
        raise ValueError("Browser session allowed_actions cannot exceed the task scope.")
    allowed_actions = args_actions if args_has_actions else context_actions
    if not allowed_actions and not (context_has_actions or args_has_actions):
        allowed_actions = sorted(BROWSER_ACTION_KINDS)
    return allowed_origins, allowed_actions


def _canonical_action_kind(value: Any) -> str:
    action = str(value or "").casefold().replace("_", "-").strip()
    if action.startswith("browser."):
        action = action.removeprefix("browser.")
    return {
        "read-page": "observe",
        "open-url": "open",
        "wait-for-selector": "wait",
        "click-element": "click",
        "fill-form": "fill",
        "submit-form": "submit",
        "cua-run": "cua",
    }.get(action, action)


def _url_origin(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser origin must be an absolute http(s) URL without credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Browser origin contains an invalid port.") from exc
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    hostname = parsed.hostname.casefold().rstrip(".")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{parsed.scheme.casefold()}://{hostname}:{port}"


def _browser_allowed_origins(context: dict[str, Any]) -> tuple[str, ...]:
    raw = context.get("_browser_allowed_origins")
    if raw is None:
        return ()
    try:
        return tuple(_normalise_origin_scope(_scope_values(raw)))
    except ValueError:
        return ()


def _network_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_network(_settings(context))
    return decision.allowed, decision.reason


def _writes_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_writes(_settings(context))
    return decision.allowed, decision.reason


def _read_limited_http_response(
    client: httpx.Client,
    url: str,
    max_bytes: int,
    *,
    max_redirects: int = 5,
    abort_context: dict[str, Any] | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, str, bool]:
    limit = max(1, int(max_bytes or 1))
    allow_private = _private_hosts_allowed()
    current = str(url or "")
    expected_origin = _url_origin(current)
    for _ in range(max_redirects + 1):
        raise_if_tool_aborted(abort_context)
        # Re-validate every hop, then connect to the exact IP we just validated
        # (Host header + SNI restore the name) so a rebinding answer or a
        # redirect to an internal host cannot be reached.
        current = _validate_final_url(
            current,
            expected_origin=expected_origin,
            allowed_origins=allowed_origins,
        )
        pinned = pin_outbound_http_url(current, allow_private=allow_private)
        headers = {"User-Agent": "LengrvisAgent/0.1", **pinned.headers}
        with client.stream("GET", pinned.url, headers=headers, extensions=dict(pinned.extensions)) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                    raise ValueError("Redirect response did not include a Location header.")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            chunks = bytearray()
            truncated = False
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > limit:
                truncated = True
            for chunk in response.iter_bytes():
                raise_if_tool_aborted(abort_context)
                if not chunk:
                    continue
                remaining = limit - len(chunks)
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    chunks.extend(chunk[:remaining])
                    truncated = True
                    break
                chunks.extend(chunk)
            encoding = response.encoding or "utf-8"
            return bytes(chunks).decode(encoding, errors="replace"), current, truncated
    raise ValueError("Too many redirects while fetching the page.")


ALLOW_PRIVATE_HOSTS_ENV = "LENGRVIS_BROWSER_ALLOW_PRIVATE_HOSTS"
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".intranet")


def _validate_final_url(
    url: str,
    *,
    expected_origin: str | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
) -> str:
    candidate = str(url or "").strip()
    # Enforce the task/session origin boundary before DNS-based SSRF checks. This
    # keeps authorization deterministic without weakening the subsequent SSRF gate.
    actual_origin = _url_origin(candidate)
    if expected_origin and actual_origin != _url_origin(expected_origin):
        raise ValueError("Browser redirect/final URL must remain same-origin.")
    if allowed_origins:
        normalized_allowed = set(_normalise_origin_scope(_scope_values(allowed_origins)))
        if actual_origin not in normalized_allowed:
            raise ValueError("Browser URL origin is outside allowed_origins.")
    return _validate_url(candidate)


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    hostname = parsed.hostname or ""
    if not _private_hosts_allowed() and _is_private_host(hostname):
        raise ValueError(
            "URLs targeting loopback, private, or link-local hosts are blocked to prevent SSRF. "
            f"Set {ALLOW_PRIVATE_HOSTS_ENV}=1 to explicitly allow LAN browsing."
        )
    return url


def _private_hosts_allowed() -> bool:
    return env_flag(ALLOW_PRIVATE_HOSTS_ENV)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _is_private_host(hostname: str) -> bool:
    if not hostname:
        return True
    lowered = hostname.lower().rstrip(".")
    try:
        return _is_blocked_ip(ipaddress.ip_address(lowered.split("%")[0]))
    except ValueError:
        pass
    if lowered == "localhost" or lowered.endswith(_LOCAL_HOST_SUFFIXES) or "." not in lowered:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        # Unresolvable hosts fail later at connection time with a clearer error.
        return False
    for info in infos:
        addr = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            return True
    return False


def _extract_page(html: str, url: str, max_chars: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:max_chars]
    links = []
    for anchor in soup.find_all("a", href=True)[:80]:
        label = anchor.get_text(" ", strip=True)[:120]
        href = urljoin(url, str(anchor.get("href")))
        if href.startswith(("http://", "https://")):
            links.append({"title": label or href, "url": href})
    return {"ok": True, "url": url, "title": title, "text": text, "links": links, "truncated": len(text) >= max_chars}


def _normalize_action(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("action")
    if isinstance(raw, dict):
        action = dict(raw)
    else:
        action = {}
    if not action.get("kind"):
        action["kind"] = str(args.get("kind") or "").lower()
    action["kind"] = _canonical_action_kind(action.get("kind"))
    for key in (
        "url",
        "selector",
        "text",
        "fields",
        "dry_run",
        "approved",
        "approval_id",
        "max_chars",
        "timeout_ms",
        "width",
        "height",
        "full_page",
        "delta_y",
        "y",
    ):
        if key in args and key not in action:
            action[key] = args[key]
    return action


def _risk_for_action(kind: str) -> RiskLevel:
    if kind in {"observe", "screenshot", "wait"}:
        return RiskLevel.R0_READ_ONLY
    if kind in {"open", "navigate"}:
        return RiskLevel.R1_OPEN_ONLY
    if kind in {"click", "fill", "scroll"}:
        return RiskLevel.R2_REVERSIBLE_MODIFY
    if kind in {"submit", "cua"}:
        return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    return RiskLevel.R4_FORBIDDEN_OR_HANDOFF


def _has_approval(args: dict[str, Any], action: dict[str, Any]) -> bool:
    return bool(
        (args.get("approved") or action.get("approved")) and (args.get("approval_id") or action.get("approval_id"))
    )


def _sensitive_action_error(action: dict[str, Any]) -> str:
    selector = str(action.get("selector") or "")
    if selector and _sensitive_selector(selector):
        return "selector looks sensitive; user must complete this browser action manually."
    fields = action.get("fields") or {}
    if isinstance(fields, dict):
        for key, value in fields.items():
            if _sensitive_selector(str(key)):
                return "field selector looks sensitive; user must fill it manually."
            if _sensitive_value(value):
                return "field value looks sensitive; user must fill it manually."
    if action.get("text") and _sensitive_value(action.get("text")):
        return "text looks sensitive; user must enter it manually."
    return ""


def _playwright_field_is_sensitive(page: Any, selector: str) -> bool:
    try:
        attrs = {
            key: page.get_attribute(selector, key, timeout=4000)
            for key in ("type", "autocomplete", "name", "id", "aria-label", "placeholder")
        }
    except _playwright_error_types():
        return False
    return _field_attributes_are_sensitive(attrs)


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
