from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from app.api.task_public_views import (
    boundary_event as _boundary_event,
)
from app.api.task_public_views import (
    empty_completion_evidence as _empty_completion_evidence,
)
from app.api.task_public_views import (
    public_agent_message as _public_agent_message,
)
from app.api.task_public_views import (
    public_agent_messages as _public_agent_messages,
)
from app.api.task_public_views import (
    public_detail as _public_detail,
)
from app.api.task_public_views import (
    public_review as _public_review,
)
from app.api.task_public_views import (
    public_step_recordings as _public_step_recordings,
)
from app.api.task_public_views import (
    public_tool_call as _public_tool_call,
)
from app.api.task_public_views import (
    public_tool_result as _public_tool_result,
)
from app.api.task_public_views import (
    public_value as _public_value,
)
from app.api.task_public_views import (
    redacted_public_field as _redacted_public_field,
)
from app.api.task_public_views import (
    review_event_detail as _review_event_detail,
)
from app.api.task_public_views import (
    task_evidence_summary as _task_evidence_summary,
)
from app.api.task_public_views import (
    tool_progress_detail as _tool_progress_detail,
)
from app.commerce.entitlements import Feature, active_plan, has_feature
from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import Task, TaskStatus
from app.llm.registry import get_effective_settings
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    create_native_confirmation_challenge,
    enforce_native_confirmation_challenge_rate_limit,
    require_native_confirmation,
)
from app.services import task_artifact_service, task_recording_service
from app.services.task_explain_service import (
    build_task_completion_evidence,
    build_task_explain,
)
from app.services.task_service import cancel_task, get_task, list_tasks, pause_task, resume_task
from app.tools import rollback_tools

router = APIRouter()
BOUNDARY_EVENT_SOURCE_LIMIT = 500
BOUNDARY_EVENT_QUERY_CHUNK_SIZE = 400
ROLLBACK_NATIVE_ACTION = "rollback_task"


def _rollback_endpoint(task_id: str) -> str:
    return f"/api/tasks/{task_id}/rollback"


def _audit_export_enabled() -> bool:
    return has_feature(active_plan(get_effective_settings()), Feature.AUDIT_EXPORT)


def _audits_for_task(task_id: str, *, limit: int = BOUNDARY_EVENT_SOURCE_LIMIT) -> list[dict]:
    if not _audit_export_enabled():
        return []
    return db.fetch_many_by_fields("audit_events", {"task_id": task_id}, limit=limit)


@router.get("/tasks")
def tasks():
    task_items = list_tasks()
    task_ids = [task.id for task in task_items]
    source_records = _boundary_source_records_for_tasks(task_ids)
    boundary_events = _boundary_events_from_source_records(task_ids, source_records)
    completion_evidence = _completion_evidence_for_tasks(task_items, source_records)
    return [
        _task_payload(
            task,
            boundary_events=boundary_events.get(task.id, []),
            completion_evidence=completion_evidence.get(task.id),
        )
        for task in task_items
    ]


