"""Translate engine state changes into the public run event stream."""

from __future__ import annotations

from app.core.schemas import RunPhase
from app.orchestration.execution_models import EngineTurnResult, RunState
from app.orchestration.run_event_bus import run_event_bus


def publish_plan_events(run_id: str, state: RunState) -> None:
    if not state.current_plan:
        return
    run_event_bus.publish(
        run_id,
        "plan.generated",
        {"plan": state.current_plan, "engine": state.engine, "turn": state.turn_count},
    )
    for step in state.current_plan.get("steps") or []:
        if isinstance(step, dict):
            run_event_bus.publish(run_id, "step.selected", {"step": step, "engine": state.engine})
            if step.get("tool"):
                run_event_bus.publish(
                    run_id,
                    "tool.proposed",
                    {"tool_name": step.get("tool"), "step_id": step.get("id"), "engine": state.engine},
                )


def publish_turn_result(run_id: str, result: EngineTurnResult) -> None:
    state = result.state
    for source, payload in result.outputs.items():
        run_event_bus.publish(
            run_id,
            "tool.progress",
            {"tool_name": source, "status": "completed", "engine": state.engine, "turn": state.turn_count},
        )
        run_event_bus.publish(
            run_id,
            "tool.result",
            {"tool_name": source, "output": payload, "engine": state.engine, "turn": state.turn_count},
        )
        if isinstance(payload, dict):
            _publish_embedded_events(run_id, state, source, payload)
    run_event_bus.publish(
        run_id,
        "turn.completed",
        {
            "turn": state.turn_count,
            "engine": state.engine,
            "phase": state.phase.value,
            "message": result.message,
            "transition_reason": state.transition_reason,
        },
    )


def publish_terminal_event(run_id: str, state: RunState, result: EngineTurnResult) -> None:
    phase = RunPhase(state.phase.value)
    if phase == RunPhase.AWAITING_APPROVAL:
        event_name = "run.waiting_approval"
    elif phase == RunPhase.CANCELLED:
        event_name = "run.cancelled"
    elif phase in {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.DENIED}:
        event_name = phase.event_name
    else:
        return
    run_event_bus.publish(
        run_id,
        event_name,
        {
            "engine": state.engine,
            "phase": phase.value,
            "message": result.message,
            "transition_reason": state.transition_reason,
            "task_id": state.task_id,
        },
    )


def _publish_embedded_events(run_id: str, state: RunState, source: str, payload: dict) -> None:
    for event in [*(payload.get("lengrvis_events") or []), *(payload.get("events") or [])]:
        if not isinstance(event, dict):
            continue
        name = event.get("name") or event.get("event")
        event_payload = event.get("payload") or {
            key: value for key, value in event.items() if key not in {"name", "event"}
        }
        if isinstance(name, str) and isinstance(event_payload, dict):
            run_event_bus.publish(
                run_id,
                name,
                {**event_payload, "engine": state.engine, "turn": state.turn_count, "source": source},
            )
