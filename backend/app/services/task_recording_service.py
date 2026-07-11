from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import AppSettings, env_raw, get_env
from app.core import db
from app.core.schemas import now_iso
from app.llm.registry import get_effective_settings
from app.observability.best_effort import log_best_effort_failure
from app.policy.redaction import redact_public_text, redact_value
from app.security.sensitive_data_crypto import (
    decrypt_sensitive_bytes,
    encrypt_sensitive_bytes,
    is_encrypted_payload,
)

RECORDING_KIND = "step_screenshot"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DEFAULT_MIME_TYPE = "image/png"
_TEST_ENV_TRUE_VALUES = {"1", "true", "yes", "on", "test", "testing"}
logger = logging.getLogger(__name__)


def capture_step_screenshot(
    task_id: str,
    step_id: str,
    phase: str,
    *,
    settings: AppSettings | None = None,
) -> dict[str, Any]:
    captured_at = now_iso()
    clean_phase = _safe_name(phase) or "frame"
    file_name = f"{_safe_name(step_id) or 'step'}-{clean_phase}-{_timestamp()}.png"
    if not recording_enabled():
        return _failed_frame(
            task_id,
            step_id,
            phase,
            captured_at,
            file_name,
            "Task recording is disabled.",
        )
    try:
        image = _grab_screen()
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
        width, height = image.size
        frame = {
            "kind": RECORDING_KIND,
            "task_id": task_id,
            "step_id": step_id,
            "phase": phase,
            "ok": True,
            "enabled": True,
            "captured_at": captured_at,
            "file_name": file_name,
            "path": "",
            "url": f"/api/tasks/{task_id}/recordings/{file_name}",
            "mime_type": _DEFAULT_MIME_TYPE,
            "width": width,
            "height": height,
            "error": "",
        }
        recording_id = persist_recording_frame(frame, png)
        return {**frame, "recording_id": recording_id}
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        log_best_effort_failure(
            logger,
            "task_recording.capture_step_screenshot",
            exc,
            task_id=task_id,
            step_id=step_id,
            phase=phase,
        )
        return _failed_frame(task_id, step_id, phase, captured_at, file_name, _safe_recording_error(exc))


def recording_enabled() -> bool:
    force = _env_flag("LENGRVIS_TASK_RECORDING_FORCE")
    if force and _is_test_environment():
        return True

    enabled = _env_flag("LENGRVIS_TASK_RECORDING_ENABLED")
    if enabled is not None:
        return enabled
    return False


def recording_task_dir(task_id: str, *, settings: AppSettings | None = None) -> Path:
    effective = settings or get_effective_settings()
    return Path(effective.data_dir) / "task_recordings" / (_safe_name(task_id) or "task")


def resolve_recording_path(task_id: str, file_name: str, *, settings: AppSettings | None = None) -> Path:
    if Path(file_name).name != file_name:
        raise ValueError("Recording file name must not contain path separators.")
    root = recording_task_dir(task_id, settings=settings).resolve(strict=False)
    path = (root / file_name).resolve(strict=False)
    if not path.is_relative_to(root):
        raise ValueError("Recording path must stay inside the task recording directory.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(file_name)
    return path


def persist_recording_frame(frame: dict[str, Any], image: bytes) -> str:
    """Persist a captured screenshot as an encrypted SQLite BLOB."""
    if not image:
        raise ValueError("Recording image must not be empty.")

    task_id = str(frame.get("task_id") or "")
    step_id = str(frame.get("step_id") or "")
    file_name = str(frame.get("file_name") or "")
    if not task_id or not step_id or not file_name:
        raise ValueError("Recording frame requires task_id, step_id, and file_name.")

    recording_id = str(frame.get("recording_id") or f"rec_{uuid4().hex}")
    captured_at = str(frame.get("captured_at") or now_iso())
    metadata = {
        "id": recording_id,
        "kind": RECORDING_KIND,
        "task_id": task_id,
        "step_id": step_id,
        "phase": str(frame.get("phase") or ""),
        "ok": bool(frame.get("ok", True)),
        "enabled": bool(frame.get("enabled", True)),
        "captured_at": captured_at,
        "file_name": file_name,
        "url": f"/api/tasks/{task_id}/recordings/{file_name}",
        "mime_type": str(frame.get("mime_type") or _DEFAULT_MIME_TYPE),
        "width": int(frame.get("width") or 0),
        "height": int(frame.get("height") or 0),
        "error": str(frame.get("error") or ""),
        "storage_encrypted": True,
    }
    encrypted_image = _encrypt_recording_image(
        image,
        recording_id=recording_id,
        task_id=task_id,
        file_name=file_name,
    )

    with db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_recordings
                (id, task_id, step_id, phase, file_name, mime_type, width, height, image, data, captured_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recording_id,
                task_id,
                step_id,
                metadata["phase"],
                file_name,
                metadata["mime_type"],
                metadata["width"],
                metadata["height"],
                encrypted_image,
                json.dumps(metadata, ensure_ascii=False),
                captured_at,
                now_iso(),
            ),
        )
    return recording_id


