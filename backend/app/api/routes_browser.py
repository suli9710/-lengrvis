from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.agents.browser_activity_review_agent import BrowserActivityReviewAgent
from app.agents.safety_review_agent import SafetyReviewAgent
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.direct_tool_execution import (
    execute_direct_tool_journaled,
    execute_direct_tool_journaled_async,
    finalize_unjournaled_direct_result,
)
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.effective_risk_binding import (
    approval_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
    risk_revalidation_context,
)
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionStore
from app.policy.risk import RISK_ORDER, SafetyVerdict
from app.security.desktop_api import close_unauthorized_desktop_websocket
from app.services.browser_host_bridge_service import BrowserHostBridgeUnavailable, browser_host_bridge_hub
from app.tools import browser_tools
from app.tools.registry import register_all_tools
from app.tools.registry import registry as tool_registry

router = APIRouter()
ws_router = APIRouter()
_browser_review_agent = BrowserActivityReviewAgent()


def _context():
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def _tool_definition(tool_name: str):
    if not tool_registry.list():
        register_all_tools()
    return tool_registry.get(tool_name)


def _review_direct_browser_call(tool_name: str, payload: dict, context: dict) -> SafetyReview:
    tool = _tool_definition(tool_name)
    browser_review = _browser_review_agent.review_tool_call(
        "direct_browser_api",
        None,
        tool_name,
        payload,
        tool.risk_level,
        context=context,
        tool_definition=tool,
    )
    if browser_review is not None and browser_review.verdict == SafetyVerdict.DENY:
        return browser_review
    global_review = SafetyReviewAgent(settings=context.get("settings")).review_tool_call(
        "direct_browser_api",
        None,
        tool_name,
        payload,
        tool.risk_level,
        context=context,
        tool_definition=tool,
    )
    if global_review.verdict == SafetyVerdict.DENY:
        return global_review
    reviews = [global_review, *([browser_review] if browser_review is not None else [])]
    selected = max(reviews, key=lambda item: RISK_ORDER[item.risk_level])
    approval_review = next(
        (item for item in reviews if item.verdict == SafetyVerdict.NEEDS_USER_APPROVAL),
        None,
    )
    if approval_review is not None and selected.verdict == SafetyVerdict.ALLOW:
        selected = selected.model_copy(
            update={
                "verdict": SafetyVerdict.NEEDS_USER_APPROVAL,
                "user_confirmation_message": approval_review.user_confirmation_message,
                "reasons": [*selected.reasons, *approval_review.reasons],
            },
            deep=True,
        )
    if selected.declared_risk_level is None:
        selected = selected.model_copy(
            update={"declared_risk_level": global_review.declared_risk_level or tool.risk_level}
        )
    return selected


def _blocked_review_response(review: SafetyReview) -> dict | None:
    if review.verdict == SafetyVerdict.DENY:
        return {
            "ok": False,
            "status": "denied",
            "error": "; ".join(review.reasons) or review.safe_alternative or "Browser activity denied.",
            "review": review.model_dump(mode="json"),
        }
    if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "review": review.model_dump(mode="json"),
        }
    return None


def _execute_reviewed_browser_adapter(
    tool_name: str,
    payload: dict,
    executor: Callable[[dict, dict], dict],
) -> dict:
    context = _context()
    review = _review_direct_browser_call(tool_name, payload, context)
    blocked = _blocked_review_response(review)
    if blocked is not None:
        return blocked
    return finalize_unjournaled_direct_result(
        _tool_definition(tool_name),
        executor(payload, context),
        context,
        risk_level=review.risk_level,
        task_id="direct_browser_api",
    )


