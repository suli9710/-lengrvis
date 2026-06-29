from __future__ import annotations

import hmac

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.agents.browser_activity_review_agent import BrowserActivityReviewAgent
from app.agents.safety_review_agent import SafetyReviewAgent
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview, now_iso
from app.llm.registry import get_effective_settings
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionStore
from app.policy.risk import SafetyVerdict
from app.security.desktop_api import close_unauthorized_desktop_websocket
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
    if browser_review is not None and browser_review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
        return browser_review
    return global_review


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
    if approval.risk_level and approval.risk_level != tool.risk_level.value:
        return "Browser approval risk level does not match this tool."
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
    if payload.get("dry_run") is True:
        return False
    if tool_name in {"browser.cua", "browser.cua_run"}:
        return True
    if tool_name != "browser.act":
        return False
    action = payload.get("action")
    kind = ""
    if isinstance(action, dict):
        kind = str(action.get("kind") or "")
    kind = str(payload.get("kind") or kind).strip().casefold().replace("_", "-")
    return kind in {"click", "fill", "submit", "scroll", "cua"}


@router.post("/browser/open-url")
def open_url(payload: dict):
    return browser_tools.open_url(payload, _context())


@router.post("/browser/session/start")
def session_start(payload: dict):
    return browser_tools.session_start(payload, _context())


@router.post("/browser/session/close")
def session_close(payload: dict):
    return browser_tools.session_close(payload, _context())


@router.get("/browser/sessions")
def sessions(limit: int = 200):
    return browser_tools.sessions({"limit": limit}, _context())


@router.get("/browser/session/{session_id}")
def session_info(session_id: str):
    return browser_tools.session_info({"session_id": session_id}, _context())


@router.get("/browser/session/{session_id}/events")
def session_events(session_id: str, limit: int = 200):
    return browser_tools.session_events({"session_id": session_id, "limit": limit}, _context())


@router.post("/browser/observe")
def observe(payload: dict):
    return browser_tools.observe(payload, _context())


@router.post("/browser/act")
def act(payload: dict):
    context = _context()
    review = _review_direct_browser_call("browser.act", payload, context)
    blocked = _blocked_review_response(review)
    if review.verdict == SafetyVerdict.DENY:
        return blocked
    approval_error = _claim_valid_browser_approval("browser.act", payload, context)
    if approval_error is not None:
        return _attach_review_to_approval_error(approval_error, blocked)
    mark_execution_approved(context)
    return browser_tools.act(payload, context)


@router.post("/browser/cua-run")
async def cua_run(payload: dict):
    context = _context()
    review = _review_direct_browser_call("browser.cua_run", payload, context)
    blocked = _blocked_review_response(review)
    if review.verdict == SafetyVerdict.DENY:
        return blocked
    approval_error = _claim_valid_browser_approval("browser.cua_run", payload, context)
    if approval_error is not None:
        return _attach_review_to_approval_error(approval_error, blocked)
    mark_execution_approved(context)
    return await browser_tools.cua_run_async(payload, context)


@router.post("/browser/cua")
async def cua(payload: dict):
    return await cua_run(payload)


@router.post("/browser/replay-export")
def replay_export(payload: dict):
    return browser_tools.replay_export(payload, _context())


@router.get("/browser/read")
def read(url: str = Query(...), max_chars: int | None = None):
    payload: dict = {"url": url}
    if max_chars is not None:
        payload["max_chars"] = max_chars
    return browser_tools.read_page(payload, _context())


@router.post("/browser/read-page")
def read_page(payload: dict):
    return browser_tools.read_page(payload, _context())


@router.post("/browser/summarize-page")
def summarize_page(payload: dict):
    return browser_tools.summarize_page(payload, _context())


@router.post("/browser/screenshot")
def screenshot(payload: dict):
    return browser_tools.screenshot(payload, _context())


@router.get("/browser/links")
def links(url: str = Query(...), max_chars: int | None = None):
    payload: dict = {"url": url}
    if max_chars is not None:
        payload["max_chars"] = max_chars
    return browser_tools.extract_links(payload, _context())


@router.post("/browser/extract-links")
def extract_links(payload: dict):
    return browser_tools.extract_links(payload, _context())


@ws_router.websocket("/ws/browser-host")
async def browser_host_bridge(websocket: WebSocket):
    if await close_unauthorized_desktop_websocket(websocket):
        return
    await websocket.accept()
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "message": "Browser host messages must be JSON objects."})
                continue
            message_type = str(message.get("type") or "")
            if message_type == "ping":
                await websocket.send_json({"type": "pong", "request_id": message.get("request_id")})
    except WebSocketDisconnect:
        return
