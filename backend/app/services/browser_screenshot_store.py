from __future__ import annotations

from typing import Any

from app.core.schemas import now_iso
from app.services.task_recording_service import persist_recording_frame


def persist_browser_screenshot(
    image: bytes,
    *,
    task_id: str,
    step_id: str,
    file_name: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    artifact_url = f"/api/tasks/{task_id}/recordings/{file_name}"
    recording_id = persist_recording_frame(
        {
            "task_id": task_id,
            "step_id": step_id,
            "phase": "browser_activity",
            "captured_at": now_iso(),
            "file_name": file_name,
            "mime_type": "image/png",
            "width": width,
            "height": height,
        },
        image,
    )
    return {"recording_id": recording_id, "artifact_url": artifact_url}
