"""Short-lived exact browser commands issued only from a validated tool context."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.orchestration.butler_runtime import bound_matter, validate_task_action
from app.policy.execution_marker import execution_is_marked_approved
from app.security.desktop_api import desktop_api_token

WRITE_KINDS = frozenset({"click", "fill", "submit", "scroll", "cua"})


def issue_browser_command(session, action: dict, context: dict, *, operation: str = "act") -> dict:
    runtime = context.get("runtime")
    if runtime is None or not getattr(runtime, "task", None):
        raise ValueError("Persistent browser commands require a managed task.")
    matter = bound_matter(runtime.task)
    if matter is None or session.task_id != runtime.task.id:
        raise ValueError("Browser session is not bound to this matter's task.")
    from app.services.butler_scopes import matter_scopes

    scopes = matter_scopes(matter)
    kind = str(action.get("kind") or "")
    from types import SimpleNamespace

    validate_task_action(
        runtime.task,
        SimpleNamespace(name="browser.act"),
        {"action": {**action, "url": action.get("url") or session.current_url, "account_id": session.account_id}},
        current_tool_call_id=str(context.get("_executing_tool_call_id") or ""),
    )
    if operation == "act" and kind in WRITE_KINDS and not execution_is_marked_approved(context):
        raise ValueError("Persistent browser writes require runtime-validated approval.")
    if operation not in {"open", "inspect", "act", "adopt"}:
        raise ValueError("Unsupported browser command operation.")
    expected = [
        state for state in context.get("_expected_resource_state", []) if state.get("kind") == "browser_control"
    ]
    if (
        operation == "act"
        and kind in WRITE_KINDS
        and (len(expected) != 1 or expected[0].get("session_id") != session.id)
    ):
        raise ValueError("Browser write requires the approved control snapshot.")
    clean_action = {
        key: value
        for key, value in action.items()
        if key in {"kind", "url", "selector", "selectors", "text", "fields", "timeout_ms", "delta_y", "max_chars"}
    }
    from app.services.butler_scopes import origin_of

    origins = list(dict.fromkeys(origin_of(value) for value in session.allowed_origins))
    if not origins:
        origins = [origin_of(session.current_url)]
    profile = next(
        (
            scope.id
            for scope in scopes
            if session.current_url
            and any(
                session.current_url.startswith(origin + "/") or session.current_url == origin
                for origin in scope.origins
            )
            and scope.account_id == (session.account_id or "")
        ),
        "",
    )
    claim = {
        "schema": "browser-execution/v1",
        "nonce": secrets.token_hex(16),
        "expires_at": int(time.time() * 1000) + 30000,
        "operation": operation,
        "session_id": session.id,
        "task_id": session.task_id,
        "account_id": session.account_id or "",
        "profile_key": profile,
        "allowed_origins": origins,
        "action": clean_action,
        "approval_id": str(action.get("approval_id") or context.get("approval_id") or "")
        if operation == "act" and kind in WRITE_KINDS
        else "",
        "expected_fingerprint": expected[0].get("fingerprint", "") if expected else "",
        "scope_versions": dict(matter.scope_versions),
    }
    if operation == "adopt":
        from app.core import db

        previous = str(context.get("previous_browser_task_id") or "")
        task = db.fetch_one("tasks", previous) if previous in matter.task_ids else None
        if not task or (task.get("metadata") or {}).get("butler_matter_id") != matter.id:
            raise ValueError("Browser continuation belongs to another matter.")
        claim["previous_task_id"] = previous
    if operation == "act" and kind in WRITE_KINDS and not claim["approval_id"]:
        raise ValueError("Browser write is missing its actual approval record id.")
    return _sign_claim(claim)


def _sign_claim(claim: dict) -> dict:
    encoded = base64.b64encode(
        json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    signature = hmac.new(
        desktop_api_token().encode(), ("browser-execution/v1:" + encoded).encode(), hashlib.sha256
    ).hexdigest()
    return {"payload": encoded, "signature": signature}


def issue_matter_observation(matter, session: dict, *, selectors: dict | None = None) -> dict:
    """Read a retained page without requiring an active write-capable run."""
    from app.core import butler_store as store
    from app.core.butler_models import ButlerMatter, MatterStatus
    from app.services.butler_scopes import matter_scopes, origin_of

    current = store.get(ButlerMatter, matter.id)
    if (
        current is None
        or current.version != matter.version
        or store.control_state()["paused"]
        or current.status in {MatterStatus.PAUSED, MatterStatus.CANCELLED}
    ):
        raise ValueError("Matter observation is no longer authorized.")
    from app.llm.registry import get_effective_settings
    from app.policy.privacy import can_use_browser_network

    settings = get_effective_settings()
    if matter.mode == "privacy":
        settings = settings.model_copy(update={"mode": "privacy"})
    if not can_use_browser_network(settings).allowed:
        raise ValueError("Browser network access is disabled.")
    scopes = matter_scopes(current)
    origin = origin_of(session.get("current_url", ""))
    if session.get("task_id") not in current.task_ids or not origin or session.get("takeover"):
        raise ValueError("Browser observation is not owned by this matter.")
    if current.source != "user" and not any(
        "observe" in scope.allowed_actions
        and origin in scope.origins
        and scope.account_id == str(session.get("account_id") or "")
        for scope in scopes
    ):
        raise ValueError("Observation is outside the current grant.")
    return _sign_claim(
        {
            "schema": "browser-execution/v1",
            "nonce": secrets.token_hex(16),
            "expires_at": int(time.time() * 1000) + 30000,
            "operation": "act",
            "session_id": session["id"],
            "task_id": session["task_id"],
            "account_id": session.get("account_id") or "",
            "profile_key": session.get("scope_id") or "",
            "allowed_origins": [origin],
            "scope_versions": dict(current.scope_versions),
            "action": {"kind": "observe", "url": session["current_url"], "selectors": selectors or {}},
            "approval_id": "",
            "expected_fingerprint": "",
        }
    )


def issue_scope_observation(scope, origin: str, *, open_session: bool = False) -> dict:
    """Standing source grants authorize only opening/observing their own watch session."""
    from app.core import butler_store as store
    from app.core.butler_models import AutonomyScope
    from app.llm.registry import get_effective_settings
    from app.policy.privacy import can_use_browser_network
    from app.services.butler_scopes import origin_of

    current = store.get(AutonomyScope, scope.id)
    control = store.control_state()
    if (
        not control["enabled"]
        or control["paused"]
        or current is None
        or not current.enabled
        or current.version != scope.version
    ):
        raise ValueError("Source observation grant is no longer active.")
    if "observe" not in current.allowed_actions or origin_of(origin) not in current.origins:
        raise ValueError("Source observation is outside the allowed origins.")
    if not can_use_browser_network(get_effective_settings()).allowed:
        raise ValueError("Browser network access is disabled.")
    session_id = "watch_" + hashlib.sha256(f"{scope.id}:{origin_of(origin)}".encode()).hexdigest()[:32]
    claim = {
        "schema": "browser-execution/v1",
        "nonce": secrets.token_hex(16),
        "expires_at": int(time.time() * 1000) + 30000,
        "operation": "open" if open_session else "act",
        "session_id": session_id,
        "task_id": session_id,
        "account_id": current.account_id,
        "profile_key": current.id,
        "allowed_origins": list(current.origins),
        "action": {"kind": "open" if open_session else "observe", "url": origin},
        "approval_id": "",
        "expected_fingerprint": "",
        "scope_versions": {current.id: current.version},
    }
    encoded = base64.b64encode(
        json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    signature = hmac.new(
        desktop_api_token().encode(), ("browser-execution/v1:" + encoded).encode(), hashlib.sha256
    ).hexdigest()
    return {"payload": encoded, "signature": signature}
