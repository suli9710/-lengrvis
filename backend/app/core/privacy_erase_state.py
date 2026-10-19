"""Durable, content-free progress for the coordinated desktop/backend erase."""

from __future__ import annotations

import json
import secrets
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.errors import AppError


def _path() -> Path:
    from app.core import db

    return db.db_path().with_name("privacy-erase-state.sqlite3")


def load_erase_state() -> dict[str, Any] | None:
    path = _path()
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT data FROM erase_state WHERE id = 1").fetchone()
    return json.loads(row[0]) if row else None


def save_erase_state(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS erase_state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO erase_state VALUES (1, ?)", (json.dumps(state),))


def erase_is_reserved() -> bool:
    state = load_erase_state()
    return bool(state and state["status"] != "complete")


def reserve_erase(*, include_settings: bool, confirmation_id: str) -> dict[str, Any]:
    state = load_erase_state()
    if state is None or state["status"] == "complete":
        state = {
            "operation_id": secrets.token_urlsafe(24),
            "include_settings": include_settings,
            "backend": "pending",
            "desktop": "pending",
            "status": "reserved",
        }
    elif state["include_settings"] != include_settings:
        raise AppError("local_data_erase_scope_changed", "请使用原来的设置范围重试未完成的数据清除。", 409)
    token = secrets.token_urlsafe(32)
    state["token_hash"] = sha256(token.encode()).hexdigest()
    state["confirmation_id"] = confirmation_id
    save_erase_state(state)
    return {**public_erase_state(state), "reservation_token": token}


def require_erase_reservation(operation_id: str, token: str) -> dict[str, Any]:
    state = load_erase_state()
    if (
        state is None
        or state["operation_id"] != operation_id
        or not token
        or not secrets.compare_digest(state["token_hash"], sha256(token.encode()).hexdigest())
    ):
        raise AppError("local_data_erase_reservation_invalid", "数据清除预留无效，请重新确认后重试。", 403)
    return state


def public_erase_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in {"token_hash", "confirmation_id"}}
