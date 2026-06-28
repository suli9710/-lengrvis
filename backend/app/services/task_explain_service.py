from __future__ import annotations

from typing import Any

from app.core import db
from app.core.schemas import Task, now_iso
from app.policy.redaction import contains_sensitive_key, redact_public_text
from app.services import task_recording_service


SOURCE_TASKS = "tasks"
SOURCE_AGENT_MESSAGES = "agent_messages"
SOURCE_SAFETY_REVIEWS = "safety_reviews"
SOURCE_AUDIT_EVENTS = "audit_events"
SOURCE_PLANS = "plans"
SENSITIVE_KEYS = {"api_key", "password", "token", "cookie", "authorization", "secret", "credential", "credentials"}
PUBLIC_REDACTED_KEYS = {
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
    "request",
    "response",
    "structured_payload",
    "system_prompt",
    "text",
    "tool_call",
    "tool_calls",
    "tool_result",
    "tool_results",
    "url",
    "uri",
    "value",
}

SUBAGENT_EXCLUDED = {"User", "PlannerAgent", "SafetyReviewAgent", "OrchestratorAgent", "HumanGateAgent"}
TERMINAL_AUDIT_TYPES = {
    "task.finished_or_waiting",
    "task.status_changed",
    "task.background_failed",
    "task.approved_step_executed",
}
COMPLETED_STATUSES = {"completed", "complete", "success", "succeeded", "done"}
SAFE_FAILURE_STATUSES = {"failed", "failure", "cancelled", "canceled", "denied", "blocked"}
ALLOW_REVIEW_VERDICTS = {"allow", "allowed", "approved", "pass", "passed"}
BLOCKING_REVIEW_VERDICTS = {"deny", "denied", "blocked", "needs_user_approval"}
RESULT_REVIEW_TARGETS = {"final", "tool_result"}
TOOL_RESULT_REVIEW_TARGET = "tool_result"
FINAL_REVIEW_TARGET = "final"
NON_RESULT_SUMMARY_MARKERS = {
    "submitted",
    "submission",
    "routed",
    "queued",
    "waiting",
    "pending",
    "in progress",
}
RESULT_QUALITY_LABELS = {
    "verified_result": "Verified result",
    "visible_progress": "Progress awaiting verification",
    "safe_failure": "Safe failure",
    "task_evidence_only": "Task evidence only",
}
RESULT_QUALITY_SUMMARIES = {
    "verified_result": "A result is recorded and verified by result reviews. Manual sign-off is still separate.",
    "visible_progress": "The task shows progress, but the result still needs verification.",
    "safe_failure": "The run failed or was blocked safely. Review the public evidence before retrying.",
    "task_evidence_only": "Only task-level evidence is available. No verified result has been recorded yet.",
}
PUBLIC_MISSING_CHECK_LABELS = {
    "completed task status": "completed task status",
    "completed result evidence": "verified result evidence",
    "successful result artifact": "successful result record",
    "all tool results succeeded": "all recorded actions succeeded",
    "final result summary": "final result summary",
    "post tool result verification": "action result review",
    "final result verification": "final result review",
    "unblocked result review": "blocking review cleared",
    "task record": "task record",
}


