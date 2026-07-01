from __future__ import annotations

import asyncio
import threading
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import AppSettings
from app.context.agent_message_projection import llm_safe_agent_message
from app.core import db
from app.core.schemas import AgentMessage, MessageType, RunEvent, now_iso
from app.policy.redaction import redact_run_payload

RUN_EVENT_NAMES = {
    "run.started",
    "turn.started",
    "plan.generated",
    "step.selected",
    "tool.proposed",
    "approval.needed",
    "tool.progress",
    "tool.result",
    "turn.completed",
    "run.waiting_approval",
    "run.completed",
    "run.failed",
    "run.denied",
    "run.cancelled",
}

TASK_TERMINAL_EVENT_NAMES = {"run.completed", "run.failed", "run.denied", "run.cancelled", "run.waiting_approval"}


class RunEventBus:
    """Event bus for run events.

    P0-6 fix: _subscriptions and _lock are now instance variables (not class
    variables) so each RunEventBus instance has its own subscriber set.
    Previously they were class variables, causing cross-task event leakage.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[RunEvent]]]] = defaultdict(
            set
        )

    def publish(
        self,
        run_id: str,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        created_at: str | None = None,
        sequence: int | None = None,
    ) -> RunEvent:
        db.init_db()
        event_payload = {
            "run_id": run_id,
            "name": name,
            "sequence": sequence or 0,
            "payload": _storage_safe_run_payload(payload or {}),
            "created_at": created_at or now_iso(),
        }
        if event_id:
            event_payload["id"] = event_id
        event = RunEvent.model_validate(db.insert_run_event(event_payload))
        self._publish_to_subscribers(event)
        return event

    def publish_event(self, event: RunEvent) -> RunEvent:
        db.init_db()
        safe_event = event.model_copy(update={"payload": _storage_safe_run_payload(event.payload)})
        stored = RunEvent.model_validate(db.insert_run_event(safe_event))
        self._publish_to_subscribers(stored)
        return stored

    def subscribe(self, run_id: str, *, max_queue_size: int = 100) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=max_queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscriptions[run_id].add((loop, queue))
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        with self._lock:
            subscribers = self._subscriptions.get(run_id)
            if not subscribers:
                return
            for subscription in list(subscribers):
                if subscription[1] is queue:
                    subscribers.discard(subscription)
            if not subscribers:
                self._subscriptions.pop(run_id, None)

    def replay(self, run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[RunEvent]:
        return [
            RunEvent.model_validate(item)
            for item in db.fetch_run_events(run_id, after_sequence=after_sequence, limit=limit)
        ]

    def prune_old_events(self, settings: AppSettings) -> int:
        retention_days = max(0, int(getattr(settings, "run_event_retention_days", 30) or 0))
        if retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        return db.delete_run_events_before(cutoff.isoformat())

    def _publish_to_subscribers(self, event: RunEvent) -> None:
        with self._lock:
            subscribers = list(self._subscriptions.get(event.run_id, set()))
        for loop, queue in subscribers:
            if loop.is_closed():
                self.unsubscribe(event.run_id, queue)
                continue
            loop.call_soon_threadsafe(self._enqueue_event, queue, event)

    @staticmethod
    def _enqueue_event(queue: asyncio.Queue[RunEvent], event: RunEvent) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(event)


def run_event_to_wire(event: RunEvent, *, replay: bool = False) -> dict[str, Any]:
    payload = redact_run_payload(event.model_dump(mode="json"))
    payload["type"] = "run_event"
    payload["event"] = event.name
    payload["event_type"] = event.name
    if replay:
        payload["replay"] = True
    return payload


def task_message_to_run_event(message: AgentMessage, *, run_id: str) -> tuple[str, dict[str, Any]] | None:
    metadata = dict(message.metadata or {})
    payload = dict(message.structured_payload or {})
    event_type = str(payload.get("event_type") or metadata.get("event_type") or "")
    message_type = (
        message.message_type.value if isinstance(message.message_type, MessageType) else str(message.message_type)
    )

    safe_message = llm_safe_agent_message(message, include_legacy=True, redact_user_content=True)
    safe_metadata = safe_message.get("metadata") if isinstance(safe_message.get("metadata"), dict) else {}
    safe_payload = (
        safe_message.get("structured_payload") if isinstance(safe_message.get("structured_payload"), dict) else {}
    )
    base_payload: dict[str, Any] = {
        "task_id": message.task_id,
        "message_id": message.id,
        "step_id": message.step_id,
        "from_agent": message.from_agent,
        "to_agent": message.to_agent,
        "content": safe_message.get("content") or "",
        "message_type": message_type,
        "metadata": safe_metadata,
        "structured_payload": safe_payload,
        "created_at": message.created_at,
    }

    if event_type == "tool.progress" or payload.get("kind") == "tool_progress":
        return "tool.progress", base_payload
    if message.tool_calls:
        base_payload["tool_calls"] = safe_message.get("tool_calls") or []
        return "tool.proposed", base_payload
    if message.tool_call_id and message.role.value == "tool":
        return "tool.result", base_payload
    if payload.get("tool_call_id") and ("ok" in payload or "error" in payload):
        return "tool.result", base_payload
    if payload.get("steps") and payload.get("goal"):
        return "plan.generated", base_payload
    if payload.get("subagent_action"):
        return "step.selected", base_payload
    if message.from_agent == "HumanGateAgent" or "approval" in message.content.casefold():
        return "approval.needed", base_payload
    return None


run_event_bus = RunEventBus()


def _storage_safe_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_run_payload(dict(payload or {}))
    return redacted if isinstance(redacted, dict) else {}
