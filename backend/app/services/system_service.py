from __future__ import annotations

from app.tools import system_tools


def health() -> dict:
    return {"status": "ok"}


def info() -> dict:
    return system_tools.get_info({}, {})


def disks() -> dict:
    return system_tools.get_disks({}, {})


def network() -> dict:
    return system_tools.get_network({}, {})


def diagnostics() -> dict:
    return system_tools.diagnostics({}, {})


def processes(limit: int = 25) -> dict:
    return system_tools.get_processes({"limit": limit}, {})


def startup_items() -> dict:
    return system_tools.get_startup_items({}, {})


def open_settings(uri: str, dry_run: bool = False) -> dict:
    return system_tools.open_settings_uri({"uri": uri, "dry_run": dry_run}, {})