def _claim_valid_browser_approval(tool_name: str, payload: dict, context: dict) -> dict | None:
    if not _browser_payload_is_live_write(tool_name, payload):
        return None
    if payload.get("approved") is not True:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "error": f"{tool_name} live execution requires approved=true and a valid approved approval_id.",
        }
    approval_id = str(payload.get("approval_id") or "").strip()
    if not approval_id:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "error": f"{tool_name} live execution requires a valid approved approval_id.",
        }
    data = db.fetch_one("approvals", approval_id)
    if not data:
        return {"ok": False, "status": "denied", "error": "Approval id was not found in the approval database."}
    approval = Approval.model_validate(data)
    binding_error = _browser_approval_binding_error(approval, tool_name, payload, context, allow_consumed=False)
    if binding_error:
        db.expire_approval_if_unconsumed(approval.id, now_iso(), binding_error)
        return {"ok": False, "status": "denied", "error": binding_error}
    claimed = db.claim_approval_for_execution(approval.id, now_iso())
    if not claimed:
        return {
            "ok": False,
            "status": "denied",
            "error": "Approval has already been consumed or is no longer approved.",
        }
    claimed_approval = Approval.model_validate(claimed)
    binding_error = _browser_approval_binding_error(claimed_approval, tool_name, payload, context, allow_consumed=True)
    if binding_error:
        return {"ok": False, "status": "denied", "error": binding_error}
    context["effective_risk_binding"] = dict(approval_risk_binding(claimed_approval) or {})
    return None


def _attach_review_to_approval_error(approval_error: dict, blocked: dict | None) -> dict:
    if approval_error.get("status") == "requires_approval" and blocked is not None:
        return {**blocked, "error": approval_error.get("error") or blocked.get("error")}
    return approval_error


