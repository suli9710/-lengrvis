from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import AgentMessage, Task, TaskStatus
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase
from app.policy.redaction import redact_text, redact_value
from app.services import task_recording_service
from app.services.task_explain_service import build_task_explain
from app.services.task_service import get_task, list_tasks, resume_task, set_task_status
from app.tools import rollback_tools


router = APIRouter()
BOUNDARY_EVENT_SOURCE_LIMIT = 500
BOUNDARY_EVENT_QUERY_CHUNK_SIZE = 400
EVIDENCE_PRIVACY_NOTE = (
    "Only status, counts, and redacted event labels are exposed; raw payloads, file contents, and hidden prompts are omitted."
)
REVIEW_EVENT_KINDS = {"post_tool_review", "safety_review"}
CAPABILITY_BOUNDARY_KINDS = {"context_boundary", "context_projection", "model_boundary_denied", "tool_contract"}
SAFE_BOUNDARY_PAYLOAD_KEYS = {
    "event_type",
    "kind",
    "risk_level",
    "source",
    "status",
    "strategy",
    "target_type",
    "tool_name",
    "tokens_saved",
    "verdict",
}


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
    return {
        "task": task_id,
        "messages": [AgentMessage.model_validate(item).to_openai_dict() for item in messages],
        "reviews": [_public_review(review) for review in reviews],
        "recordings": _step_recordings(task_id),
        "boundary_events": boundary_events,
        "evidence_summary": _task_evidence_summary(task_model, boundary_events),
    }


def _task_payload(task: Task, *, boundary_events: list[dict] | None = None) -> dict:
    payload = task.model_dump(mode="json")
    events = boundary_events if boundary_events is not None else _boundary_events(task.id)
    payload["boundary_events"] = events
    payload["evidence_summary"] = _task_evidence_summary(task, events)
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


def _task_evidence_summary(task: Task | None, boundary_events: list[dict]) -> dict[str, Any]:
    counts = {
        "events": len(boundary_events),
        "review_checkpoints": sum(1 for event in boundary_events if event.get("kind") in REVIEW_EVENT_KINDS),
        "capability_boundaries": sum(1 for event in boundary_events if event.get("kind") in CAPABILITY_BOUNDARY_KINDS),
        "tool_updates": sum(1 for event in boundary_events if event.get("kind") == "tool_progress"),
        "items_needing_attention": sum(
            1 for event in boundary_events if event.get("severity") in {"warning", "danger", "critical", "high"}
        ),
    }
    evidence = []
    if counts["review_checkpoints"]:
        evidence.append(f"{counts['review_checkpoints']} review checkpoint(s) recorded.")
    if counts["capability_boundaries"]:
        evidence.append(f"{counts['capability_boundaries']} capability boundary event(s) recorded.")
    if counts["tool_updates"]:
        evidence.append(f"{counts['tool_updates']} tool progress update(s) recorded.")
    if counts["items_needing_attention"]:
        evidence.append(f"{counts['items_needing_attention']} item(s) need review before trusting the result.")
    if not evidence:
        evidence.append("No review or boundary evidence has been recorded yet.")

    status = _task_status_summary(task)
    return {
        "status": status,
        "evidence": evidence[:4],
        "next_step": _task_next_step(task, counts),
        "counts": counts,
        "privacy_note": EVIDENCE_PRIVACY_NOTE,
    }


def _task_status_summary(task: Task | None) -> str:
    if task is None:
        return "Task record unavailable."
    status = _enum_text(task.status)
    stage = _enum_text(task.execution_stage)
    if stage == "awaiting_approval":
        return "Waiting for approval before the agent continues."
    if status == "completed":
        return "Completed with an auditable evidence trail."
    if status == "failed":
        return "Failed; review the evidence trail before retrying."
    if status == "cancelled":
        return "Cancelled before completion."
    if status == "execution":
        return "Running; evidence is being collected as steps finish."
    if status in {"planning", "plan_review", "consultation", "final_review"}:
        return "Preparing or reviewing the plan."
    return f"Task status: {status or 'unknown'}."


def _task_next_step(task: Task | None, counts: dict[str, int]) -> str:
    status = _enum_text(task.status) if task else ""
    stage = _enum_text(task.execution_stage) if task else ""
    if stage == "awaiting_approval":
        return "Review the pending approval summary before allowing live execution."
    if counts["items_needing_attention"]:
        return "Review the flagged checkpoint and decide whether to continue, revise, or stop."
    if status == "completed":
        return "Open the task explanation to inspect the decision chain."
    if counts["events"] == 0:
        return "Let the run continue until the first reviewed step is recorded."
    return "Keep monitoring; the summary will update as more steps are reviewed."


def _enum_text(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


def _tool_progress_detail(tool: str, status: str) -> str:
    parts = [part for part in [tool, status] if part]
    return " ".join(parts) if parts else "Tool progress was recorded."


def _review_event_detail(review: dict[str, Any]) -> str:
    target_type = str(review.get("target_type") or "review")
    verdict = str(review.get("verdict") or "recorded")
    risk_level = str(review.get("risk_level") or "")
    suffix = f" at {risk_level}" if risk_level else ""
    return f"{target_type}: {verdict}{suffix}."


def _public_review(review: dict[str, Any]) -> dict[str, Any]:
    reasons = review.get("reasons") if isinstance(review.get("reasons"), list) else []
    required_changes = review.get("required_changes") if isinstance(review.get("required_changes"), list) else []
    return {
        "id": str(review.get("id") or ""),
        "step_id": review.get("step_id"),
        "target_type": str(review.get("target_type") or ""),
        "verdict": str(review.get("verdict") or ""),
        "risk_level": str(review.get("risk_level") or ""),
        "summary": _review_event_detail(review),
        "reason_count": len(reasons),
        "required_change_count": len(required_changes),
        "has_user_confirmation_message": bool(review.get("user_confirmation_message")),
        "has_safe_alternative": bool(review.get("safe_alternative")),
        "created_at": str(review.get("created_at") or ""),
    }


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
        "detail": _public_detail(detail),
        "severity": severity,
        "step_id": str(step_id or ""),
        "created_at": created_at,
        "payload": _safe_boundary_payload(kind, payload),
    }


def _public_detail(detail: str, *, limit: int = 220) -> str:
    text = redact_text(str(detail or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _safe_boundary_payload(kind: str, payload: dict | None) -> dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
    safe: dict[str, Any] = {
        "kind": kind,
        "redacted": True,
        "field_count": len([key for key in raw_payload if not str(key).startswith("_")]),
    }
    for key in SAFE_BOUNDARY_PAYLOAD_KEYS:
        if key in raw_payload:
            safe[key] = _safe_boundary_value(raw_payload.get(key))
    for key, count_key in (
        ("reasons", "reason_count"),
        ("required_changes", "required_change_count"),
        ("diff_preview", "preview_field_count"),
        ("engineering_boundary", "boundary_field_count"),
    ):
        value = raw_payload.get(key)
        if isinstance(value, list):
            safe[count_key] = len(value)
        elif isinstance(value, dict):
            safe[count_key] = len(value)
    return safe


def _safe_boundary_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _public_detail(value, limit=120)
    redacted = redact_value(value)
    if isinstance(redacted, dict):
        return {"field_count": len(redacted)}
    if isinstance(redacted, list):
        return {"count": len(redacted)}
    return redacted


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
