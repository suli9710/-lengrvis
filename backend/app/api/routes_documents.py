from __future__ import annotations

from fastapi import APIRouter

from app.commerce.entitlements import Feature, active_plan, require_feature
from app.llm.registry import get_effective_settings
from app.tools import document_tools


router = APIRouter()


def _context() -> dict:
    settings = get_effective_settings()
    return {"allowed_directories": settings.allowed_directories, "settings": settings}


def _require_document_ai() -> None:
    require_feature(active_plan(get_effective_settings()), Feature.DOCUMENT_AI)


@router.post("/documents/parse")
def parse_document(payload: dict):
    _require_document_ai()
    return document_tools.parse_advanced(payload, _context())


@router.post("/documents/ask")
def ask_document(payload: dict):
    _require_document_ai()
    return document_tools.ask_with_citations(payload, _context())


@router.post("/documents/compare")
def compare_documents(payload: dict):
    _require_document_ai()
    return document_tools.compare(payload, _context())


@router.post("/documents/tables")
def document_tables(payload: dict):
    _require_document_ai()
    return document_tools.extract_tables(payload, _context())


@router.post("/documents/redact-preview")
def document_redact_preview(payload: dict):
    _require_document_ai()
    return document_tools.redact_preview(payload, _context())


@router.post("/documents/cited-report")
def document_cited_report(payload: dict):
    _require_document_ai()
    return document_tools.generate_cited_report(payload, _context())
