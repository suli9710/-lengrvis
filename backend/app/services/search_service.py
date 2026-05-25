from __future__ import annotations

from typing import Any

from app.llm.registry import get_effective_settings
from app.tools import search_tools


def _context() -> dict[str, Any]:
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def query(q: str) -> dict:
    return search_tools.query({"query": q}, _context())
