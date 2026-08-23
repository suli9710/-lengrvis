"""Shared path-lock keys for task rollback mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.orchestration.tool_runtime_paths import FILESYSTEM_WRITE_BARRIER_KEY, normalize_lock_path
from app.tools.rollback_inventory import RollbackSnapshot


def rollback_write_lock_keys(snapshot: RollbackSnapshot) -> list[str]:
    """Return the same path and parent keys used by ordinary write tools."""

    keys: set[str] = {FILESYSTEM_WRITE_BARRIER_KEY} if snapshot.entries else set()
    for entry in snapshot.entries:
        if entry.result is None:
            continue
        info = dict(entry.result.rollback_info or {})
        for state in _post_resource_states(info):
            _add_lock_path(keys, state.get("path"))
        for action in ("move_back", "rename_back"):
            spec = info.get(action)
            if isinstance(spec, dict):
                _add_lock_path(keys, spec.get("from"))
                _add_lock_path(keys, spec.get("to"))
        for action in ("trash_created_file", "delete_folder_if_empty"):
            _add_lock_path(keys, info.get(action))
        for backup_key in ("backup", "dst_backup"):
            backup = info.get(backup_key)
            if isinstance(backup, dict):
                _add_lock_path(keys, backup.get("path"))
                _add_lock_path(keys, backup.get("original_path"))
            elif backup:
                _add_lock_path(keys, backup)
                try:
                    _add_lock_path(keys, Path(str(backup)).with_suffix(""))
                except ValueError:
                    continue
    return sorted(keys)


def _post_resource_states(info: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("_post_resource_state", "_post_state"):
        value = info.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _add_lock_path(keys: set[str], value: Any) -> None:
    normalized = normalize_lock_path(value)
    if not normalized:
        return
    keys.add(normalized)
    parent = str(Path(normalized).parent)
    if parent and parent != normalized:
        keys.add(parent)