def build_task_explain(task_id: str, *, audits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    task_data = db.fetch_one("tasks", task_id)
    if not task_data:
        raise KeyError(task_id)

    task = Task.model_validate(task_data)
    messages = _chronological(db.fetch_many(SOURCE_AGENT_MESSAGES, "task_id = ?", (task_id,), limit=5000))
    reviews = _chronological(db.fetch_many(SOURCE_SAFETY_REVIEWS, "task_id = ?", (task_id,), limit=5000))
    if audits is None:
        audits = _chronological(db.fetch_many(SOURCE_AUDIT_EVENTS, "task_id = ?", (task_id,), limit=5000))
    plan_payload, plan_source = _latest_plan_payload(task_id, messages)
    initial_plan_message = _initial_plan_message(messages)

    user_goal = _user_goal(task, messages, audits)
    supervisor_judgment = _supervisor_judgment(task, messages, audits)
    planner_reasoning = _planner_reasoning(task, plan_payload, initial_plan_message, plan_source)
    global_safety_reviews = [_review_item(review) for review in reviews if not review.get("step_id")]
    subagent_suggestions = _subagent_suggestions(messages)
    steps = _step_explanations(plan_payload, messages, reviews)
    final_result = _final_result(task, reviews, audits)
    completion_evidence = build_task_completion_evidence(task, messages=messages, reviews=reviews, audits=audits)
    next_step = _completion_next_step(task, completion_evidence)
    result_quality = build_task_result_quality(task, completion_evidence, next_step=next_step)
    final_result["next_step"] = next_step

    missing_sections = _missing_sections(
        user_goal=user_goal,
        supervisor_judgment=supervisor_judgment,
        planner_reasoning=planner_reasoning,
        reviews=reviews,
        subagent_suggestions=subagent_suggestions,
        final_result=final_result,
    )

    chain = [
        {
            "stage": "user_goal",
            "title": "User goal",
            "summary": user_goal["text"],
            "evidence": user_goal["evidence"],
        },
        {
            "stage": "supervisor_judgment",
            "title": "Supervisor judgment",
            "summary": supervisor_judgment["summary"],
            "evidence": supervisor_judgment["evidence"],
        },
        {
            "stage": "planner_reasoning",
            "title": "Planner reasoning",
            "summary": planner_reasoning["summary"],
            "evidence": planner_reasoning["evidence"],
        },
        {
            "stage": "step_safety_reviews",
            "title": "Per-step safety review",
            "summary": _step_safety_summary(steps, global_safety_reviews),
            "evidence": [_review_evidence(review) for review in reviews],
        },
        {
            "stage": "subagent_suggestions",
            "title": "Subagent suggestions",
            "summary": _subagent_summary(subagent_suggestions),
            "evidence": [_message_evidence(message) for message in subagent_suggestions],
        },
        {
            "stage": "final_result",
            "title": "Final result",
            "summary": final_result["summary"],
            "evidence": final_result["evidence"],
        },
    ]

    return {
        "task_id": task.id,
        "user_goal": _public_text(task.user_goal),
        "status": _enum_value(task.status),
        "mode": task.mode,
        "generated_at": now_iso(),
        "complete": not missing_sections,
        "missing_sections": missing_sections,
        "data_sources": {
            SOURCE_AGENT_MESSAGES: len(messages),
            SOURCE_SAFETY_REVIEWS: len(reviews),
            SOURCE_AUDIT_EVENTS: len(audits),
        },
        "user_goal_record": user_goal,
        "supervisor_judgment": supervisor_judgment,
        "planner_reasoning": planner_reasoning,
        "global_safety_reviews": global_safety_reviews,
        "steps": steps,
        "subagent_suggestions": subagent_suggestions,
        "completion_evidence": completion_evidence,
        "result_quality": result_quality,
        "next_step": next_step,
        "final_result": final_result,
        "chain": chain,
    }


def build_task_completion_evidence(
    task: Task,
    *,
    messages: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    audits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize public completion evidence without exposing raw task data."""

    task_id = task.id
    messages = messages if messages is not None else _chronological(db.fetch_many(SOURCE_AGENT_MESSAGES, "task_id = ?", (task_id,), limit=5000))
    reviews = reviews if reviews is not None else _chronological(db.fetch_many(SOURCE_SAFETY_REVIEWS, "task_id = ?", (task_id,), limit=5000))
    audits = audits if audits is not None else _chronological(db.fetch_many(SOURCE_AUDIT_EVENTS, "task_id = ?", (task_id,), limit=5000))
    tool_calls = db.fetch_many_by_fields("tool_calls", {"task_id": task_id}, limit=5000)
    tool_call_by_id = {str(item.get("id") or ""): item for item in tool_calls if item.get("id")}
    tool_call_ids = [str(item.get("id") or "") for item in tool_calls if item.get("id")]
    tool_results = db.fetch_many_in("tool_results", "tool_call_id", tool_call_ids, limit=5000) if tool_call_ids else []
    successful_results = [item for item in tool_results if bool(item.get("ok"))]
    failed_results = [item for item in tool_results if not bool(item.get("ok"))]
    result_reviews = [review for review in reviews if str(review.get("target_type") or "") in RESULT_REVIEW_TARGETS]
    final_reviews = [review for review in result_reviews if str(review.get("target_type") or "") == FINAL_REVIEW_TARGET]
    allowing_final_reviews = [review for review in final_reviews if _review_verdict(review) in ALLOW_REVIEW_VERDICTS]
    allowing_tool_result_reviews = [
        review
        for review in result_reviews
        if str(review.get("target_type") or "") == TOOL_RESULT_REVIEW_TARGET
        and _review_verdict(review) in ALLOW_REVIEW_VERDICTS
    ]
    reviewed_tool_result_steps = {
        str(review.get("step_id") or "")
        for review in allowing_tool_result_reviews
        if str(review.get("step_id") or "").strip()
    }
    blocking_reviews = [review for review in result_reviews if _review_verdict(review) in BLOCKING_REVIEW_VERDICTS]
    terminal_audits = [event for event in audits if event.get("event_type") in TERMINAL_AUDIT_TYPES]
    progress_messages = [
        message
        for message in messages
        if _payload(message).get("kind") == "tool_progress"
        or str((_payload(message).get("event_type") or (message.get("metadata") or {}).get("event_type") or "")).lower() == "tool.progress"
        or bool(message.get("tool_call_id"))
    ]

    status = _enum_value(task.status).replace("-", "_").casefold()
    has_final_summary = bool(str(task.final_summary or "").strip())
    has_result_summary = _has_result_summary(task.final_summary)
    successful_result_count = len(successful_results)
    successful_result_steps = [
        str((tool_call_by_id.get(str(item.get("tool_call_id") or "")) or {}).get("step_id") or "")
        for item in successful_results
    ]
    successful_result_steps_are_bindable = (
        successful_result_count > 0
        and all(step_id.strip() for step_id in successful_result_steps)
        and len(set(successful_result_steps)) == successful_result_count
    )
    has_reviewed_successful_results = (
        successful_result_count > 0
        and successful_result_steps_are_bindable
        and all(step_id in reviewed_tool_result_steps for step_id in successful_result_steps)
        and not failed_results
    )
    has_verified_result_evidence = has_result_summary and has_reviewed_successful_results and bool(allowing_final_reviews)
    safe_failure = status in SAFE_FAILURE_STATUSES or bool(blocking_reviews) or any(
        str(event.get("event_type") or "") == "task.background_failed" for event in terminal_audits
    )
    result_verified = status in COMPLETED_STATUSES and has_verified_result_evidence and not safe_failure
    if result_verified:
        level = "completed_result"
    elif safe_failure:
        level = "safe_failure"
    elif tool_calls or tool_results or progress_messages or result_reviews or terminal_audits or has_final_summary:
        level = "visible_progress"
    elif task.id or any(event.get("event_type") == "task.created" for event in audits):
        level = "task_created"
    else:
        level = "submission"

    result_artifacts: list[dict[str, Any]] = []
    if successful_results:
        result_artifacts.append(_completion_evidence_item("tool_result", "Successful tool result", len(successful_results)))
    if failed_results:
        result_artifacts.append(_completion_evidence_item("failed_tool_result", "Failed tool result", len(failed_results)))
    if has_final_summary:
        result_artifacts.append(_completion_evidence_item("final_summary", "Final task summary", 1))
    if allowing_tool_result_reviews:
        result_artifacts.append(
            _completion_evidence_item("post_tool_review", "Post-tool verification review", len(allowing_tool_result_reviews))
        )
    if final_reviews:
        result_artifacts.append(_completion_evidence_item("final_review", "Final safety review", len(final_reviews)))
    if safe_failure:
        result_artifacts.append(_completion_evidence_item("safe_failure", "Blocking result review", len(blocking_reviews) or 1))
    if not result_artifacts and terminal_audits:
        result_artifacts.append(_completion_evidence_item("terminal_event", "Terminal task event", len(terminal_audits)))
    if not result_artifacts and progress_messages:
        result_artifacts.append(_completion_evidence_item("tool_progress", "Tool progress event", len(progress_messages)))
    if not result_artifacts and tool_calls:
        result_artifacts.append(_completion_evidence_item("tool_call", "Tool call", len(tool_calls)))
    recording_frame_count = len(task_recording_service.list_recording_frames(task_id))
    if not result_artifacts and recording_frame_count:
        result_artifacts.append(_completion_evidence_item("recording_frame", "Recording frame", recording_frame_count))

    missing: list[str] = []
    if status not in COMPLETED_STATUSES:
        missing.append("completed task status")
    if not has_verified_result_evidence:
        missing.append("completed result evidence")
    if successful_result_count == 0:
        missing.append("successful result artifact")
    if failed_results:
        missing.append("all tool results succeeded")
    if not has_result_summary:
        missing.append("final result summary")
    if successful_result_count > 0 and not has_reviewed_successful_results:
        missing.append("post-tool result verification")
    if not allowing_final_reviews:
        missing.append("final result verification")
    if blocking_reviews:
        missing.append("unblocked result review")

    return {
        "level": level,
        "result_verified": result_verified,
        "result_artifacts": result_artifacts,
        "missing": [] if result_verified else missing,
        "signoff": False,
    }


def build_task_result_quality(
    task: Task | None,
    completion_evidence: dict[str, Any],
    *,
    next_step: str | None = None,
) -> dict[str, Any]:
    """Beginner-safe public result quality contract derived from completion evidence."""

    state = _result_quality_state(completion_evidence)
    result_verified = state == "verified_result"
    missing_checks = [] if result_verified else _public_missing_checks(completion_evidence)
    public_next_step = _public_text(next_step) if next_step else ""
    if not public_next_step:
        public_next_step = _result_quality_next_step(task, state, missing_checks)
    return {
        "state": state,
        "label": RESULT_QUALITY_LABELS[state],
        "summary": RESULT_QUALITY_SUMMARIES[state],
        "result_verified": result_verified,
        "can_treat_as_done": result_verified,
        "needs_review": not result_verified,
        "missing_checks": missing_checks,
        "next_step": public_next_step,
        "signoff": False,
        "redacted": True,
        "privacy_note": (
            "Private action details, file names, raw outputs, prompts, hosts, "
            "and secret-bearing URLs are hidden."
        ),
    }


def _review_verdict(review: dict[str, Any]) -> str:
    return _enum_value(review.get("verdict") or "").replace("-", "_").casefold()


def _completion_next_step(task: Task, completion_evidence: dict[str, Any]) -> str:
    state = _result_quality_state(completion_evidence)
    missing_checks = _public_missing_checks(completion_evidence)
    return _result_quality_next_step(task, state, missing_checks)


def _result_quality_state(completion_evidence: dict[str, Any]) -> str:
    level = str(completion_evidence.get("level") or "").replace("-", "_").casefold()
    if (
        level == "completed_result"
        and completion_evidence.get("result_verified") is True
        and completion_evidence.get("signoff") is False
    ):
        return "verified_result"
    if level == "safe_failure":
        return "safe_failure"
    if level in {"visible_progress", "completed_result"}:
        return "visible_progress"
    return "task_evidence_only"


def _public_missing_checks(completion_evidence: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in completion_evidence.get("missing") or []:
        key = _missing_check_key(item)
        label = PUBLIC_MISSING_CHECK_LABELS.get(key, "additional result review")
        label = _public_text(label)
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels


def _missing_check_key(value: Any) -> str:
    return " ".join(str(value or "").replace("-", " ").replace("_", " ").casefold().split())


def _result_quality_next_step(task: Task | None, state: str, missing_checks: list[str]) -> str:
    status = _enum_value(task.status).replace("-", "_").casefold() if task else ""
    if state == "verified_result":
        return "Review the verified result evidence before any separate manual sign-off."
    if state == "safe_failure":
        return "Review the failed or blocked evidence, fix the cause, then retry only if the goal is still safe."
    if state == "visible_progress":
        if missing_checks:
            return f"Collect these public checks before treating the result as done: {', '.join(missing_checks[:3])}."
        return "Open the task explanation and review the progress before treating the result as done."
    if status in COMPLETED_STATUSES:
        return "Collect verified result evidence before treating this completed task as done."
    if status in {"created", "planning", "plan_review", "consultation", "final_review"}:
        return "Let the task continue until reviewed execution and result evidence are recorded."
    if status in {"execution", "paused"}:
        return "Resume or monitor the task until a reviewed result is recorded."
    return "Check the task explanation before trusting this result."


def _has_result_summary(summary: Any) -> bool:
    text = str(summary or "").strip()
    if not text:
        return False
    normalized = text.casefold()
    if any(marker in normalized for marker in NON_RESULT_SUMMARY_MARKERS):
        return False
    return True


def _chronological(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (str(row.get("created_at") or row.get("updated_at") or ""), str(row.get("id") or "")))


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value or "")


def _payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    direct = row.get("structured_payload")
    if isinstance(direct, dict):
        return direct
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("structured_payload"), dict):
        return metadata["structured_payload"]
    return {}


def _latest_plan_payload(task_id: str, messages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    plan_messages = [message for message in messages if _is_plan_payload(_payload(message))]
    if plan_messages:
        latest = plan_messages[-1]
        return _payload(latest), _message_evidence(latest)

    plans = _chronological(db.fetch_many(SOURCE_PLANS, "task_id = ?", (task_id,), limit=1000))
    if not plans:
        return None, None
    latest_plan = plans[-1]
    return latest_plan, _plan_evidence(latest_plan)


def _initial_plan_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        if _agent_name(message) == "PlannerAgent" and _is_plan_payload(_payload(message)):
            return message
    return None


def _is_plan_payload(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("steps"), list) and bool(payload.get("goal") or payload.get("task_id"))


def _user_goal(task: Task, messages: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    user_message = next((message for message in messages if _agent_name(message).lower() in {"user", "human"}), None)
    task_created = next((event for event in audits if event.get("event_type") == "task.created"), None)
    evidence = []
    if user_message:
        evidence.append(_message_evidence(user_message))
    if task_created:
        evidence.append(_audit_evidence(task_created))
    if not evidence:
        evidence.append(
            {
                "source": SOURCE_TASKS,
                "id": task.id,
                "created_at": task.created_at,
                "actor": "Task",
                "summary": _public_text(task.user_goal),
            }
        )
    return {"text": _public_text(user_message.get("content") if user_message else task.user_goal), "evidence": evidence}


def _supervisor_judgment(task: Task, messages: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    decision_event = next(
        (event for event in audits if event.get("event_type") == "supervisor.decision" and event.get("actor") == "SupervisorAgent"),
        None,
    )
    if decision_event:
        payload = decision_event.get("payload") if isinstance(decision_event.get("payload"), dict) else {}
        delegate = bool(payload.get("delegate"))
        agent_hint = str(payload.get("agent_hint") or "")
        reply = _public_text(payload.get("reply") or "").strip()
        summary = reply or (
            f"Supervisor delegated the task to {agent_hint}."
            if delegate
            else "Supervisor kept the request in chat."
        )
        return {
            "summary": summary,
            "delegate": delegate,
            "agent_hint": agent_hint,
            "inferred": False,
            "evidence": [_audit_evidence(decision_event)],
        }

    task_created = next((event for event in audits if event.get("event_type") == "task.created"), None)
    user_message = next((message for message in messages if _agent_name(message).lower() in {"user", "human"}), None)
    evidence = []
    if task_created:
        evidence.append(_audit_evidence(task_created))
    if user_message:
        evidence.append(_message_evidence(user_message))
    return {
        "summary": "Task was accepted into orchestration; no task-scoped SupervisorAgent decision audit was recorded, so delegation is inferred from task creation.",
        "delegate": True,
        "agent_hint": "OrchestratorAgent",
        "inferred": True,
        "evidence": evidence,
    }


def _planner_reasoning(
    task: Task,
    plan_payload: dict[str, Any] | None,
    initial_plan_message: dict[str, Any] | None,
    plan_source: dict[str, Any] | None,
) -> dict[str, Any]:
    assumptions = [_public_text(item) for item in (plan_payload or {}).get("assumptions") or [] if str(item).strip()]
    steps = _plan_steps(plan_payload)
    message_summary = _public_text(initial_plan_message.get("content") or "").strip() if initial_plan_message else ""
    assumption_summary = " ".join(assumptions)
    step_summary = "; ".join(
        f"{step['order']}. {step['agent_name']} uses {step['tool_name']}: {step['description']}"
        for step in steps
    )
    summary_parts = [part for part in [message_summary, assumption_summary, step_summary] if part]
    summary = " ".join(summary_parts) or "No planner output was found for this task."
    evidence = []
    if initial_plan_message:
        evidence.append(_message_evidence(initial_plan_message))
    elif plan_source:
        evidence.append(plan_source)
    return {
        "summary": _public_text(summary),
        "plan_id": str((plan_payload or {}).get("id") or task.id),
        "goal": _public_text((plan_payload or {}).get("goal") or task.user_goal),
        "assumptions": assumptions,
        "step_count": len(steps),
        "global_risk_level": str((plan_payload or {}).get("global_risk_level") or ""),
        "requires_user_approval": bool((plan_payload or {}).get("requires_user_approval")),
        "evidence": evidence,
    }


def _plan_steps(plan_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw_steps = (plan_payload or {}).get("steps")
    if not isinstance(raw_steps, list):
        return []
    steps: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            continue
        step_id = str(raw.get("id") or f"step_{index}")
        steps.append(
            {
                "id": step_id,
                "step_id": step_id,
                "order": int(raw.get("order") or index),
                "agent_name": str(raw.get("agent_name") or ""),
                "tool_name": _public_tool_label(str(raw.get("tool_name") or "")),
                "description": _public_text(raw.get("description") or ""),
                "status": _enum_value(raw.get("status") or ""),
                "risk_level": _enum_value(raw.get("risk_level") or ""),
                "requires_approval": bool(raw.get("requires_approval")),
                "expected_observation": _public_text(raw.get("expected_observation") or ""),
                "rollback_strategy": _public_text(raw.get("rollback_strategy") or ""),
            }
        )
    return sorted(steps, key=lambda step: (step["order"], step["id"]))


def _step_explanations(
    plan_payload: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    steps = _plan_steps(plan_payload)
    if not steps:
        step_ids = sorted({str(item.get("step_id")) for item in [*messages, *reviews] if item.get("step_id")})
        steps = [
            {
                "id": step_id,
                "step_id": step_id,
                "order": index,
                "agent_name": "",
                "tool_name": "",
                "description": "",
                "status": "",
                "risk_level": "",
                "requires_approval": False,
                "expected_observation": "",
                "rollback_strategy": "",
            }
            for index, step_id in enumerate(step_ids, start=1)
        ]

    suggestions = _subagent_suggestions(messages)
    result = []
    for step in steps:
        step_id = step["step_id"]
        step_reviews = [_review_item(review) for review in reviews if str(review.get("step_id") or "") == step_id]
        step_suggestions = [message for message in suggestions if str(message.get("step_id") or "") == step_id]
        observations = [_message_item(message) for message in messages if _is_step_observation(message, step_id)]
        planner_reason = _planner_reason_for_step(step)
        result.append(
            {
                **step,
                "planner_reason": planner_reason,
                "safety_reviews": step_reviews,
                "subagent_suggestions": step_suggestions,
                "observations": observations,
            }
        )
    return result


def _planner_reason_for_step(step: dict[str, Any]) -> str:
    parts = [step.get("description"), step.get("expected_observation"), step.get("rollback_strategy")]
    return _public_text(" ".join(str(part).strip() for part in parts if str(part or "").strip()))


def _review_item(review: dict[str, Any]) -> dict[str, Any]:
    reasons = list(review.get("reasons") or [])
    required_changes = list(review.get("required_changes") or [])
    return {
        "id": str(review.get("id") or ""),
        "step_id": review.get("step_id"),
        "target_type": _public_review_target(str(review.get("target_type") or "")),
        "verdict": _enum_value(review.get("verdict") or ""),
        "risk_level": _enum_value(review.get("risk_level") or ""),
        "reasons": ["Review reason redacted."] * len(reasons),
        "reason_count": len(reasons),
        "required_changes": ["Required change redacted."] * len(required_changes),
        "required_change_count": len(required_changes),
        "user_confirmation_message": _public_text(review.get("user_confirmation_message") or ""),
        "safe_alternative": _public_text(review.get("safe_alternative") or ""),
        "created_at": str(review.get("created_at") or ""),
        "evidence": [_review_evidence(review)],
    }


def _subagent_suggestions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        payload = _payload(message)
        has_action = isinstance(payload.get("subagent_action"), dict)
        from_agent = _agent_name(message)
        message_type = str(message.get("message_type") or (message.get("metadata") or {}).get("message_type") or "")
        if not has_action and (from_agent in SUBAGENT_EXCLUDED or message_type not in {"proposal", "revision", "critique"}):
            continue
        if from_agent == "SafetyReviewAgent":
            continue
        item = _message_item(message)
        action = payload.get("subagent_action") if isinstance(payload.get("subagent_action"), dict) else {}
        if action:
            item["action"] = {
                "kind": _public_action_kind(str(action.get("kind") or "")),
                "tool_name": _public_tool_label(str(action.get("tool_name") or "")),
                "rationale": _public_text(action.get("rationale") or ""),
                "follow_up_question": _public_text(action.get("follow_up_question") or ""),
                "raw_details_redacted": True,
            }
        result.append(item)
    return result


def _message_item(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(message.get("id") or ""),
        "step_id": message.get("step_id"),
        "from_agent": _agent_name(message),
        "to_agent": message.get("to_agent") or (message.get("metadata") or {}).get("to_agent"),
        "message_type": str(message.get("message_type") or (message.get("metadata") or {}).get("message_type") or ""),
        "content": _message_public_content(message),
        "created_at": str(message.get("created_at") or ""),
        "evidence": [_message_evidence(message)],
    }


def _is_step_observation(message: dict[str, Any], step_id: str) -> bool:
    if str(message.get("step_id") or "") != step_id:
        return False
    if _agent_name(message) == "SafetyReviewAgent":
        return False
    if _payload(message).get("kind") == "step_screenshot":
        return False
    message_type = str(message.get("message_type") or (message.get("metadata") or {}).get("message_type") or "")
    return message_type in {"observation", "final"} or bool(message.get("tool_call_id"))


def _final_result(task: Task, reviews: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    final_reviews = [_review_item(review) for review in reviews if str(review.get("target_type") or "") == "final"]
    terminal_audits = [
        event
        for event in audits
        if event.get("event_type") in TERMINAL_AUDIT_TYPES
    ]
    evidence = []
    if final_reviews:
        evidence.extend(_review_evidence(review) for review in reviews if str(review.get("target_type") or "") == "final")
    if terminal_audits:
        evidence.append(_audit_evidence(terminal_audits[-1]))
    evidence.append(
        {
            "source": SOURCE_TASKS,
            "id": task.id,
            "created_at": task.updated_at,
            "actor": "Task",
            "summary": _public_text(task.final_summary or _enum_value(task.status)),
        }
    )
    return {
        "status": _enum_value(task.status),
        "summary": _public_text(task.final_summary or f"Task status: {_enum_value(task.status)}"),
        "safety_reviews": final_reviews,
        "evidence": evidence,
    }


def _completion_evidence_item(kind: str, label: str, count: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "count": int(count),
        "redacted": True,
    }


def _missing_sections(
    *,
    user_goal: dict[str, Any],
    supervisor_judgment: dict[str, Any],
    planner_reasoning: dict[str, Any],
    reviews: list[dict[str, Any]],
    subagent_suggestions: list[dict[str, Any]],
    final_result: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    if not user_goal.get("text"):
        missing.append("user_goal")
    if not supervisor_judgment.get("summary"):
        missing.append("supervisor_judgment")
    if not planner_reasoning.get("step_count"):
        missing.append("planner_reasoning")
    if not reviews:
        missing.append("safety_reviews")
    if not subagent_suggestions:
        missing.append("subagent_suggestions")
    if not final_result.get("summary"):
        missing.append("final_result")
    return missing


def _step_safety_summary(steps: list[dict[str, Any]], global_reviews: list[dict[str, Any]]) -> str:
    step_review_count = sum(len(step.get("safety_reviews") or []) for step in steps)
    return f"{step_review_count} step-scoped safety review(s), {len(global_reviews)} global safety review(s)."


def _subagent_summary(subagent_suggestions: list[dict[str, Any]]) -> str:
    if not subagent_suggestions:
        return "No subagent suggestion messages were recorded."
    return f"{len(subagent_suggestions)} subagent suggestion/message(s) recorded."


def _agent_name(message: dict[str, Any]) -> str:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    return str(message.get("from_agent") or metadata.get("from_agent") or message.get("name") or "")


def _message_evidence(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE_AGENT_MESSAGES,
        "id": str(message.get("id") or ""),
        "created_at": str(message.get("created_at") or ""),
        "actor": _agent_name(message),
        "step_id": message.get("step_id"),
        "summary": _message_public_content(message),
    }


def _review_evidence(review: dict[str, Any]) -> dict[str, Any]:
    reason_count = len(review.get("reasons") or [])
    suffix = f"; {reason_count} reason(s) redacted" if reason_count else ""
    target = _public_review_target(str(review.get("target_type") or ""))
    return {
        "source": SOURCE_SAFETY_REVIEWS,
        "id": str(review.get("id") or ""),
        "created_at": str(review.get("created_at") or ""),
        "actor": "SafetyReviewAgent",
        "step_id": review.get("step_id"),
        "summary": _public_text(f"{target}: {review.get('verdict')}{suffix}".strip()),
    }


def _audit_evidence(event: dict[str, Any]) -> dict[str, Any]:
    payload = _sanitize(event.get("payload") if isinstance(event.get("payload"), dict) else {})
    summary_source = payload.get("reply") or payload.get("goal") or payload.get("status") or payload.get("error")
    summary = _public_text(summary_source) if summary_source else _public_audit_summary(str(event.get("event_type") or ""))
    return {
        "source": SOURCE_AUDIT_EVENTS,
        "id": str(event.get("id") or ""),
        "created_at": str(event.get("created_at") or ""),
        "actor": str(event.get("actor") or ""),
        "event_type": _public_audit_event_type(str(event.get("event_type") or "")),
        "summary": summary,
    }


def _plan_evidence(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": SOURCE_PLANS,
        "id": str(plan.get("id") or ""),
        "created_at": str(plan.get("created_at") or ""),
        "actor": "PlannerAgent",
        "summary": f"Persisted plan with {len(plan.get('steps') or [])} step(s).",
    }


def _public_text(value: Any) -> str:
    return redact_public_text(str(value or ""))


def _message_public_content(message: dict[str, Any]) -> str:
    payload = _payload(message)
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    event_type = str(payload.get("event_type") or metadata.get("event_type") or "").lower()
    if event_type.startswith("model_boundary") or _agent_name(message).lower() == "modelboundary":
        return "Model boundary event recorded."
    if event_type == "tool.progress" or payload.get("kind") == "tool_progress":
        return "Tool progress was recorded."
    if _agent_name(message).lower() in {"user", "human"}:
        return "User message recorded."
    return "Agent message recorded."


def _sanitize(value: Any, key: str = "") -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key in SENSITIVE_KEYS or contains_sensitive_key(key):
        return "***"
    if normalized_key in PUBLIC_REDACTED_KEYS:
        return _redacted_public_field(value)
    if isinstance(value, dict):
        return {item_key: _sanitize(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _public_text(value)
    return value


def _public_tool_label(tool_name: str) -> str:
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


def _public_action_kind(kind: str) -> str:
    normalized = str(kind or "").strip().replace("-", "_").casefold()
    if "proposal" in normalized or "propose" in normalized:
        return "tool proposal"
    if "revision" in normalized:
        return "revision"
    if "critique" in normalized:
        return "critique"
    if "final" in normalized:
        return "final update"
    return "agent update"


def _public_review_target(target_type: str) -> str:
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


def _public_audit_event_type(event_type: str) -> str:
    normalized = str(event_type or "").strip().replace("-", "_").casefold()
    if normalized.startswith("task."):
        return "task_lifecycle"
    if normalized.startswith("supervisor."):
        return "supervisor_decision"
    if normalized.startswith("context."):
        return "context_boundary"
    if normalized.startswith("model_boundary"):
        return "model_boundary"
    return "audit_event"


def _public_audit_summary(event_type: str) -> str:
    public_type = _public_audit_event_type(event_type)
    if public_type == "task_lifecycle":
        return "Task lifecycle event recorded."
    if public_type == "supervisor_decision":
        return "Supervisor routing decision recorded."
    if public_type == "context_boundary":
        return "Context boundary event recorded."
    if public_type == "model_boundary":
        return "Model boundary event recorded."
    return "Audit event recorded."


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