@router.get("/tasks/{task_id}")
def task(task_id: str):
    try:
        return _task_payload(get_task(task_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.get("/tasks/{task_id}/timeline")
def timeline(task_id: str):
    task_data = db.fetch_one("tasks", task_id)
    task_model = Task.model_validate(task_data) if task_data else None
    messages = db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    reviews = db.fetch_many_by_fields("safety_reviews", {"task_id": task_id})
    boundary_events = _boundary_events(task_id, messages=messages, reviews=reviews)
    completion_evidence = (
        build_task_completion_evidence(
            task_model,
            messages=messages,
            reviews=reviews,
            audits=_audits_for_task(task_id),
        )
        if task_model
        else _empty_completion_evidence()
    )
    return {
        "task": task_id,
        "messages": [_public_agent_message(item) for item in messages],
        "reviews": [_public_review(review) for review in reviews],
        "recordings": _public_step_recordings(task_id),
        "boundary_events": boundary_events,
        "evidence_summary": _task_evidence_summary(
            task_model, boundary_events, completion_evidence=completion_evidence
        ),
    }


def _task_payload(
    task: Task,
    *,
    boundary_events: list[dict] | None = None,
    completion_evidence: dict[str, Any] | None = None,
) -> dict:
    payload = task.model_dump(mode="json")
    payload["user_goal"] = _public_detail(str(payload.get("user_goal") or ""), limit=1000)
    payload["final_summary"] = _public_detail(str(payload.get("final_summary") or ""), limit=2000)
    payload["metadata"] = _redacted_public_field(payload.get("metadata") or {})
    events = boundary_events if boundary_events is not None else _boundary_events(task.id)
    completion = (
        completion_evidence
        if completion_evidence is not None
        else build_task_completion_evidence(task, audits=_audits_for_task(task.id))
    )
    payload["boundary_events"] = events
    payload["completion_evidence"] = completion
    payload["evidence_summary"] = _task_evidence_summary(task, events, completion_evidence=completion)
    payload["result_quality"] = payload["evidence_summary"]["result_quality"]
    return payload


def _boundary_events_for_tasks(task_ids: list[str]) -> dict[str, list[dict]]:
    unique_task_ids = [task_id for task_id in dict.fromkeys(task_ids) if task_id]
    if not unique_task_ids:
        return {}

    return _boundary_events_from_source_records(unique_task_ids, _boundary_source_records_for_tasks(unique_task_ids))


def _boundary_source_records_for_tasks(task_ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    unique_task_ids = [task_id for task_id in dict.fromkeys(task_ids) if task_id]
    if not unique_task_ids:
        return {"messages": {}, "reviews": {}, "audits": {}}
    audits: dict[str, list[dict]] = {}
    if _audit_export_enabled():
        audits = _recent_records_by_task("audit_events", unique_task_ids)
    return {
        "messages": _recent_records_by_task("agent_messages", unique_task_ids),
        "reviews": _recent_records_by_task("safety_reviews", unique_task_ids),
        "audits": audits,
    }


def _boundary_events_from_source_records(
    task_ids: list[str],
    source_records: dict[str, dict[str, list[dict]]],
) -> dict[str, list[dict]]:
    unique_task_ids = [task_id for task_id in dict.fromkeys(task_ids) if task_id]
    messages_by_task = source_records.get("messages", {})
    reviews_by_task = source_records.get("reviews", {})
    audits_by_task = source_records.get("audits", {})
    return {
        task_id: _boundary_events(
            task_id,
            messages=messages_by_task.get(task_id, []),
            reviews=reviews_by_task.get(task_id, []),
            audits=audits_by_task.get(task_id, []),
        )
        for task_id in unique_task_ids
    }


def _completion_evidence_for_tasks(
    tasks: list[Task],
    source_records: dict[str, dict[str, list[dict]]],
) -> dict[str, dict[str, Any]]:
    messages_by_task = source_records.get("messages", {})
    reviews_by_task = source_records.get("reviews", {})
    audits_by_task = source_records.get("audits", {})
    return {
        task.id: build_task_completion_evidence(
            task,
            messages=messages_by_task.get(task.id, []),
            reviews=reviews_by_task.get(task.id, []),
            audits=audits_by_task.get(task.id, []),
        )
        for task in tasks
    }


def _recent_records_by_task(
    table: str, task_ids: list[str], *, limit_per_task: int = BOUNDARY_EVENT_SOURCE_LIMIT
) -> dict[str, list[dict]]:
    if table not in {"agent_messages", "safety_reviews", "audit_events"}:
        raise ValueError(f"Unsupported boundary event table: {table}")
    if not task_ids:
        return {}

    grouped: dict[str, list[dict]] = {task_id: [] for task_id in task_ids}
    for start in range(0, len(task_ids), BOUNDARY_EVENT_QUERY_CHUNK_SIZE):
        chunk = task_ids[start : start + BOUNDARY_EVENT_QUERY_CHUNK_SIZE]
        placeholders = ", ".join("?" for _ in chunk)
        query_template = """
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
                FROM {table_name}
                WHERE task_id IN ({task_placeholders})
            )
            WHERE row_number <= ?
            ORDER BY task_id ASC, created_at DESC, id DESC
        """
        query = query_template.format(table_name=table, task_placeholders=placeholders)  # noqa: S608
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
        messages if messages is not None else db.fetch_many_by_fields("agent_messages", {"task_id": task_id}, limit=500)
    )
    reviews = (
        reviews if reviews is not None else db.fetch_many_by_fields("safety_reviews", {"task_id": task_id}, limit=500)
    )
    if not _audit_export_enabled():
        audits = []
    elif audits is None:
        audits = db.fetch_many_by_fields("audit_events", {"task_id": task_id}, limit=500)
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
                    _tool_progress_detail(tool, status),
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
                    "Planner steps were checked against registry risk and tool metadata.",
                    created_at,
                    severity="info",
                    payload=payload,
                )
            )
        elif payload.get("kind") == "context_compact_boundary" or metadata.get("context_boundary"):
            events.append(
                _boundary_event(
                    message.get("id"),
                    "context_boundary",
                    "Context boundary",
                    "Context projection boundary was recorded.",
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
                    "Model boundary blocked an unsafe transfer.",
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
            events.append(
                _boundary_event(
                    review.get("id"),
                    "post_tool_review" if target_type == "tool_result" else "safety_review",
                    "Post-tool review" if target_type == "tool_result" else "Safety review",
                    _review_event_detail(review),
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
                    payload={**payload, "event_type": event_type},
                )
            )
        elif event_type.startswith("model_boundary"):
            events.append(
                _boundary_event(
                    audit.get("id"),
                    "model_boundary_denied",
                    "Model boundary denied",
                    "Model boundary blocked an unsafe transfer.",
                    str(audit.get("created_at") or ""),
                    step_id=payload.get("step_id"),
                    severity="danger",
                    payload={**payload, "event_type": event_type},
                )
            )

    deduped = {str(event["id"]): event for event in events if event.get("id")}
    ordered = sorted(deduped.values(), key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return ordered[-20:]


@router.get("/tasks/{task_id}/replay")
def replay(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    task_payload = _task_payload(task)
    messages = sorted(
        _public_agent_messages(task_id), key=lambda item: (item.get("created_at") or "", item.get("id") or "")
    )
    tool_calls = sorted(
        db.fetch_many_by_fields("tool_calls", {"task_id": task_id}, limit=1000),
        key=lambda item: item.get("created_at") or "",
    )
    results_by_call = _tool_results_by_call_id([str(call.get("id") or "") for call in tool_calls])
    return {
        "task": task_payload,
        "result_quality": task_payload["result_quality"],
        "events": messages,
        "tool_calls": [_public_tool_call(call) for call in tool_calls],
        "tool_results": [
            _public_tool_result(results_by_call[call["id"]]) for call in tool_calls if call.get("id") in results_by_call
        ],
        "recordings": _public_step_recordings(task_id),
        "raw_redacted": True,
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
        if isinstance(payload, dict) and (
            payload.get("kind") == "tool_progress" or metadata.get("event_type") == "tool.progress"
        ):
            progress_events.append(
                {
                    "id": message.get("id"),
                    "created_at": message.get("created_at"),
                    "step_id": message.get("step_id"),
                    "has_tool_call": bool(message.get("tool_call_id")),
                    "payload": _public_value(payload),
                }
            )
    progress_events.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    return {"task_id": task.id, "status": task.status, "progress": progress_events, "count": len(progress_events)}


@router.get("/tasks/{task_id}/artifacts")
def artifacts(task_id: str):
    """Desktop-only workspace view: real local artifact paths for this task.

    Accepts either a task id or a run id (the desktop merges run/task views and
    uses run ids for engine-backed tasks). Mobile/public clients must keep
    using the redacted timeline/replay payloads.
    """
    resolved_task_id = task_id
    try:
        get_task(task_id)
    except KeyError:
        run = db.fetch_one("runs", task_id)
        linked_task_id = str((run or {}).get("task_id") or "")
        if not linked_task_id:
            raise HTTPException(status_code=404, detail="Task not found") from None
        resolved_task_id = linked_task_id
    return task_artifact_service.collect_task_artifacts(resolved_task_id)


@router.get("/tasks/{task_id}/explain")
def explain(task_id: str):
    try:
        return build_task_explain(task_id, audits=_audits_for_task(task_id))
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
    return _public_agent_messages(task_id)


@router.get("/tasks/{task_id}/safety-reviews")
def safety_reviews(task_id: str):
    return [_public_review(review) for review in db.fetch_many_by_fields("safety_reviews", {"task_id": task_id})]


@router.post("/tasks/{task_id}/pause")
async def pause(task_id: str):
    return await pause_task(task_id)


@router.post("/tasks/{task_id}/resume")
async def resume(task_id: str):
    return resume_task(task_id)


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str):
    return await cancel_task(task_id)


@router.post("/tasks/{task_id}/rollback")
def rollback(
    task_id: str,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    native_confirmation = require_native_confirmation(
        action=ROLLBACK_NATIVE_ACTION,
        endpoint=_rollback_endpoint(task_id),
        approval_id=task_id,
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
        preview_hmac=_rollback_preview_hmac(task_id),
    )
    if task.status not in {TaskPhase.COMPLETED, TaskPhase.FAILED}:
        raise StateTransitionError(task.status.value, TaskStatus.ROLLED_BACK.value)
    db.require_sensitive_integrity_ok()
    outcome = rollback_tools.execute_rollback(task_id)
    outcome["native_confirmation"] = {
        "confirmation_id": native_confirmation.get("confirmation_id"),
        "desktop_native_confirmed": True,
    }
    safe_transition(task, TaskStatus.ROLLED_BACK, actor="TaskService", strict=True)
    return outcome


@router.post("/tasks/{task_id}/rollback/native-confirmation-challenge")
def rollback_native_confirmation_challenge(task_id: str, request: Request):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    if task.status not in {TaskPhase.COMPLETED, TaskPhase.FAILED}:
        raise StateTransitionError(task.status.value, TaskStatus.ROLLED_BACK.value)
    enforce_native_confirmation_challenge_rate_limit(_client_scope(request))
    db.require_sensitive_integrity_ok()
    return create_native_confirmation_challenge(
        action=ROLLBACK_NATIVE_ACTION,
        endpoint=_rollback_endpoint(task_id),
        approval_id=task_id,
        preview_hmac=_rollback_preview_hmac(task_id),
    )


@router.get("/tasks/{task_id}/rollback-preview")
def rollback_preview(task_id: str):
    try:
        get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    return rollback_tools.build_rollback_plan(task_id)


def _rollback_preview_hmac(task_id: str) -> str:
    return sha256(_canonical_json(rollback_tools.build_rollback_plan(task_id)).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _client_scope(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return (host or "unknown").strip().lower() or "unknown"
