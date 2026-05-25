from __future__ import annotations

from typing import Any

from app.llm.registry import get_effective_settings
from app.tools import app_tools


def _context() -> dict[str, Any]:
    settings = get_effective_settings()
    return {"settings": settings, "allowed_directories": settings.allowed_directories}


def list_apps() -> dict[str, Any]:
    return app_tools.list_installed({}, _context())


def launch(payload: dict[str, Any]) -> dict[str, Any]:
    return app_tools.launch_installed(payload, _context())


def open_file(payload: dict[str, Any]) -> dict[str, Any]:
    return app_tools.open_file(payload, _context())


def open_folder(payload: dict[str, Any]) -> dict[str, Any]:
    return app_tools.open_folder(payload, _context())


def reveal(payload: dict[str, Any]) -> dict[str, Any]:
    return app_tools.reveal_in_explorer(payload, _context())