def list_recording_frames(task_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, data
            FROM task_recordings
            WHERE task_id = ?
            ORDER BY captured_at ASC, id ASC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
    frames: list[dict[str, Any]] = []
    for row in rows:
        frame = json.loads(row["data"])
        if not isinstance(frame, dict):
            continue
        frame["recording_id"] = row["id"]
        frame["path"] = ""
        frame["url"] = f"/api/tasks/{task_id}/recordings/{frame.get('file_name', row['id'])}"
        frames.append(frame)
    return frames


def read_recording_image(task_id: str, file_name: str) -> tuple[bytes, str]:
    if Path(file_name).name != file_name:
        raise ValueError("Recording file name must not contain path separators.")
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, image, mime_type, data
            FROM task_recordings
            WHERE task_id = ? AND file_name = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (task_id, file_name),
        ).fetchone()
    if row is None:
        raise FileNotFoundError(file_name)
    recording_id = str(row["id"])
    stored = bytes(row["image"])
    if is_encrypted_payload(stored):
        image = _decrypt_recording_image(
            stored,
            recording_id=recording_id,
            task_id=task_id,
            file_name=file_name,
        )
    else:
        # One-way compatibility migration: legacy plaintext screenshots are
        # encrypted before their bytes are returned to the caller.
        image = stored
        encrypted = _encrypt_recording_image(
            image,
            recording_id=recording_id,
            task_id=task_id,
            file_name=file_name,
        )
        metadata = _mark_recording_metadata_encrypted(str(row["data"] or ""))
        with db.connect() as conn:
            conn.execute(
                "UPDATE task_recordings SET image = ?, data = ? WHERE id = ?",
                (encrypted, metadata, recording_id),
            )
    return image, str(row["mime_type"] or _DEFAULT_MIME_TYPE)


def migrate_plaintext_recordings(*, batch_size: int = 200) -> dict[str, int]:
    """Encrypt legacy plaintext recording rows in place.

    This is safe to run repeatedly and deliberately loads the encryption key
    only when at least one plaintext row exists.
    """
    limit = max(1, min(1000, int(batch_size)))
    scanned = 0
    migrated = 0
    last_id = ""
    while True:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, file_name, image, data
                FROM task_recordings
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (last_id, limit),
            ).fetchall()
        if not rows:
            break
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in rows:
                recording_id = str(row["id"])
                last_id = recording_id
                scanned += 1
                stored = bytes(row["image"])
                if is_encrypted_payload(stored):
                    continue
                encrypted = _encrypt_recording_image(
                    stored,
                    recording_id=recording_id,
                    task_id=str(row["task_id"]),
                    file_name=str(row["file_name"]),
                )
                metadata = _mark_recording_metadata_encrypted(str(row["data"] or ""))
                conn.execute(
                    "UPDATE task_recordings SET image = ?, data = ? WHERE id = ?",
                    (encrypted, metadata, recording_id),
                )
                migrated += 1
    return {"scanned": scanned, "migrated": migrated}


def _encrypt_recording_image(image: bytes, *, recording_id: str, task_id: str, file_name: str) -> bytes:
    return encrypt_sensitive_bytes(
        image,
        purpose="task_recording_image",
        binding={
            "recording_id": recording_id,
            "task_id": task_id,
            "file_name": file_name,
        },
        data_dir=db.db_path().parent,
    )


def _decrypt_recording_image(image: bytes, *, recording_id: str, task_id: str, file_name: str) -> bytes:
    return decrypt_sensitive_bytes(
        image,
        purpose="task_recording_image",
        binding={
            "recording_id": recording_id,
            "task_id": task_id,
            "file_name": file_name,
        },
        data_dir=db.db_path().parent,
    )


def _mark_recording_metadata_encrypted(raw: str) -> str:
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["storage_encrypted"] = True
    return json.dumps(metadata, ensure_ascii=False)


def _grab_screen():
    from PIL import ImageGrab

    try:
        image = ImageGrab.grab(all_screens=True)
    except TypeError:
        image = ImageGrab.grab()
    return image.convert("RGB")


def _failed_frame(
    task_id: str,
    step_id: str,
    phase: str,
    captured_at: str,
    file_name: str,
    error: str,
) -> dict[str, Any]:
    return {
        "kind": RECORDING_KIND,
        "task_id": task_id,
        "step_id": step_id,
        "phase": phase,
        "ok": False,
        "enabled": False,
        "captured_at": captured_at,
        "file_name": file_name,
        "path": "",
        "url": "",
        "mime_type": "image/png",
        "width": 0,
        "height": 0,
        "error": error,
    }


def _safe_recording_error(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))


def _safe_name(value: str) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip())
    return text.strip("._-")[:120]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _env_flag(name: str) -> bool | None:
    # env_raw keeps the tri-state contract: unset -> None, set -> parsed bool.
    raw = env_raw(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_test_environment() -> bool:
    if get_env("PYTEST_CURRENT_TEST"):
        return True
    return any(
        str(get_env(name) or "").strip().lower() in _TEST_ENV_TRUE_VALUES
        for name in ("LENGRVIS_TEST", "APP_ENV", "LENGRVIS_ENV")
    )
