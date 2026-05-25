from __future__ import annotations

import re
import os
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import AppSettings
from app.core import db
from app.core.schemas import now_iso
from app.llm.registry import get_effective_settings


RECORDING_KIND = "step_screenshot"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DEFAULT_MIME_TYPE = "image/png"


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
    except Exception as exc:  # noqa: BLE001
        return _failed_frame(task_id, step_id, phase, captured_at, file_name, str(exc))


def recording_enabled() -> bool:
    raw = os.environ.get("MARVIS_TASK_RECORDING_ENABLED") or os.environ.get("MAVRIS_TASK_RECORDING_ENABLED")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("MARVIS_TASK_RECORDING_FORCE"):
        return False
    return True


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
    """Persist a captured screenshot as a SQLite BLOB and return its id."""
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
    }

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
                image,
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
            SELECT image, mime_type
            FROM task_recordings
            WHERE task_id = ? AND file_name = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (task_id, file_name),
        ).fetchone()
    if row is None:
        raise FileNotFoundError(file_name)
    return bytes(row["image"]), str(row["mime_type"] or _DEFAULT_MIME_TYPE)


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


def _safe_name(value: str) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip())
    return text.strip("._-")[:120]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