def _browser_approval_binding_error(
    approval: Approval,
    tool_name: str,
    payload: dict,
    context: dict,
    *,
    allow_consumed: bool,
) -> str:
    if approval.approval_type != "tool_call":
        return "Browser approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"Browser approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "Browser approval has already been consumed."
    tool = _tool_definition(tool_name)
    required = {
        "tool_name": approval.tool_name,
        "args_binding_hmac": approval.args_binding_hmac,
        "preview_hmac": approval.preview_hmac,
        "settings_fingerprint": approval.settings_fingerprint,
        "permission_policy_version": approval.permission_policy_version,
        "tool_version": approval.tool_version,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return f"Browser approval lacks binding metadata: {', '.join(missing)}."
    if approval.tool_name != tool_name:
        return "Browser approval tool name does not match this route."
    current_context = risk_revalidation_context(context, task_id=approval.task_id)
    refreshed_review = _review_direct_browser_call(tool_name, payload, current_context)
    current_declared = refreshed_review.declared_risk_level or tool.risk_level
    risk_binding = approval_risk_binding(approval)
    risk_error = effective_risk_binding_error(
        risk_binding,
        current_declared_risk=current_declared,
        approval_risk_level=approval.risk_level,
    )
    if risk_error:
        return risk_error
    refreshed_error = refreshed_effective_risk_error(risk_binding, refreshed_review)
    if refreshed_error:
        return refreshed_error
    if approval.tool_version != getattr(tool, "tool_version", "1"):
        return "Browser approval tool version does not match this tool."
    expected_args = args_binding_hmac(tool_name, payload, task_id=approval.task_id, step_id=approval.step_id)
    if not hmac.compare_digest(str(approval.args_binding_hmac or ""), str(expected_args or "")):
        return "Browser approval arguments do not match this request."
    expected_preview = preview_hmac(approval.diff_preview)
    if not hmac.compare_digest(str(approval.preview_hmac or ""), str(expected_preview or "")):
        return "Browser approval preview was modified after review."
    expected_settings = settings_fingerprint(
        context.get("settings"),
        allowed_directories=list(context.get("allowed_directories") or []),
    )
    if not hmac.compare_digest(str(approval.settings_fingerprint or ""), str(expected_settings or "")):
        return "Browser runtime settings changed after approval preview."
    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not hmac.compare_digest(str(approval.permission_policy_version or ""), str(expected_policy or "")):
        return "Browser permission policy changed after approval preview."
    return ""


def _browser_payload_is_live_write(tool_name: str, payload: dict) -> bool:
    if tool_name in {"browser.cua", "browser.cua_run"}:
        return not bool(payload.get("dry_run", True))
    if tool_name != "browser.act":
        return False
    action = payload.get("action")
    kind = ""
    if isinstance(action, dict):
        kind = str(action.get("kind") or "")
    kind = str(kind or payload.get("kind") or "").strip().casefold().replace("_", "-")
    if kind not in {"click", "fill", "submit", "scroll", "cua"}:
        return False
    if isinstance(action, dict) and "dry_run" in action:
        dry_run = action.get("dry_run")
    else:
        dry_run = payload.get("dry_run", True)
    return not bool(dry_run)


def _browser_payload_is_review_preview(tool_name: str, payload: dict) -> bool:
    if tool_name in {"browser.cua", "browser.cua_run"}:
        return not _browser_payload_is_live_write(tool_name, payload)
    if tool_name != "browser.act":
        return False
    action = payload.get("action")
    kind = str(action.get("kind") or "") if isinstance(action, dict) else ""
    kind = str(kind or payload.get("kind") or "").strip().casefold().replace("_", "-")
    return kind in {"click", "fill", "submit", "scroll", "cua"} and not _browser_payload_is_live_write(
        tool_name,
        payload,
    )


@router.post("/browser/open-url")
def open_url(payload: dict):
    return _execute_reviewed_browser_adapter("browser.open_url", payload, browser_tools.open_url)


@router.post("/browser/session/start")
def session_start(payload: dict):
    return _execute_reviewed_browser_adapter("browser.session_start", payload, browser_tools.session_start)


@router.post("/browser/session/close")
def session_close(payload: dict):
    return _execute_reviewed_browser_adapter("browser.session_close", payload, browser_tools.session_close)


@router.get("/browser/sessions")
def sessions(limit: int = 200):
    return _execute_reviewed_browser_adapter("browser.sessions", {"limit": limit}, browser_tools.sessions)


@router.get("/browser/session/{session_id}")
def session_info(session_id: str):
    return _execute_reviewed_browser_adapter(
        "browser.session_info",
        {"session_id": session_id},
        browser_tools.session_info,
    )


@router.get("/browser/session/{session_id}/events")
def session_events(session_id: str, limit: int = 200):
    return _execute_reviewed_browser_adapter(
        "browser.events",
        {"session_id": session_id, "limit": limit},
        browser_tools.session_events,
    )


@router.post("/browser/observe")
def observe(payload: dict):
    return _execute_reviewed_browser_adapter("browser.observe", payload, browser_tools.observe)


@router.post("/browser/act")
def act(payload: dict):
    context = _context()
    review = _review_direct_browser_call("browser.act", payload, context)
    blocked = _blocked_review_response(review)
    if review.verdict == SafetyVerdict.DENY:
        return blocked
    if not _browser_payload_is_live_write("browser.act", payload):
        if blocked is not None and not _browser_payload_is_review_preview("browser.act", payload):
            return blocked
        result = finalize_unjournaled_direct_result(
            _tool_definition("browser.act"),
            browser_tools.act(payload, context),
            context,
            risk_level=review.risk_level,
            task_id="direct_browser_api",
        )
        if blocked is not None:
            result.setdefault("review", review.model_dump(mode="json"))
        return result
    approval_error = _claim_valid_browser_approval("browser.act", payload, context)
    if approval_error is not None:
        return _attach_review_to_approval_error(approval_error, blocked)
    mark_execution_approved(context)
    return execute_direct_tool_journaled(
        _tool_definition("browser.act"),
        payload,
        context,
        approval_id=str(payload.get("approval_id") or ""),
        executor=browser_tools.act,
    )


@router.post("/browser/cua-run")
async def cua_run(payload: dict):
    context = _context()
    review = _review_direct_browser_call("browser.cua_run", payload, context)
    blocked = _blocked_review_response(review)
    if review.verdict == SafetyVerdict.DENY:
        return blocked
    if not _browser_payload_is_live_write("browser.cua_run", payload):
        result = finalize_unjournaled_direct_result(
            _tool_definition("browser.cua_run"),
            await browser_tools.cua_run_async(payload, context),
            context,
            risk_level=review.risk_level,
            task_id="direct_browser_api",
        )
        if blocked is not None:
            result.setdefault("review", review.model_dump(mode="json"))
        return result
    approval_error = _claim_valid_browser_approval("browser.cua_run", payload, context)
    if approval_error is not None:
        return _attach_review_to_approval_error(approval_error, blocked)
    mark_execution_approved(context)
    return await execute_direct_tool_journaled_async(
        _tool_definition("browser.cua_run"),
        payload,
        context,
        approval_id=str(payload.get("approval_id") or ""),
        executor=browser_tools.cua_run_async,
    )


@router.post("/browser/cua")
async def cua(payload: dict):
    return await cua_run(payload)


@router.post("/browser/replay-export")
def replay_export(payload: dict):
    return _execute_reviewed_browser_adapter("browser.replay_export", payload, browser_tools.replay_export)


@router.get("/browser/read")
def read(url: str = Query(...), max_chars: int | None = None):
    payload: dict = {"url": url}
    if max_chars is not None:
        payload["max_chars"] = max_chars
    return _execute_reviewed_browser_adapter("browser.read_page", payload, browser_tools.read_page)


@router.post("/browser/read-page")
def read_page(payload: dict):
    return _execute_reviewed_browser_adapter("browser.read_page", payload, browser_tools.read_page)


@router.post("/browser/summarize-page")
def summarize_page(payload: dict):
    return _execute_reviewed_browser_adapter("browser.summarize_page", payload, browser_tools.summarize_page)


@router.post("/browser/screenshot")
def screenshot(payload: dict):
    return _execute_reviewed_browser_adapter("browser.screenshot", payload, browser_tools.screenshot)


@router.get("/browser/links")
def links(url: str = Query(...), max_chars: int | None = None):
    payload: dict = {"url": url}
    if max_chars is not None:
        payload["max_chars"] = max_chars
    return _execute_reviewed_browser_adapter("browser.extract_links", payload, browser_tools.extract_links)


@router.post("/browser/extract-links")
def extract_links(payload: dict):
    return _execute_reviewed_browser_adapter("browser.extract_links", payload, browser_tools.extract_links)


@router.get("/browser-host/bridge/snapshot")
def browser_host_bridge_snapshot() -> dict:
    return _execute_reviewed_browser_adapter(
        "browser.sessions",
        {"source": "browser_host_bridge"},
        lambda _payload, _context: browser_host_bridge_hub.status(),
    )


@router.post("/browser-host/bridge/action")
async def browser_host_bridge_action(payload: dict) -> dict:
    session_id = str(payload.get("session_id") or "").strip()
    raw_action = payload.get("action")
    action_kind = str(raw_action.get("kind") or "").strip().casefold() if isinstance(raw_action, dict) else ""
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required.")
    if len(session_id) > 256:
        raise HTTPException(status_code=422, detail="session_id is too long.")
    if action_kind not in {"observe", "screenshot"}:
        raise HTTPException(
            status_code=403,
            detail="Only read-only BrowserHost actions are allowed through this bridge.",
        )
    tool_name = "browser.observe" if action_kind == "observe" else "browser.screenshot"
    context = _context()
    review = _review_direct_browser_call(tool_name, {"session_id": session_id, "kind": action_kind}, context)
    blocked = _blocked_review_response(review)
    if blocked is not None:
        return blocked
    try:
        return finalize_unjournaled_direct_result(
            _tool_definition(tool_name),
            await browser_host_bridge_hub.request_read_only_action(
                session_id=session_id,
                action={"kind": action_kind},
            ),
            context,
            risk_level=review.risk_level,
            task_id="direct_browser_api",
        )
    except BrowserHostBridgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@ws_router.websocket("/ws/browser-host")
async def browser_host_bridge(websocket: WebSocket):
    if await close_unauthorized_desktop_websocket(websocket):
        return
    await websocket.accept()
    browser_host_bridge_hub.connect(websocket)
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "Browser host messages must be JSON objects."})
                continue
            message_type = str(message.get("type") or "")
            if message_type == "snapshot":
                browser_host_bridge_hub.receive_snapshot(websocket, message.get("snapshot"))
            elif message_type == "result":
                browser_host_bridge_hub.receive_result(websocket, message)
            elif message_type == "ping":
                await websocket.send_json({"type": "pong", "request_id": message.get("request_id")})
    except WebSocketDisconnect:
        return
    finally:
        browser_host_bridge_hub.disconnect(websocket)
