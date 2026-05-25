from __future__ import annotations

from fastapi import APIRouter, Query

from app.llm.registry import get_effective_settings
from app.tools import browser_tools


router = APIRouter()


def _context():
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


@router.post("/browser/open-url")
def open_url(payload: dict):
    return browser_tools.open_url(payload, _context())


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
