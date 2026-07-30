from __future__ import annotations

from typing import Any

from app.context.agent_message_projection import llm_safe_agent_message
from app.core import db
from app.core.schemas import AgentMessage, Task
from app.policy.redaction import redact_public_text, redact_value
from app.services import task_recording_service
from app.services.task_explain_service import build_task_completion_evidence, build_task_result_quality

EVIDENCE_PRIVACY_NOTE = (
    "Only status, counts, and redacted event labels are exposed; raw payloads, file contents, "
    "and hidden prompts are omitted."
)
REVIEW_EVENT_KINDS = {"post_tool_review", "safety_review"}
CAPABILITY_BOUNDARY_KINDS = {"context_boundary", "context_projection", "model_boundary_denied", "tool_contract"}
SAFE_BOUNDARY_PAYLOAD_KEYS = {
    "automatic_replay_blocked",
    "event_type",
    "kind",
    "risk_level",
    "requires_user_review",
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
PUBLIC_PAYLOAD_PROTOCOL_KEYS = {
    "event_type",
    "function",
    "name",
    "protocol",
    "schema",
    "structured_payload",
    "tool_call",
    "tool_call_id",
    "tool_calls",
    "tool_name",
    "tool_result",
    "tool_results",
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
    "outcome_unknown": "outcome_unknown",
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


def openai_agent_messages(task_id: str) -> list[dict]:
    return [
        llm_safe_agent_message(AgentMessage.model_validate(item))
        for item in db.fetch_many_by_fields("agent_messages", {"task_id": task_id})
    ]


def public_agent_messages(task_id: str) -> list[dict]:
    return [public_agent_message(item) for item in db.fetch_many_by_fields("agent_messages", {"task_id": task_id})]


def public_agent_message(item: dict) -> dict:
    model = AgentMessage.model_validate(item)
    safe = llm_safe_agent_message(model, include_legacy=True, redact_user_content=True)
    safe["content"] = public_agent_message_content(model)
    for key in ("metadata", "structured_payload", "tool_calls"):
        if key in safe:
            safe[key] = public_value(safe.get(key))
    safe["redacted"] = True
    return safe


def public_agent_message_content(message: AgentMessage) -> str:
    payload = message.structured_payload if isinstance(message.structured_payload, dict) else {}
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    event_type = str(payload.get("event_type") or metadata.get("event_type") or "").lower()
    if event_type.startswith("model_boundary") or message.from_agent.lower() == "modelboundary":
        return "Model boundary event recorded."
    if event_type == "tool.progress" or payload.get("kind") == "tool_progress":
        return tool_progress_detail(
            str(payload.get("tool_name") or metadata.get("tool_name") or ""), str(payload.get("status") or "")
        )
    if message.from_agent.lower() in {"user", "human"}:
        return "User message recorded."
    return "Agent message recorded."


def step_recordings(task_id: str) -> list[dict]:
    result: dict[str, dict] = {}
    for frame in task_recording_service.list_recording_frames(task_id):
        step_id = str(frame.get("step_id") or "")
        if not step_id:
            continue
        merge_step_recording(result, step_id, "", "", [frame])

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
        merge_step_recording(
            result,
            step_id,
            str(payload.get("tool_name") or ""),
            str(payload.get("agent") or message.get("from_agent") or ""),
            recording_frames,
        )
    return list(result.values())


def public_step_recordings(task_id: str) -> list[dict]:
    return [public_step_recording(item) for item in step_recordings(task_id)]


def public_step_recording(item: dict) -> dict:
    frames = [frame for frame in item.get("frames") or [] if isinstance(frame, dict)]
    return {
        "step_id": str(item.get("step_id") or ""),
        "tool_name": public_tool_label(str(item.get("tool_name") or "")),
        "agent": str(item.get("agent") or ""),
        "frame_count": len(frames),
        "frames": [public_recording_frame(frame) for frame in frames],
        "redacted": True,
        "privacy_note": (
            "Recording images are hidden from the timeline; open them only through an explicit local review flow."
        ),
    }


def public_recording_frame(frame: dict) -> dict:
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


def merge_step_recording(
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


def task_evidence_summary(
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

    completion_evidence = (
        completion_evidence
        if completion_evidence is not None
        else (build_task_completion_evidence(task) if task else empty_completion_evidence())
    )
    status = task_status_summary(task, completion_evidence)
    next_step = task_next_step(task, counts, completion_evidence)
    result_quality = build_task_result_quality(task, completion_evidence, next_step=next_step)
    return {
        "status": status,
        "evidence": evidence[:4],
        "next_step": result_quality["next_step"],
        "counts": counts,
        "completion_evidence": completion_evidence,
        "result_quality": result_quality,
        "privacy_note": EVIDENCE_PRIVACY_NOTE,
    }


def empty_completion_evidence() -> dict[str, Any]:
    return {
        "level": "submission",
        "result_verified": False,
        "result_artifacts": [],
        "missing": ["task record"],
        "signoff": False,
    }


def task_status_summary(task: Task | None, completion_evidence: dict[str, Any]) -> str:
    if task is None:
        return "Task record unavailable."
    status = enum_text(task.status)
    stage = enum_text(task.execution_stage)
    if stage == "awaiting_approval":
        return "Waiting for approval before the agent continues."
    if status == "completed":
        if completion_result_verified(task, completion_evidence):
            return "Completed with verified result evidence; manual sign-off is still separate."
        return "Completed, but result evidence is not verified yet."
    if status == "rolled_back":
        return "Rolled back with verified restoration evidence."
    if status == "repair_required":
        return "Rollback is incomplete; review the remaining repair actions."
    if status == "failed":
        return "Failed; review the evidence trail before retrying."
    if status == "cancelled":
        return "Cancelled before completion."
    if status == "execution":
        return "Running; evidence is being collected as steps finish."
    if status in {"planning", "plan_review", "consultation", "final_review"}:
        return "Preparing or reviewing the plan."
    return f"Task status: {status or 'unknown'}."


def task_next_step(task: Task | None, counts: dict[str, int], completion_evidence: dict[str, Any]) -> str:
    status = enum_text(task.status) if task else ""
    stage = enum_text(task.execution_stage) if task else ""
    if stage == "awaiting_approval":
        return "Review the pending approval summary before allowing live execution."
    if counts["items_needing_attention"]:
        return "Review the flagged checkpoint and decide whether to continue, revise, or stop."
    if status == "completed":
        if completion_result_verified(task, completion_evidence):
            return "Open the task explanation to inspect the verified result evidence before manual sign-off."
        missing = completion_missing_text(completion_evidence)
        return f"Open the task explanation and collect missing evidence before treating the result as done{missing}."
    if status == "rolled_back":
        return "Inspect the rollback evidence before closing the task."
    if status == "repair_required":
        return "Review the rollback details and complete the remaining repair actions."
    if status == "failed":
        return "Review the failed evidence trail, fix the cause, then retry only if the goal is still safe."
    if status == "cancelled":
        return "Start a new task or revise the goal; this run did not reach a verified result."
    if status in {"created", "planning", "plan_review", "consultation", "final_review"}:
        return "Let the task continue until reviewed execution and result evidence are recorded."
    if status == "execution":
        return "Keep monitoring until a reviewed result is recorded."
    if counts["events"] == 0:
        return "Let the run continue until the first reviewed step is recorded."
    return "Check the task explanation or local diagnostics before trusting this result."


def completion_missing_text(completion_evidence: dict[str, Any]) -> str:
    missing = [
        str(item)
        for item in build_task_result_quality(None, completion_evidence).get("missing_checks") or []
        if str(item)
    ]
    if not missing:
        return ""
    return f": {', '.join(missing[:3])}"


def completion_result_verified(task: Task | None, completion_evidence: dict[str, Any]) -> bool:
    if task is None or enum_text(task.status) != "completed":
        return False
    return (
        completion_evidence.get("level") == "completed_result"
        and completion_evidence.get("result_verified") is True
        and completion_evidence.get("signoff") is False
    )


def enum_text(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


def tool_progress_detail(tool: str, status: str) -> str:
    parts = [part for part in [public_tool_label(tool), public_status_label(status)] if part]
    return " ".join(parts) if parts else "Tool progress was recorded."


def review_event_detail(review: dict[str, Any]) -> str:
    target_type = public_review_target(str(review.get("target_type") or "review"))
    verdict = str(review.get("verdict") or "recorded")
    risk_level = str(review.get("risk_level") or "")
    suffix = f" at {risk_level}" if risk_level else ""
    return f"{target_type}: {verdict}{suffix}."


def public_review(review: dict[str, Any]) -> dict[str, Any]:
    reasons = review.get("reasons") if isinstance(review.get("reasons"), list) else []
    required_changes = review.get("required_changes") if isinstance(review.get("required_changes"), list) else []
    return {
        "id": str(review.get("id") or ""),
        "step_id": review.get("step_id"),
        "target_type": public_review_target(str(review.get("target_type") or "")),
        "verdict": str(review.get("verdict") or ""),
        "risk_level": str(review.get("risk_level") or ""),
        "summary": review_event_detail(review),
        "reasons": [],
        "reason_count": len(reasons),
        "required_change_count": len(required_changes),
        "has_user_confirmation_message": bool(review.get("user_confirmation_message")),
        "has_safe_alternative": bool(review.get("safe_alternative")),
        "safe_alternative": "",
        "created_at": str(review.get("created_at") or ""),
    }


def boundary_event(
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
        "detail": public_detail(detail),
        "severity": severity,
        "step_id": str(step_id or ""),
        "created_at": created_at,
        "payload": safe_boundary_payload(kind, payload),
    }


def public_detail(detail: str, *, limit: int = 220) -> str:
    text = redact_public_text(str(detail or "")).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def safe_boundary_payload(kind: str, payload: dict | None) -> dict[str, Any]:
    raw_payload = payload if isinstance(payload, dict) else {}
    safe: dict[str, Any] = {
        "kind": kind,
        "redacted": True,
        "field_count": len([key for key in raw_payload if not str(key).startswith("_")]),
    }
    for key in SAFE_BOUNDARY_PAYLOAD_KEYS:
        if key in raw_payload:
            if key == "status":
                safe[key] = public_status_label(str(raw_payload.get(key) or ""))
            elif key == "tool_name":
                safe[key] = public_tool_label(str(raw_payload.get(key) or ""))
            elif key == "event_type":
                safe[key] = public_event_label(str(raw_payload.get(key) or ""))
            else:
                safe[key] = safe_boundary_value(raw_payload.get(key))
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


def safe_boundary_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return public_detail(value, limit=120)
    redacted = redact_value(value)
    if isinstance(redacted, dict):
        return {"field_count": len(redacted)}
    if isinstance(redacted, list):
        return {"count": len(redacted)}
    return redacted


def public_value(value: Any, key: str = "") -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key in PUBLIC_PAYLOAD_ERROR_KEYS:
        return public_error_metadata(value)
    if normalized_key in PUBLIC_PAYLOAD_STATUS_KEYS and isinstance(value, str):
        return public_status_label(value)
    if normalized_key == "tool_name" and isinstance(value, str):
        return public_tool_label(value)
    if normalized_key in PUBLIC_PAYLOAD_PROTOCOL_KEYS:
        return redacted_public_field(value)
    if normalized_key in PUBLIC_PAYLOAD_TEXT_KEYS:
        return redacted_public_field(value)
    redacted = redact_value(value)
    if isinstance(redacted, dict):
        return {str(item_key): public_value(item, str(item_key)) for item_key, item in redacted.items()}
    if isinstance(redacted, list):
        return [public_value(item, key) for item in redacted]
    if isinstance(redacted, tuple):
        return [public_value(item, key) for item in redacted]
    if isinstance(redacted, set):
        return [public_value(item, key) for item in sorted(redacted, key=str)]
    if isinstance(redacted, str):
        return redact_public_text(redacted)
    return redacted


def redacted_public_field(value: Any) -> Any:
    if isinstance(value, dict):
        return {"redacted": True, "field_count": len(value)}
    if isinstance(value, list | tuple | set):
        return {"redacted": True, "count": len(value)}
    if isinstance(value, str):
        return "[REDACTED_TEXT]"
    if value is None:
        return None
    return "[REDACTED_VALUE]"


def public_status_label(status: str) -> str:
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


def public_tool_label(tool_name: str) -> str:
    normalized = str(tool_name or "").strip().casefold()
    if not normalized:
        return ""
    if normalized.startswith("file."):
        return "File capability"
    if normalized.startswith("system."):
        return "System capability"
    if normalized.startswith("browser."):
        return "Browser capability"
    if normalized.startswith("app.") or normalized.startswith("ui."):
        return "App capability"
    if normalized.startswith("document.") or normalized.startswith("excel."):
        return "Document capability"
    if normalized.startswith("search."):
        return "Search capability"
    if normalized.startswith("remote."):
        return "Remote session capability"
    return "Tool capability"


def public_review_target(target_type: str) -> str:
    normalized = str(target_type or "").strip().replace("-", "_").casefold()
    if normalized == "tool_result":
        return "result"
    if normalized == "tool_call":
        return "action"
    if normalized == "final":
        return "final result"
    if normalized == "plan":
        return "plan"
    if normalized == "goal":
        return "goal"
    if normalized.startswith("agent_message"):
        return "agent message"
    return "review"


def public_event_label(event_type: str) -> str:
    normalized = str(event_type or "").strip().replace("-", "_").casefold()
    if normalized.startswith("tool."):
        return "tool_update"
    if normalized.startswith("task."):
        return "task_lifecycle"
    if normalized.startswith("context."):
        return "context_boundary"
    if normalized.startswith("model_boundary"):
        return "model_boundary"
    if normalized.startswith("plan."):
        return "plan_review"
    return "event"


def public_error_metadata(error: Any, *, ok: bool | None = None) -> dict[str, Any]:
    has_detail = has_error_detail(error)
    failed = ok is False or has_detail
    category = public_error_category(error) if failed else ""
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
    elif isinstance(error, list | tuple | set):
        metadata["count"] = len(error)
    return metadata


def has_error_detail(error: Any) -> bool:
    if error is None:
        return False
    if isinstance(error, str):
        return bool(error.strip())
    if isinstance(error, list | tuple | set | dict):
        return bool(error)
    return True


def public_error_category(error: Any) -> str:
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


def public_tool_call(call: dict) -> dict:
    outcome_unknown = str(call.get("status") or "") == "outcome_unknown"
    return {
        "id": call.get("id"),
        "task_id": call.get("task_id"),
        "step_id": call.get("step_id"),
        "tool_name": public_tool_label(str(call.get("tool_name") or "")),
        "risk_level": call.get("risk_level"),
        "status": public_status_label(str(call.get("status") or "")),
        "outcome_unknown": outcome_unknown,
        "automatic_replay_blocked": outcome_unknown,
        "requires_user_review": outcome_unknown,
        "dry_run": call.get("dry_run"),
        "created_at": call.get("created_at"),
        "args": redacted_public_field(call.get("args") or {}),
        "redacted": True,
    }


def public_tool_result(result: dict) -> dict:
    error_metadata = public_error_metadata(result.get("error"), ok=result.get("ok"))
    return {
        "id": result.get("id"),
        "has_tool_call": bool(result.get("tool_call_id")),
        "ok": result.get("ok"),
        "output": redacted_public_field(result.get("output") or {}),
        "error": error_metadata["summary"] if error_metadata["status"] == "failed" else "",
        "error_metadata": error_metadata,
        "changed_paths": redacted_public_field(result.get("changed_paths") or []),
        "rollback_info": redacted_public_field(result.get("rollback_info") or {}),
        "observation": redacted_public_field(result.get("observation")),
        "created_at": result.get("created_at"),
        "redacted": True,
    }
