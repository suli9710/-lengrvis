from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import AgentMessage, Task, TaskStatus
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase
from app.services import task_recording_service
from app.services.task_explain_service import build_task_explain
from app.services.task_service import get_task, list_tasks, resume_task, set_task_status
from app.tools import rollback_tools


router = APIRouter()
BOUNDARY_EVENT_SOURCE_LIMIT = 500
BOUNDARY_EVENT_QUERY_CHUNK_SIZE = 400


def _openai_agent_messages(task_id: str) -> list[dict]:
    return [
        AgentMessage.model_validate(item).to_openai_dict()
        for item in db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    ]


def _step_recordings(task_id: str) -> list[dict]:
    result: dict[str, dict] = {}
    for frame in task_recording_service.list_recording_frames(task_id):
        step_id = str(frame.get("step_id") or "")
        if not step_id:
            continue
        _merge_step_recording(result, step_id, "", "", [frame])

    for message in reversed(db.fetch_many_by_fields("agent_messages", {"task_id": task_id}, limit=1000)):
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
def tasks():
    task_items = list_tasks()
    boundary_events = _boundary_events_for_tasks([task.id for task in task_items])
    return [_task_payload(task, boundary_events=boundary_events.get(task.id, [])) for task in task_items]


@router.get("/tasks/{task_id}")
def task(task_id: str) -> Task:
    try:
        return get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.get("/tasks/{task_id}/timeline")
def timeline(task_id: str):
    messages = db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    reviews = db.fetch_many_by_fields("safety_reviews", {"task_id": task_id})
    return {
        "task": task_id,
        "messages": [AgentMessage.model_validate(item).to_openai_dict() for item in messages],
        "reviews": reviews,
        "recordings": _step_recordings(task_id),
        "boundary_events": _boundary_events(task_id, messages=messages, reviews=reviews),
    }


def _task_payload(task: Task, *, boundary_events: list[dict] | None = None) -> dict:
    payload = task.model_dump(mode="json")
    payload["boundary_events"] = boundary_events if boundary_events is not None else _boundary_events(task.id)
    return payload


def _boundary_events_for_tasks(task_ids: list[str]) -> dict[str, list[dict]]:
    unique_task_ids = [task_id for task_id in dict.fromkeys(task_ids) if task_id]
    if not unique_task_ids:
        return {}

    messages_by_task = _recent_records_by_task("agent_messages", unique_task_ids)
    reviews_by_task = _recent_records_by_task("safety_reviews", unique_task_ids)
    audits_by_task = _recent_records_by_task("audit_events", unique_task_ids)
    return {
        task_id: _boundary_events(
            task_id,
            messages=messages_by_task.get(task_id, []),
            reviews=reviews_by_task.get(task_id, []),
            audits=audits_by_task.get(task_id, []),
        )
        for task_id in unique_task_ids
    }


def _recent_records_by_task(table: str, task_ids: list[str], *, limit_per_task: int = BOUNDARY_EVENT_SOURCE_LIMIT) -> dict[str, list[dict]]:
    if table not in {"agent_messages", "safety_reviews", "audit_events"}:
        raise ValueError(f"Unsupported boundary event table: {table}")
    if not task_ids:
        return {}

    grouped: dict[str, list[dict]] = {task_id: [] for task_id in task_ids}
    for start in range(0, len(task_ids), BOUNDARY_EVENT_QUERY_CHUNK_SIZE):
        chunk = task_ids[start : start + BOUNDARY_EVENT_QUERY_CHUNK_SIZE]
        placeholders = ", ".join("?" for _ in chunk)
        query = f"""
            SELECT task_id, data
            FROM (
                SELECT
                    task_id,
                    data,
                    created_at,
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY task_id
                        ORDER BY created_at DESC, id DESC
                    ) AS row_number
                FROM {table}
                WHERE task_id IN ({placeholders})
            )
            WHERE row_number <= ?
            ORDER BY task_id ASC, created_at DESC, id DESC
        """
        with db.connect() as conn:
            rows = conn.execute(query, (*chunk, limit_per_task)).fetchall()
        for row in rows:
            item = json.loads(row["data"])
            source_task_id = str(row["task_id"] or "")
            if source_task_id in grouped:
                item["task_id"] = source_task_id
                grouped[source_task_id].append(item)
    return grouped


