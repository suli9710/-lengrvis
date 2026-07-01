from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePath
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import env_flag
from app.core.audit import record
from app.core.outbound_url import pin_outbound_http_url
from app.core.schemas import new_id, now_iso
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.policy_rules import (
    BROWSER_CONTENT_PROMPT_INJECTION_WARNING,
    BROWSER_CONTENT_TRUST,
    BROWSER_PROMPT_INJECTION_PATTERNS,
)
from app.policy.privacy import can_use_browser_network, can_use_browser_writes
from app.policy.redaction import REDACTED, contains_sensitive_key, redact_public_text, redact_text, redact_value
from app.policy.risk import RiskLevel, SafetyVerdict
from app.policy.sensitive_values import looks_sensitive_value

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
SENSITIVE_SELECTOR_TOKENS = {
    "password",
    "pwd",
    "passwd",
    "credit",
    "card",
    "cvv",
    "cvc",
    "ssn",
    "payment",
    "pay",
    "order",
    "delete",
    "token",
    "cookie",
    "otp",
    "2fa",
    "passcode",
    "auth",
    "credential",
}
@dataclass(slots=True)
class BrowserSession:
    id: str = field(default_factory=lambda: new_id("browser_session"))
    task_id: str | None = None
    current_url: str = ""
    title: str = ""
    status: str = "active"
    mode: str = "headless"
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
        url = _validate_url(str(action.get("url") or ""))
        max_chars = max(
            1, int(action.get("max_chars") or getattr(_settings(context), "browser_max_page_bytes", 250000))
        )
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                html = page.content()
                final_url = _validate_final_url(page.url)
                browser.close()
            data = _extract_page(html, final_url, max_chars)
            data["adapter"] = "playwright"
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            # follow_redirects=False: redirects are followed manually so every
            # hop is re-validated and IP-pinned (no rebinding / redirect SSRF).
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                html, final_url, response_truncated = _read_limited_http_response(client, url, max_chars)
            final_url = _validate_final_url(final_url)
            data = _extract_page(html, final_url, max_chars)
            data["adapter"] = "httpx"
            data["playwright_error"] = str(exc)
            if response_truncated:
                data["response_truncated"] = True
        return data

    def _screenshot(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        url = _validate_url(str(action.get("url") or ""))
        out_dir = Path(
            getattr(_settings(context), "browser_screenshot_dir", "")
            or Path.cwd() / ".lengrvis_data" / "browser_screenshots"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".png"
        out_path = out_dir / filename
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    viewport={"width": int(action.get("width", 1280)), "height": int(action.get("height", 800))}
                )
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.screenshot(path=str(out_path), full_page=bool(action.get("full_page", True)))
                title = page.title()
                final_url = _validate_final_url(page.url)
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Playwright screenshot failed: {exc}"}
        return {"ok": True, "url": final_url, "title": title, "path": str(out_path), "screenshot_url": str(out_path)}

    def _wait(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        url = _validate_url(str(action.get("url") or ""))
        selector = str(action.get("selector") or "")
        timeout = int(action.get("timeout_ms") or 10000)
        if not selector:
            return {"ok": False, "error": "selector is required"}
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector(selector, timeout=timeout)
                title = page.title()
                final_url = _validate_final_url(page.url)
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"wait_for failed: {exc}"}
        return {"ok": True, "url": final_url, "title": title, "present": True}

    def _write_like(self, action: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        url = _validate_url(str(action.get("url") or ""))
        kind = str(action.get("kind") or "").lower()
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if kind == "click":
                    page.click(str(action.get("selector") or ""), timeout=8000)
                elif kind == "fill":
                    fields = action.get("fields") or {}
                    for selector, value in fields.items():
                        # Element-semantics guard: block credential/payment/OTP
                        # fields even when reached via a generic selector.
                        if _playwright_field_is_sensitive(page, str(selector)):
                            raise ValueError(
                                "target field is a sensitive credential/payment field; user must fill it manually."
                            )
                        page.fill(str(selector), str(value), timeout=8000)
                elif kind == "submit":
                    selector = str(action.get("selector") or "form")
                    page.evaluate(
                        "(sel) => { const el = document.querySelector(sel); if (el && el.submit) el.submit(); }",
                        selector,
                    )
                elif kind == "scroll":
                    page.evaluate("(y) => window.scrollBy(0, y)", int(action.get("delta_y") or action.get("y") or 500))
                else:
                    return {"ok": False, "error": "CUA actions require a dedicated CUA provider."}
                final_url = _validate_final_url(page.url)
                title = page.title()
                browser.close()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{kind} failed: {exc}"}
        return {"ok": True, "url": final_url, "title": title, "changed_paths": [], "rollback_info": {}}


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
        url = str(args.get("url") or "").strip()
        if url:
            url = _validate_url(url)
        now = now_iso()
        session = BrowserSession(
            task_id=_optional_text(args.get("task_id") or context.get("task_id")),
            current_url=url,
            title="",
            mode=str(args.get("mode") or "headless"),
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
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
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

    def session_info(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            session = self._require_session(str(args.get("session_id") or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "session": self._session_dict(session)}

    def sessions(self, args: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        limit = int((args or {}).get("limit") or 200)
        with self._lock:
            sessions = list(self._sessions.values())
        sessions = sorted(sessions, key=lambda session: session.updated_at, reverse=True)[: max(1, limit)]
        return {"ok": True, "sessions": [self._session_dict(session) for session in sessions]}

    def events(self, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        session_id = str(args.get("session_id") or "").strip()
        task_id = str(args.get("task_id") or "").strip()
        limit = int(args.get("limit") or 200)
        with self._lock:
            events = list(self._events)
        if session_id:
            events = [event for event in events if event.session_id == session_id]
        if task_id:
            events = [event for event in events if event.task_id == task_id]
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
            return {"ok": False, "error": str(exc)}
        kind = str(action.get("kind") or "").lower()
        dry_run = bool(action.get("dry_run", args.get("dry_run", kind in WRITE_ACTION_KINDS)))
        action["dry_run"] = dry_run
        task_id = _optional_text(
            args.get("task_id") or action.get("task_id") or session.task_id or context.get("task_id")
        )
        if task_id:
            session.task_id = task_id
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
            result = self.adapter.perform(session, action, context)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
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
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        events = self.events({"session_id": session_id, "limit": int(args.get("limit") or 1000)})["events"]
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
            return self._require_session(session_id)
        url = str(args.get("url") or "").strip()
        if url:
            url = _validate_url(url)
        started = self.session_start(
            {
                "task_id": args.get("task_id") or context.get("task_id"),
                "step_id": args.get("step_id") or context.get("step_id"),
                "url": url,
                "mode": args.get("mode") or "headless",
            },
            context,
        )
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
        if not action.get("url") and session.current_url:
            action["url"] = session.current_url
        if action.get("url"):
            action["url"] = _validate_url(str(action.get("url") or ""))
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
        except Exception:  # noqa: BLE001
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


def _network_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_network(_settings(context))
    return decision.allowed, decision.reason


def _writes_allowed(context: dict[str, Any]) -> tuple[bool, str]:
    decision = can_use_browser_writes(_settings(context))
    return decision.allowed, decision.reason


def _read_limited_http_response(
    client: httpx.Client, url: str, max_bytes: int, *, max_redirects: int = 5
) -> tuple[str, str, bool]:
    limit = max(1, int(max_bytes or 1))
    allow_private = _private_hosts_allowed()
    current = str(url or "")
    for _ in range(max_redirects + 1):
        # Re-validate every hop, then connect to the exact IP we just validated
        # (Host header + SNI restore the name) so a rebinding answer or a
        # redirect to an internal host cannot be reached.
        current = _validate_url(current)
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


def _validate_final_url(url: str) -> str:
    return _validate_url(str(url or ""))


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


# RFC 2544 benchmarking range, used as the fake-IP pool by local tunneling
# proxies (Clash/mihomo/sing-box). When DNS answers land here the connection
# actually goes through the proxy to the public site, so blocking it would
# break every domain on such machines. Literal fake-IP URLs stay blocked.
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


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
        if isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_NETWORK:
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
    action["kind"] = str(action.get("kind") or "").lower()
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


# Autocomplete hints (WHATWG) for credential / payment / OTP fields.
SENSITIVE_AUTOCOMPLETE_TOKENS = {
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "cc-name",
}


def _field_attributes_are_sensitive(attrs: dict[str, Any]) -> bool:
    """Decide field sensitivity from element semantics, not the selector string.

    A generic selector (e.g. ``#f1``) can still target a password/payment/OTP
    input, so inspect the resolved element's ``type``/``autocomplete`` and
    descriptive attributes instead of trusting the caller-supplied selector text.
    """
    if str(attrs.get("type") or "").strip().lower() == "password":
        return True
    autocomplete = str(attrs.get("autocomplete") or "").lower().replace(",", " ")
    if {token.strip() for token in autocomplete.split()} & SENSITIVE_AUTOCOMPLETE_TOKENS:
        return True
    for key in ("name", "id", "aria-label", "placeholder"):
        if _sensitive_selector(str(attrs.get(key) or "")):
            return True
    return False


def _playwright_field_is_sensitive(page: Any, selector: str) -> bool:
    try:
        attrs = {
            key: page.get_attribute(selector, key, timeout=4000)
            for key in ("type", "autocomplete", "name", "id", "aria-label", "placeholder")
        }
    except Exception:  # noqa: BLE001 - element missing/un-introspectable; let fill surface its own error.
        return False
    return _field_attributes_are_sensitive(attrs)


def _sensitive_selector(selector: str) -> bool:
    lowered = selector.lower()
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS)


def _sensitive_value(value: Any) -> bool:
    lowered = str(value or "").lower()
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS) or looks_sensitive_value(value)


def _sanitize_action(action: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in action.items():
        if key == "url":
            safe[key] = _safe_url(str(value or ""))
        elif key in {"selector", "text", "fields", "approval_id"}:
            safe[key] = REDACTED if value not in (None, "", {}) else value
        elif key in {
            "approved",
            "dry_run",
            "kind",
            "max_chars",
            "timeout_ms",
            "width",
            "height",
            "full_page",
            "delta_y",
            "y",
        }:
            safe[key] = value
        else:
            safe[key] = _redact_event_value(value)
    return safe


def _result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"ok": bool(result.get("ok"))}
    for key in ("url", "title", "adapter", "present", "changed_paths", "rollback_info", "screenshot_url", "path"):
        if key in result:
            metadata[key] = result[key]
    if result.get("error"):
        metadata["error"] = result["error"]
    if result.get("text") is not None:
        metadata["text_chars"] = len(str(result.get("text") or ""))
    if isinstance(result.get("links"), list):
        metadata["link_count"] = len(result.get("links") or [])
    if _has_browser_content(result):
        metadata["content_trust"] = BROWSER_CONTENT_TRUST
        warnings = _browser_content_warnings(result)
        if warnings:
            metadata["browser_content_warnings"] = warnings
    return metadata


def _safe_result(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for item_key, item in value.items():
            text_key = str(item_key)
            if text_key == "url":
                result[text_key] = _safe_url(str(item or ""))
            elif text_key in {"path", "screenshot_url"}:
                result[text_key] = _artifact_ref(item)
            elif text_key in {"title", "error"}:
                result[text_key] = _safe_text(str(item or ""))
            else:
                result[text_key] = _safe_result(item, key=text_key)
        if _has_browser_content(value):
            result["content_trust"] = BROWSER_CONTENT_TRUST
            warnings = _browser_content_warnings(value)
            if warnings:
                result["browser_content_warnings"] = warnings
        return result
    if isinstance(value, list):
        return [_safe_result(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_safe_result(item, key=key) for item in value]
    if isinstance(value, str):
        if key == "text":
            return value
        return _safe_text(value)
    return value


def _has_browser_content(result: dict[str, Any]) -> bool:
    return result.get("text") is not None or isinstance(result.get("links"), list)


def _browser_content_warnings(result: dict[str, Any]) -> list[str]:
    inspected_parts: list[str] = []
    if result.get("text") is not None:
        inspected_parts.append(str(result.get("text") or ""))
    if result.get("title") is not None:
        inspected_parts.append(str(result.get("title") or ""))
    for link in result.get("links") or []:
        if isinstance(link, dict):
            inspected_parts.append(str(link.get("title") or ""))
            inspected_parts.append(str(link.get("url") or ""))
    inspected = "\n".join(inspected_parts)
    if any(re.search(pattern, inspected, flags=re.IGNORECASE) for pattern in BROWSER_PROMPT_INJECTION_PATTERNS):
        return [BROWSER_CONTENT_PROMPT_INJECTION_WARNING]
    return []


def _redact_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key == "url":
                result[key] = _safe_url(str(item or ""))
            elif text_key in {"path", "screenshot_url"}:
                result[key] = _artifact_ref(item)
            elif text_key in {"content_trust", "browser_content_warnings"}:
                result[key] = _safe_metadata_label_value(item)
            else:
                if text_key in {"text", "selector", "fields"}:
                    result[key] = REDACTED if item not in (None, "", {}) else item
                elif contains_sensitive_key(text_key):
                    result[key] = _redact_event_value(redact_value({text_key: item}).get(text_key))
                else:
                    result[key] = _redact_event_value(item)
        return result
    if isinstance(value, list):
        return [_redact_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_event_value(item) for item in value]
    redacted = redact_value(value)
    if isinstance(redacted, str):
        return _safe_text(redacted)
    return redacted


def _safe_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(redact_text(url))
    except Exception:  # noqa: BLE001
        return redact_text(url)
    if not parsed.query:
        return redact_text(url)
    return parsed._replace(query=REDACTED).geturl()


def _artifact_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    candidate = parsed.path if parsed.scheme else text.split("?", 1)[0].split("#", 1)[0]
    return redact_text(PurePath(candidate.replace("\\", "/")).name)


def _safe_metadata_label_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_safe_metadata_label_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_metadata_label_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_metadata_label_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return redact_text(value, redact_generic_tokens=False)
    return value


def _safe_text(text: str) -> str:
    return redact_public_text(text) if text else ""


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
