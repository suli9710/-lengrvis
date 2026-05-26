from __future__ import annotations

from typing import Any

from app.core import db
from app.core.schemas import Task, now_iso


SOURCE_TASKS = "tasks"
SOURCE_AGENT_MESSAGES = "agent_messages"
SOURCE_SAFETY_REVIEWS = "safety_reviews"
SOURCE_AUDIT_EVENTS = "audit_events"
SOURCE_PLANS = "plans"
SENSITIVE_KEYS = {"api_key", "password", "token", "cookie", "authorization", "secret", "credential", "credentials"}

SUBAGENT_EXCLUDED = {"User", "PlannerAgent", "SafetyReviewAgent", "OrchestratorAgent", "HumanGateAgent"}


def build_task_explain(task_id: str) -> dict[str, Any]:
    task_data = db.fetch_one("tasks", task_id)
    if not task_data:
        raise KeyError(task_id)

    task = Task.model_validate(task_data)
    messages = _chronological(db.fetch_many(SOURCE_AGENT_MESSAGES, "task_id = ?", (task_id,), limit=5000))
    reviews = _chronological(db.fetch_many(SOURCE_SAFETY_REVIEWS, "task_id = ?", (task_id,), limit=5000))
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
        "user_goal": task.user_goal,
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
        "final_result": final_result,
        "chain": chain,
    }


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
                "summary": task.user_goal,
            }
        )
    return {"text": user_message.get("content") if user_message else task.user_goal, "evidence": evidence}


def _supervisor_judgment(task: Task, messages: list[dict[str, Any]], audits: list[dict[str, Any]]) -> dict[str, Any]:
    decision_event = next(
        (event for event in audits if event.get("event_type") == "supervisor.decision" and event.get("actor") == "SupervisorAgent"),
        None,
    )
    if decision_event:
        payload = decision_event.get("payload") if isinstance(decision_event.get("payload"), dict) else {}
        delegate = bool(payload.get("delegate"))
        agent_hint = str(payload.get("agent_hint") or "")
        reply = str(payload.get("reply") or "").strip()
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
    assumptions = [str(item) for item in (plan_payload or {}).get("assumptions") or [] if str(item).strip()]
    steps = _plan_steps(plan_payload)
    message_summary = str(initial_plan_message.get("content") or "").strip() if initial_plan_message else ""
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
        "summary": summary,
        "plan_id": str((plan_payload or {}).get("id") or task.id),
        "goal": str((plan_payload or {}).get("goal") or task.user_goal),
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
                "tool_name": str(raw.get("tool_name") or ""),
                "description": str(raw.get("description") or ""),
                "status": _enum_value(raw.get("status") or ""),
                "risk_level": _enum_value(raw.get("risk_level") or ""),
                "requires_approval": bool(raw.get("requires_approval")),
                "expected_observation": str(raw.get("expected_observation") or ""),
                "rollback_strategy": str(raw.get("rollback_strategy") or ""),
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
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def _review_item(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(review.get("id") or ""),
        "step_id": review.get("step_id"),
        "target_type": str(review.get("target_type") or ""),
        "verdict": _enum_value(review.get("verdict") or ""),
        "risk_level": _enum_value(review.get("risk_level") or ""),
        "reasons": [str(reason) for reason in review.get("reasons") or []],
        "required_changes": [str(change) for change in review.get("required_changes") or []],
        "user_confirmation_message": str(review.get("user_confirmation_message") or ""),
        "safe_alternative": str(review.get("safe_alternative") or ""),
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
                "kind": str(action.get("kind") or ""),
                "tool_name": str(action.get("tool_name") or ""),
                "rationale": str(action.get("rationale") or ""),
                "follow_up_question": str(action.get("follow_up_question") or ""),
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
        "content": str(message.get("content") or ""),
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
        if event.get("event_type") in {"task.finished_or_waiting", "task.status_changed", "task.background_failed", "task.approved_step_executed"}
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
            "summary": task.final_summary or _enum_value(task.status),
        }
    )
    return {
        "status": _enum_value(task.status),
        "summary": task.final_summary or f"Task status: {_enum_value(task.status)}",
        "safety_reviews": final_reviews,
        "evidence": evidence,
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
        "summary": str(message.get("content") or ""),
    }


def _review_evidence(review: dict[str, Any]) -> dict[str, Any]:
    reasons = "; ".join(str(reason) for reason in review.get("reasons") or [])
    return {
        "source": SOURCE_SAFETY_REVIEWS,
        "id": str(review.get("id") or ""),
        "created_at": str(review.get("created_at") or ""),
        "actor": "SafetyReviewAgent",
        "step_id": review.get("step_id"),
        "summary": f"{review.get('target_type')}: {review.get('verdict')} {reasons}".strip(),
    }


def _audit_evidence(event: dict[str, Any]) -> dict[str, Any]:
    payload = _sanitize(event.get("payload") if isinstance(event.get("payload"), dict) else {})
    summary = str(payload.get("reply") or payload.get("goal") or payload.get("status") or payload.get("error") or payload)
    return {
        "source": SOURCE_AUDIT_EVENTS,
        "id": str(event.get("id") or ""),
        "created_at": str(event.get("created_at") or ""),
        "actor": str(event.get("actor") or ""),
        "event_type": str(event.get("event_type") or ""),
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


def _sanitize(value: Any, key: str = "") -> Any:
    if key.casefold() in SENSITIVE_KEYS:
        return "***"
    if isinstance(value, dict):
        return {item_key: _sanitize(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value
