from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.config import AppSettings, get_env
from app.config_paths import DEFAULT_DATA_DIR

REVOCATION_FILE_PRESENCE_ANCHOR_VERSION = 1
REVOCATION_FILE_PRESENCE_KEY_PREFIX = "security.capability_revocation_file_presence.v1."


def read_revocation_file_presence_anchor(
    path: Path,
    *,
    settings: AppSettings | None,
) -> tuple[bool, str]:
    """Read the protected presence marker without creating state on a fresh install."""
    try:
        from app.core import db

        data_dir = _anchor_data_dir(settings)
        with db.using_data_dir(data_dir):
            if not db.db_path().exists():
                return False, ""
            db.init_db()
            key = _presence_key(path)
            with db.connect() as conn:
                row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
                presence = conn.execute(
                    """
                    SELECT 1
                    FROM sensitive_record_presence
                    WHERE table_name = 'app_settings' AND record_id = ?
                    """,
                    (key,),
                ).fetchone()
                if row is None:
                    return False, "anchor_missing" if presence is not None else ""
                db._require_sensitive_record_integrity(conn, "app_settings", key, row["value"])
                payload = json.loads(row["value"])
    except Exception:  # noqa: BLE001 - broad-exception-boundary: unverifiable security state fails closed.
        return False, "anchor_unreadable"

    if payload != _presence_payload(path):
        return False, "anchor_invalid"
    return True, ""


def persist_revocation_file_presence_anchor(
    path: Path,
    *,
    settings: AppSettings | None,
) -> str:
    try:
        from app.core import db
        from app.core.db_settings import set_setting

        with db.using_data_dir(_anchor_data_dir(settings)):
            db.init_db()
            set_setting(_presence_key(path), _presence_payload(path))
            observed, error = read_revocation_file_presence_anchor(path, settings=settings)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: failure to persist the kill switch is fatal.
        return "anchor_write_failed"
    if error or not observed:
        return error or "anchor_write_failed"
    return ""


def _anchor_data_dir(settings: AppSettings | None) -> Path:
    if settings is not None:
        return Path(settings.data_dir)
    return Path(str(get_env("LENGRVIS_DATA_DIR") or DEFAULT_DATA_DIR))


def _presence_key(path: Path) -> str:
    normalized_path = os.path.normcase(str(path.expanduser().resolve(strict=False))).replace("\\", "/")
    path_hash = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
    return REVOCATION_FILE_PRESENCE_KEY_PREFIX + path_hash


def _presence_payload(path: Path) -> dict[str, Any]:
    key = _presence_key(path)
    return {
        "version": REVOCATION_FILE_PRESENCE_ANCHOR_VERSION,
        "file_path_hash": key.removeprefix(REVOCATION_FILE_PRESENCE_KEY_PREFIX),
        "required": True,
    }
