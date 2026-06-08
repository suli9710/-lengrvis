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
from app.policy.redaction import redact_public_text, redact_value
from app.services import task_recording_service
from app.services.task_explain_service import build_task_completion_evidence, build_task_explain
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
PUBLIC_PAYLOAD_TEXT_KEYS = {
    "args",
    "argument",
    "arguments",
    "body",
    "content",
    "detail",
    "details",
    "hidden_prompt",
    "html",
    "input",
    "inputs",
    "message",
    "messages",
    "observation",
    "output",
    "parameter",
    "parameters",
    "params",
    "prompt",
    "raw",
    "reason",
    "system_prompt",
    "text",
    "value",
}
PUBLIC_PAYLOAD_ERROR_KEYS = {
    "error",
    "errors",
    "exception",
    "exceptions",
    "stack",
    "stack_trace",
    "stderr",
    "traceback",
}
PUBLIC_PAYLOAD_STATUS_KEYS = {
    "status",
    "tool_status",
}
PUBLIC_ERROR_SUMMARIES = {
    "blocked": "The tool was blocked by a permission or policy check.",
    "invalid_input": "The tool could not run because required input was invalid or missing.",
    "not_found": "The tool could not find a requested resource.",
    "timeout": "The tool timed out before it finished.",
    "network": "The tool hit a network or service connection problem.",
    "execution_error": "The tool reported an error. Private diagnostic details are hidden from replay.",
}
PUBLIC_ERROR_NEXT_STEPS = {
    "blocked": "Review the approval or permission settings before retrying.",
    "invalid_input": "Check the step inputs, then retry the task.",
    "not_found": "Confirm the requested file, page, or resource is available, then retry.",
    "timeout": "Retry when the device or service is responsive.",
    "network": "Check the connection or service status, then retry.",
    "execution_error": "Check the step inputs, then retry or open local diagnostics for details.",
}
PUBLIC_STATUS_ALIASES = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "complete": "completed",
    "completed": "completed",
    "created": "created",
    "denied": "blocked",
    "done": "completed",
    "error": "failed",
    "failed": "failed",
    "failure": "failed",
    "in_progress": "running",
    "ok": "completed",
    "pending": "pending",
    "queued": "pending",
    "retry": "retrying",
    "retrying": "retrying",
    "running": "running",
    "skipped": "skipped",
    "started": "started",
    "success": "completed",
    "succeeded": "completed",
    "timeout": "failed",
    "timed_out": "failed",
    "waiting": "waiting",
}


def _openai_agent_messages(task_id: str) -> list[dict]:
    return [
        AgentMessage.model_validate(item).to_openai_dict()
        for item in db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    ]


def _public_agent_messages(task_id: str) -> list[dict]:
    return [
        _public_agent_message(item)
        for item in db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    ]


def _public_agent_message(item: dict) -> dict:
    model = AgentMessage.model_validate(item)
    message = model.to_openai_dict()
    safe = dict(message)
    safe["content"] = _public_agent_message_content(model)
    for key in ("metadata", "structured_payload", "tool_calls"):
        if key in safe:
            safe[key] = _public_value(safe.get(key))
    safe["redacted"] = True
    return safe


def _public_agent_message_content(message: AgentMessage) -> str:
    payload = message.structured_payload if isinstance(message.structured_payload, dict) else {}
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    event_type = str(payload.get("event_type") or metadata.get("event_type") or "").lower()
    if event_type.startswith("model_boundary") or message.from_agent.lower() == "modelboundary":
        return "Model boundary event recorded."
    if event_type == "tool.progress" or payload.get("kind") == "tool_progress":
        return _tool_progress_detail(str(payload.get("tool_name") or metadata.get("tool_name") or ""), str(payload.get("status") or ""))
    if message.from_agent.lower() in {"user", "human"}:
        return "User message recorded."
    return "Agent message recorded."


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