def _boundary_events(
    task_id: str,
    *,
    messages: list[dict] | None = None,
    reviews: list[dict] | None = None,
    audits: list[dict] | None = None,
) -> list[dict]:
    messages = (
        messages
        if messages is not None
        else db.fetch_many_by_fields("agent_messages", {"task_id": task_id}, limit=500)
    )
    reviews = (
        reviews
        if reviews is not None
        else db.fetch_many_by_fields("safety_reviews", {"task_id": task_id}, limit=500)
    )
    audits = (
        audits
        if audits is not None
        else db.fetch_many_by_fields("audit_events", {"task_id": task_id}, limit=500)
    )
    events: list[dict] = []

    for message in messages:
        payload = message.get("structured_payload") or (message.get("metadata") or {}).get("structured_payload") or {}
        metadata = message.get("metadata") or {}
        event_type = str(payload.get("event_type") or metadata.get("event_type") or metadata.get("message_type") or "")
        content = str(message.get("content") or "")
        created_at = str(message.get("created_at") or "")
        if event_type == "tool.progress" or payload.get("kind") == "tool_progress":
            status = str(payload.get("status") or metadata.get("tool_status") or "")
            tool = str(payload.get("tool_name") or metadata.get("tool_name") or "")
            events.append(
                _boundary_event(
                    message.get("id"),
                    "tool_progress",
                    "Tool progress",
                    f"{tool} {status}".strip() or content,
                    created_at,
                    step_id=message.get("step_id"),
                    severity="info",
                    payload=payload,
                )
            )
        elif event_type == "plan.contract_annotated":
            events.append(
                _boundary_event(
                    message.get("id"),
                    "tool_contract",
                    "Tool contract boundary",
                    content or "Planner steps were annotated with registry risk and tool metadata.",
                    created_at,
                    severity="info",
                    payload=payload,
                )
            )
        elif payload.get("kind") == "context_compact_boundary" or metadata.get("context_boundary"):
            boundary = str(metadata.get("context_boundary") or payload.get("context_boundary") or "")
            events.append(
                _boundary_event(
                    message.get("id"),
                    "context_boundary",
                    "Context boundary",
                    boundary or content or "Context projection boundary persisted.",
                    created_at,
                    severity="info",
                    payload=payload or metadata,
                )
            )
        elif "model boundary" in content.lower() or event_type.startswith("model_boundary"):
            events.append(
                _boundary_event(
                    message.get("id"),
                    "model_boundary_denied",
                    "Model boundary denied",
                    content,
                    created_at,
                    step_id=message.get("step_id"),
                    severity="danger",
                    payload=payload or metadata,
                )
            )

    for review in reviews:
        target_type = str(review.get("target_type") or "")
        verdict = str(review.get("verdict") or "")
        if target_type == "tool_result" or verdict in {"deny", "needs_user_approval"}:
            reasons = review.get("reasons") if isinstance(review.get("reasons"), list) else []
            events.append(
                _boundary_event(
                    review.get("id"),
                    "post_tool_review" if target_type == "tool_result" else "safety_review",
                    "Post-tool review" if target_type == "tool_result" else "Safety review",
                    " ".join(str(reason) for reason in reasons[:2]) or verdict,
                    str(review.get("created_at") or ""),
                    step_id=review.get("step_id"),
                    severity="danger" if verdict == "deny" else "warning",
                    payload=review,
                )
            )

    for audit in audits:
        event_type = str(audit.get("event_type") or "")
        payload = audit.get("payload") if isinstance(audit.get("payload"), dict) else {}
        if event_type.startswith("context."):
            strategy = str(payload.get("strategy") or payload.get("source") or event_type)
            saved = payload.get("tokens_saved")
            detail = f"{strategy}; saved {saved} tokens" if saved is not None else strategy
            events.append(
                _boundary_event(
                    audit.get("id"),
                    "context_projection",
                    "Context projection",
                    detail,
                    str(audit.get("created_at") or ""),
                    severity="info",
                    payload=payload,
                )
            )
        elif event_type.startswith("model_boundary"):
            events.append(
                _boundary_event(
                    audit.get("id"),
                    "model_boundary_denied",
                    "Model boundary denied",
                    str(payload.get("error") or payload.get("reason") or event_type),
                    str(audit.get("created_at") or ""),
                    step_id=payload.get("step_id"),
                    severity="danger",
                    payload=payload,
                )
            )

    deduped = {str(event["id"]): event for event in events if event.get("id")}
    ordered = sorted(deduped.values(), key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return ordered[-20:]


def _boundary_event(
    event_id,
    kind: str,
    title: str,
    detail: str,
    created_at: str,
    *,
    step_id=None,
    severity: str = "info",
    payload: dict | None = None,
) -> dict:
    return {
        "id": str(event_id or f"{kind}-{created_at}"),
        "kind": kind,
        "title": title,
        "detail": detail,
        "severity": severity,
        "step_id": str(step_id or ""),
        "created_at": created_at,
        "payload": payload or {},
    }


@router.get("/tasks/{task_id}/replay")
def replay(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    messages = sorted(_openai_agent_messages(task_id), key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    tool_calls = sorted(
        db.fetch_many_by_fields("tool_calls", {"task_id": task_id}, limit=1000),
        key=lambda item: item.get("created_at") or "",
    )
    results_by_call = _tool_results_by_call_id([str(call.get("id") or "") for call in tool_calls])
    return {
        "task": task.model_dump(mode="json"),
        "events": messages,
        "tool_calls": tool_calls,
        "tool_results": [results_by_call[call["id"]] for call in tool_calls if call.get("id") in results_by_call],
        "recordings": _step_recordings(task_id),
    }


def _tool_results_by_call_id(call_ids: list[str]) -> dict[str, dict]:
    results_by_call: dict[str, dict] = {}
    unique_ids = [call_id for call_id in dict.fromkeys(call_ids) if call_id]
    for start in range(0, len(unique_ids), 400):
        chunk = unique_ids[start : start + 400]
        for result in db.fetch_many_in("tool_results", "tool_call_id", tuple(chunk), limit=len(chunk)):
            tool_call_id = str(result.get("tool_call_id") or "")
            if tool_call_id and tool_call_id not in results_by_call:
                results_by_call[tool_call_id] = result
    return results_by_call


@router.get("/tasks/{task_id}/progress")
def progress(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    messages = db.fetch_many_by_fields("agent_messages", {"task_id": task_id}, limit=1000)
    progress_events = []
    for message in messages:
        payload = message.get("structured_payload") or (message.get("metadata") or {}).get("structured_payload") or {}
        metadata = message.get("metadata") or {}
        if isinstance(payload, dict) and (payload.get("kind") == "tool_progress" or metadata.get("event_type") == "tool.progress"):
            progress_events.append(
                {
                    "id": message.get("id"),
                    "created_at": message.get("created_at"),
                    "step_id": message.get("step_id"),
                    "tool_call_id": message.get("tool_call_id"),
                    "payload": payload,
                }
            )
    progress_events.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return {"task_id": task.id, "status": task.status, "progress": progress_events, "count": len(progress_events)}


@router.get("/tasks/{task_id}/explain")
def explain(task_id: str):
    try:
        return build_task_explain(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


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
    return db.fetch_many_by_fields("safety_reviews", {"task_id": task_id})


@router.post("/tasks/{task_id}/pause")
def pause(task_id: str):
    return set_task_status(task_id, TaskStatus.PAUSED)


@router.post("/tasks/{task_id}/resume")
def resume(task_id: str):
    return resume_task(task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str):
    return set_task_status(task_id, TaskStatus.CANCELLED)


@router.post("/tasks/{task_id}/rollback")
def rollback(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    if task.status not in {TaskPhase.COMPLETED, TaskPhase.FAILED}:
        raise StateTransitionError(task.status.value, TaskStatus.ROLLED_BACK.value)
    outcome = rollback_tools.execute_rollback(task_id)
    safe_transition(task, TaskStatus.ROLLED_BACK, actor="TaskService", strict=True)
    return outcome


@router.get("/tasks/{task_id}/rollback-preview")
def rollback_preview(task_id: str):
    try:
        get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    return rollback_tools.build_rollback_plan(task_id)
