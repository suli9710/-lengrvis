from __future__ import annotations

from fastapi import APIRouter, Query

from app.agents.browser_activity_review_agent import BrowserActivityReviewAgent
from app.core.schemas import SafetyReview
from app.llm.registry import get_effective_settings
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import browser_tools


router = APIRouter()
_browser_review_agent = BrowserActivityReviewAgent()


def _context():
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def _review_direct_browser_call(tool_name: str, payload: dict, risk_level: RiskLevel) -> SafetyReview:
    return _browser_review_agent.review_tool_call(
        "direct_browser_api",
        None,
        tool_name,
        payload,
        risk_level,
        context=_context(),
    )


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
    review = _review_direct_browser_call("browser.act", payload, RiskLevel.R2_REVERSIBLE_MODIFY)
    blocked = _blocked_review_response(review)
    if review.verdict == SafetyVerdict.DENY:
        return blocked
    if blocked is not None and not payload.get("dry_run"):
        return blocked
    return browser_tools.act(payload, _context())


@router.post("/browser/cua-run")
async def cua_run(payload: dict):
    review = _review_direct_browser_call("browser.cua_run", payload, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM)
    blocked = _blocked_review_response(review)
    if blocked is not None:
        return blocked
    return await browser_tools.cua_run_async(payload, _context())


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
