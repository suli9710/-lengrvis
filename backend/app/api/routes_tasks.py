from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core import db
from app.core.schemas import AgentMessage, Task, TaskStatus
from app.llm.registry import get_effective_settings
from app.orchestration.state_machine import ensure_transition_allowed
from app.services import task_recording_service
from app.services.task_service import get_task, list_tasks, set_task_status
from app.tools import rollback_tools


router = APIRouter()


def _openai_agent_messages(task_id: str) -> list[dict]:
    return [
        AgentMessage.model_validate(item).to_openai_dict()
        for item in db.fetch_many("agent_messages", "task_id = ?", (task_id,))
    ]


def _step_recordings(task_id: str) -> list[dict]:
    result: dict[str, dict] = {}
    for frame in task_recording_service.list_recording_frames(task_id):
        step_id = str(frame.get("step_id") or "")
        if not step_id:
            continue
        _merge_step_recording(result, step_id, "", "", [frame])

    for message in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
        payload = message.get("structured_payload") or (message.get("metadata") or {}).get("structured_payload") or {}
        if not isinstance(payload, dict) or payload.get("kind") != task_recording_service.RECORDING_KIND:
            continue
        step_id = str(payload.get("step_id") or message.get("step_id") or "")
        if not step_id:
            continue
        frames = payload.get("frames")
        if isinstance(frames, list):
            recording_frames = [frame for frame in frames if isinstance(frame, dict)]
        else:
            recording_frames = [payload]
        _merge_step_recording(
            result,
            step_id,
            str(payload.get("tool_name") or ""),
            str(payload.get("agent") or message.get("from_agent") or ""),
            recording_frames,
        )
    return list(result.values())


def _merge_step_recording(
    result: dict[str, dict],
    step_id: str,
    tool_name: str,
    agent: str,
    frames: list[dict],
) -> None:
    item = result.setdefault(
        step_id,
        {
            "step_id": step_id,
            "tool_name": tool_name,
            "agent": agent,
            "frames": [],
        },
    )
    if tool_name and not item.get("tool_name"):
        item["tool_name"] = tool_name
    if agent and not item.get("agent"):
        item["agent"] = agent
    seen = {
        (
            str(frame.get("phase") or ""),
            str(frame.get("captured_at") or ""),
            str(frame.get("file_name") or frame.get("url") or ""),
        )
        for frame in item["frames"]
        if isinstance(frame, dict)
    }
    for frame in frames:
        key = (
            str(frame.get("phase") or ""),
            str(frame.get("captured_at") or ""),
            str(frame.get("file_name") or frame.get("url") or ""),
        )
        if key in seen:
            continue
        item["frames"].append(frame)
        seen.add(key)


@router.get("/tasks")
def tasks() -> list[Task]:
    return list_tasks()


@router.get("/tasks/{task_id}")
def task(task_id: str) -> Task:
    try:
        return get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.get("/tasks/{task_id}/timeline")
def timeline(task_id: str):
    return {
        "task": task_id,
        "messages": _openai_agent_messages(task_id),
        "reviews": db.fetch_many("safety_reviews", "task_id = ?", (task_id,)),
        "recordings": _step_recordings(task_id),
    }


@router.get("/tasks/{task_id}/recordings/{file_name}")
def recording(task_id: str, file_name: str):
    try:
        image, media_type = task_recording_service.read_recording_image(task_id, file_name)
    except FileNotFoundError:
        try:
            path = task_recording_service.resolve_recording_path(task_id, file_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Recording not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return Response(path.read_bytes(), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return Response(image, media_type=media_type)


@router.get("/tasks/{task_id}/agent-messages")
def agent_messages(task_id: str):
    return _openai_agent_messages(task_id)


@router.get("/tasks/{task_id}/safety-reviews")
def safety_reviews(task_id: str):
    return db.fetch_many("safety_reviews", "task_id = ?", (task_id,))


@router.post("/tasks/{task_id}/pause")
def pause(task_id: str):
    return set_task_status(task_id, TaskStatus.PAUSED)


@router.post("/tasks/{task_id}/resume")
def resume(task_id: str):
    return set_task_status(task_id, TaskStatus.EXECUTING_STEP)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str):
    return set_task_status(task_id, TaskStatus.CANCELLED)


@router.post("/tasks/{task_id}/rollback")
def rollback(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    if get_effective_settings().strict_state_machine:
        ensure_transition_allowed(task, TaskStatus.ROLLED_BACK)
    outcome = rollback_tools.execute_rollback(task_id)
    set_task_status(task_id, TaskStatus.ROLLED_BACK)
    return outcome


@router.get("/tasks/{task_id}/rollback-preview")
def rollback_preview(task_id: str):
    try:
        get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    return rollback_tools.build_rollback_plan(task_id)
