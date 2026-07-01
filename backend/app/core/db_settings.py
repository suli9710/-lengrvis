from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from app.core import db

_SETTINGS_HOOK_LOCK = threading.Lock()
_SETTINGS_INVALIDATION_HOOKS: list[Callable[[], None]] = []


def register_settings_invalidation_hook(fn: Callable[[], None]) -> None:
    with _SETTINGS_HOOK_LOCK:
        if fn not in _SETTINGS_INVALIDATION_HOOKS:
            _SETTINGS_INVALIDATION_HOOKS.append(fn)


def notify_settings_invalidated() -> None:
    with _SETTINGS_HOOK_LOCK:
        hooks = tuple(_SETTINGS_INVALIDATION_HOOKS)
    for hook in hooks:
        hook()


def set_setting(key: str, value: Any) -> None:
    stored = db._json(value)
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, stored, db._now_iso()),
        )
        db._store_sensitive_record_integrity(conn, "app_settings", key, stored)
    notify_settings_invalidated()


def get_settings_overrides() -> dict[str, Any]:
    with db.connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "app_settings", str(row["key"]), row["value"])
    result: dict[str, Any] = {}
    for row in rows:
        result[row["key"]] = json.loads(row["value"])
    return result