def _public_step_recordings(task_id: str) -> list[dict]:
    return [_public_step_recording(item) for item in _step_recordings(task_id)]


def _public_step_recording(item: dict) -> dict:
    frames = [frame for frame in item.get("frames") or [] if isinstance(frame, dict)]
    return {
        "step_id": str(item.get("step_id") or ""),
        "tool_name": str(item.get("tool_name") or ""),
        "agent": str(item.get("agent") or ""),
        "frame_count": len(frames),
        "frames": [_public_recording_frame(frame) for frame in frames],
        "redacted": True,
        "privacy_note": "Recording images are hidden from the timeline; open them only through an explicit local review flow.",
    }


def _public_recording_frame(frame: dict) -> dict:
    return {
        "kind": str(frame.get("kind") or task_recording_service.RECORDING_KIND),
        "phase": str(frame.get("phase") or ""),
        "ok": bool(frame.get("ok", False)),
        "enabled": bool(frame.get("enabled", False)),
        "captured_at": str(frame.get("captured_at") or ""),
        "mime_type": str(frame.get("mime_type") or ""),
        "width": int(frame.get("width") or 0),
        "height": int(frame.get("height") or 0),
        "has_image": bool(frame.get("url") or frame.get("file_name") or frame.get("recording_id")),
        "image_redacted": True,
    }


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
        build_task_completion_evidence(task_model, messages=messages, reviews=reviews) if task_model else _empty_completion_evidence()
    )
    return {
        "task": task_id,
        "messages": [_public_agent_message(item) for item in messages],
        "reviews": [_public_review(review) for review in reviews],
        "recordings": _public_step_recordings(task_id),
        "boundary_events": boundary_events,
        "evidence_summary": _task_evidence_summary(task_model, boundary_events, completion_evidence=completion_evidence),
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
    completion = completion_evidence if completion_evidence is not None else build_task_completion_evidence(task)
    payload["boundary_events"] = events
    payload["completion_evidence"] = completion
    payload["evidence_summary"] = _task_evidence_summary(task, events, completion_evidence=completion)
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
    return {
        "messages": _recent_records_by_task("agent_messages", unique_task_ids),
        "reviews": _recent_records_by_task("safety_reviews", unique_task_ids),
        "audits": _recent_records_by_task("audit_events", unique_task_ids),
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


def _task_evidence_summary(
    task: Task | None,
    boundary_events: list[dict],
    *,
    completion_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    completion_evidence = completion_evidence if completion_evidence is not None else (
        build_task_completion_evidence(task) if task else _empty_completion_evidence()
    )
    status = _task_status_summary(task, completion_evidence)
    return {
        "status": status,
        "evidence": evidence[:4],
        "next_step": _task_next_step(task, counts, completion_evidence),
        "counts": counts,
        "completion_evidence": completion_evidence,
        "privacy_note": EVIDENCE_PRIVACY_NOTE,
    }


def _empty_completion_evidence() -> dict[str, Any]:
    return {
        "level": "submission",
        "result_verified": False,
        "result_artifacts": [],
        "missing": ["task record"],
        "signoff": False,
    }


def _task_status_summary(task: Task | None, completion_evidence: dict[str, Any]) -> str:
    if task is None:
        return "Task record unavailable."
    status = _enum_text(task.status)
    stage = _enum_text(task.execution_stage)
    if stage == "awaiting_approval":
        return "Waiting for approval before the agent continues."
    if status == "completed":
        if _completion_result_verified(task, completion_evidence):
            return "Completed with verified result evidence; manual sign-off is still separate."
        return "Completed, but result evidence is not verified yet."
    if status == "failed":
        return "Failed; review the evidence trail before retrying."
    if status == "cancelled":
        return "Cancelled before completion."
    if status == "execution":
        return "Running; evidence is being collected as steps finish."
    if status in {"planning", "plan_review", "consultation", "final_review"}:
        return "Preparing or reviewing the plan."
    return f"Task status: {status or 'unknown'}."


def _task_next_step(task: Task | None, counts: dict[str, int], completion_evidence: dict[str, Any]) -> str:
    status = _enum_text(task.status) if task else ""
    stage = _enum_text(task.execution_stage) if task else ""
    if stage == "awaiting_approval":
        return "Review the pending approval summary before allowing live execution."
    if counts["items_needing_attention"]:
        return "Review the flagged checkpoint and decide whether to continue, revise, or stop."
    if status == "completed":
        if _completion_result_verified(task, completion_evidence):
            return "Open the task explanation to inspect the verified result evidence before manual sign-off."
        return "Open the task explanation and collect missing result evidence before treating the result as done."
    if counts["events"] == 0:
        return "Let the run continue until the first reviewed step is recorded."
    return "Keep monitoring; the summary will update as more steps are reviewed."


def _completion_result_verified(task: Task | None, completion_evidence: dict[str, Any]) -> bool:
    if task is None or _enum_text(task.status) != "completed":
        return False
    return (
        completion_evidence.get("level") == "completed_result"
        and completion_evidence.get("result_verified") is True
        and completion_evidence.get("signoff") is False
    )


def _enum_text(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


def _tool_progress_detail(tool: str, status: str) -> str:
    parts = [part for part in [tool, _public_status_label(status)] if part]
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
        "reasons": [],
        "reason_count": len(reasons),
        "required_change_count": len(required_changes),
        "has_user_confirmation_message": bool(review.get("user_confirmation_message")),
        "has_safe_alternative": bool(review.get("safe_alternative")),
        "safe_alternative": "",
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
    text = redact_public_text(str(detail or "")).strip()
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
            if key == "status":
                safe[key] = _public_status_label(str(raw_payload.get(key) or ""))
            else:
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


def _public_value(value: Any, key: str = "") -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key in PUBLIC_PAYLOAD_ERROR_KEYS:
        return _public_error_metadata(value)
    if normalized_key in PUBLIC_PAYLOAD_STATUS_KEYS and isinstance(value, str):
        return _public_status_label(value)
    if normalized_key in PUBLIC_PAYLOAD_TEXT_KEYS:
        return _redacted_public_field(value)
    redacted = redact_value(value)
    if isinstance(redacted, dict):
        return {str(item_key): _public_value(item, str(item_key)) for item_key, item in redacted.items()}
    if isinstance(redacted, list):
        return [_public_value(item, key) for item in redacted]
    if isinstance(redacted, tuple):
        return [_public_value(item, key) for item in redacted]
    if isinstance(redacted, set):
        return [_public_value(item, key) for item in sorted(redacted, key=str)]
    if isinstance(redacted, str):
        return redact_public_text(redacted)
    return redacted


def _redacted_public_field(value: Any) -> Any:
    if isinstance(value, dict):
        return {"redacted": True, "field_count": len(value)}
    if isinstance(value, (list, tuple, set)):
        return {"redacted": True, "count": len(value)}
    if isinstance(value, str):
        return "[REDACTED_TEXT]"
    if value is None:
        return None
    return "[REDACTED_VALUE]"


def _public_status_label(status: str) -> str:
    normalized = str(status or "").strip().replace("-", "_").casefold()
    if not normalized:
        return ""
    if normalized in PUBLIC_STATUS_ALIASES:
        return PUBLIC_STATUS_ALIASES[normalized]
    if any(token in normalized for token in ("deny", "blocked", "permission", "policy", "approval")):
        return "blocked"
    if any(token in normalized for token in ("fail", "error", "exception", "traceback")):
        return "failed"
    if "timeout" in normalized or "timed out" in normalized:
        return "failed"
    if any(token in normalized for token in ("start", "run", "progress")):
        return "running"
    return "updated"


def _public_error_metadata(error: Any, *, ok: bool | None = None) -> dict[str, Any]:
    has_detail = _has_error_detail(error)
    failed = ok is False or has_detail
    category = _public_error_category(error) if failed else ""
    summary = PUBLIC_ERROR_SUMMARIES.get(category, "") if failed else ""
    next_step = PUBLIC_ERROR_NEXT_STEPS.get(category, "") if failed else ""
    metadata: dict[str, Any] = {
        "status": "failed" if failed else "completed" if ok is True else "unknown",
        "has_detail": has_detail,
        "category": category,
        "summary": summary,
        "next_step": next_step,
        "private_detail_redacted": has_detail,
        "redacted": True,
    }
    if isinstance(error, dict):
        metadata["field_count"] = len(error)
    elif isinstance(error, (list, tuple, set)):
        metadata["count"] = len(error)
    return metadata


def _has_error_detail(error: Any) -> bool:
    if error is None:
        return False
    if isinstance(error, str):
        return bool(error.strip())
    if isinstance(error, (list, tuple, set, dict)):
        return bool(error)
    return True


def _public_error_category(error: Any) -> str:
    text = str(error or "").casefold() if isinstance(error, str) else ""
    if any(token in text for token in ("permission", "policy", "approval", "authorized", "denied", "blocked")):
        return "blocked"
    if any(token in text for token in ("invalid", "missing", "required", "validation", "bad request")):
        return "invalid_input"
    if any(token in text for token in ("not found", "no such file", "does not exist", "missing file")):
        return "not_found"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(token in text for token in ("connection", "network", "http", "service unavailable", "dns")):
        return "network"
    return "execution_error"


@router.get("/tasks/{task_id}/replay")
def replay(task_id: str):
    try:
        task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    messages = sorted(_public_agent_messages(task_id), key=lambda item: (item.get("created_at") or "", item.get("id") or ""))
    tool_calls = sorted(
        db.fetch_many_by_fields("tool_calls", {"task_id": task_id}, limit=1000),
        key=lambda item: item.get("created_at") or "",
    )
    results_by_call = _tool_results_by_call_id([str(call.get("id") or "") for call in tool_calls])
    return {
        "task": _task_payload(task),
        "events": messages,
        "tool_calls": [_public_tool_call(call) for call in tool_calls],
        "tool_results": [_public_tool_result(results_by_call[call["id"]]) for call in tool_calls if call.get("id") in results_by_call],
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


def _public_tool_call(call: dict) -> dict:
    return {
        "id": call.get("id"),
        "task_id": call.get("task_id"),
        "step_id": call.get("step_id"),
        "tool_name": call.get("tool_name"),
        "risk_level": call.get("risk_level"),
        "status": call.get("status"),
        "dry_run": call.get("dry_run"),
        "created_at": call.get("created_at"),
        "args": _redacted_public_field(call.get("args") or {}),
        "redacted": True,
    }


def _public_tool_result(result: dict) -> dict:
    error_metadata = _public_error_metadata(result.get("error"), ok=result.get("ok"))
    return {
        "id": result.get("id"),
        "tool_call_id": result.get("tool_call_id"),
        "ok": result.get("ok"),
        "output": _redacted_public_field(result.get("output") or {}),
        "error": error_metadata["summary"] if error_metadata["status"] == "failed" else "",
        "error_metadata": error_metadata,
        "changed_paths": _public_value(result.get("changed_paths") or []),
        "rollback_info": _redacted_public_field(result.get("rollback_info") or {}),
        "observation": _redacted_public_field(result.get("observation")),
        "created_at": result.get("created_at"),
        "redacted": True,
    }


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
                    "payload": _public_value(payload),
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
    return _public_agent_messages(task_id)


@router.get("/tasks/{task_id}/safety-reviews")
def safety_reviews(task_id: str):
    return [_public_review(review) for review in db.fetch_many_by_fields("safety_reviews", {"task_id": task_id})]


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
