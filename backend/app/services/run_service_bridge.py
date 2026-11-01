from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core import db
from app.core.schemas import Run, RunPhase
from app.orchestration.agent_bus import AgentBus
from app.orchestration.run_event_bus import run_event_bus, task_message_to_run_event
from app.services.run_service_state import (
    _PERSISTED_AGENT_MESSAGE_ERRORS,
    TASK_SYNC_EVENT_PHASES,
    _redacted_error,
)

logger = logging.getLogger(__name__)


async def _bridge_task_messages(
    run_id: str,
    task_id: str,
    queue: asyncio.Queue,
    stop_event: asyncio.Event,
    *,
    bus: AgentBus,
) -> None:
    seen_message_ids: set[str] = _seen_task_message_ids(run_id)
    try:
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run_id, message, seen_message_ids)
        while not stop_event.is_set():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            _publish_translated_message(run_id, message, seen_message_ids)
        while True:
            try:
                message = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            _publish_translated_message(run_id, message, seen_message_ids)
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run_id, message, seen_message_ids)
    finally:
        bus.unsubscribe(task_id, queue)


def _publish_translated_message(run_id: str, message: Any, seen_message_ids: set[str]) -> None:
    if message.id in seen_message_ids:
        return
    seen_message_ids.add(message.id)
    translated = task_message_to_run_event(message, run_id=run_id)
    if translated is None:
        return
    name, payload = translated
    run_event_bus.publish(run_id, name, payload)
    if name == "plan.generated":
        for step in payload.get("structured_payload", {}).get("steps") or []:
            if isinstance(step, dict):
                run_event_bus.publish(
                    run_id,
                    "step.selected",
                    {"task_id": message.task_id, "step": step},
                )


def _seen_task_message_ids(run_id: str) -> set[str]:
    seen: set[str] = set()
    for event in db.fetch_run_events(run_id, limit=5000):
        payload = event.get("payload") or {}
        message_id = payload.get("message_id")
        if message_id:
            seen.add(str(message_id))
    return seen


def _has_run_event(run_id: str, name: str) -> bool:
    return any(str(event.get("name")) == name for event in db.fetch_run_events(run_id, limit=5000))


def _publish_task_phase_event_once(run: Run, phase: RunPhase, task: Any, *, reason: str) -> None:
    if phase not in TASK_SYNC_EVENT_PHASES:
        return
    event_name = phase.event_name
    if _has_run_event(run.id, event_name):
        return
    run_event_bus.publish(
        run.id,
        event_name,
        {
            "task_id": task.id,
            "task_status": task.status.value,
            "execution_stage": task.execution_stage.value,
            "final_summary": task.final_summary,
            "phase": phase.value,
            "reason": reason,
        },
    )


def _agent_message(raw: dict[str, Any]) -> Any | None:
    try:
        from app.core.schemas import AgentMessage

        return AgentMessage.model_validate(raw)
    except _PERSISTED_AGENT_MESSAGE_ERRORS as exc:
        message_id = raw.get("id") if isinstance(raw, dict) else ""
        logger.debug(
            "invalid agent message skipped while bridging run messages (message_id=%s): %s",
            message_id,
            _redacted_error(exc),
            exc_info=True,
        )
        return None
