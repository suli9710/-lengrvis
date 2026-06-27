from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from collections import defaultdict
from contextlib import suppress
from typing import Any

from app.config import AppSettings
from app.context.management import project_ledger_for_llm
from app.core import db
from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole

GLOBAL_TASK_ID = "__global__"
_ALL_EVENT_TYPES = "*"
logger = logging.getLogger(__name__)

# --- P1 fix: Bounded persist queue with lossless backpressure ---
# Previously _PERSIST_QUEUE was an unbounded queue.Queue(), which meant that
# under sustained high message throughput (e.g. long-running orchestrator
# tasks, burst perception events) the queue could grow without limit, causing
# unbounded memory growth and eventual OOM.
#
# The queue now has a configurable max size. When full, publishers apply
# backpressure by waiting until the writer drains space. The durable queue must
# never drop agent_messages: assistant tool_calls and matching tool results are
# the authoritative ledger used to rebuild LLM context and audit history.

_PERSIST_QUEUE_MAX_SIZE = int(os.environ.get("LENGRVIS_PERSIST_QUEUE_MAX_SIZE", "10000"))
_PERSIST_BACKPRESSURE_TIMEOUT = float(os.environ.get("LENGRVIS_PERSIST_BACKPRESSURE_TIMEOUT", "5.0"))
_PERSIST_BACKPRESSURE_MAX_WAIT = float(os.environ.get("LENGRVIS_PERSIST_BACKPRESSURE_MAX_WAIT", "30.0"))
_PERSIST_QUEUE: queue.Queue[tuple[AgentMessage, str]] = queue.Queue(maxsize=_PERSIST_QUEUE_MAX_SIZE)
_PERSIST_STATE = threading.Condition()
_PERSIST_PENDING = 0
_PERSIST_THREAD: threading.Thread | None = None
_PERSIST_THREAD_LOCK = threading.Lock()


def _persist_worker() -> None:
    global _PERSIST_PENDING
    while True:
        message, data_dir = _PERSIST_QUEUE.get()
        try:
            with db.using_data_dir(data_dir):
                db.init_db()
                db.upsert_model("agent_messages", message)
        except Exception:  # noqa: BLE001
            logger.exception("agent_bus: failed to persist message %s", message.id)
        finally:
            with _PERSIST_STATE:
                _PERSIST_PENDING -= 1
                _PERSIST_STATE.notify_all()


def _ensure_persist_thread() -> None:
    global _PERSIST_THREAD
    with _PERSIST_THREAD_LOCK:
        if _PERSIST_THREAD is None or not _PERSIST_THREAD.is_alive():
            _PERSIST_THREAD = threading.Thread(target=_persist_worker, name="agent-bus-writer", daemon=True)
            _PERSIST_THREAD.start()


def _enqueue_persist(message: AgentMessage) -> None:
    global _PERSIST_PENDING
    _ensure_persist_thread()
    data_dir = str(db.db_path().parent)

    # --- P1 fix: Backpressure instead of unbounded growth ---
    queue_depth = _PERSIST_QUEUE.qsize()
    if queue_depth >= int(_PERSIST_QUEUE_MAX_SIZE * 0.9):
        logger.warning(
            "agent_bus: persist queue near capacity (%d/%d); applying backpressure",
            queue_depth,
            _PERSIST_QUEUE_MAX_SIZE,
        )
    elif queue_depth >= int(_PERSIST_QUEUE_MAX_SIZE * 0.8):
        logger.info(
            "agent_bus: persist queue at 80%% capacity (%d/%d)",
            queue_depth,
            _PERSIST_QUEUE_MAX_SIZE,
        )

    # Count the message before queueing: the writer can drain immediately after
    # put() returns, so incrementing afterwards can race the worker decrement.
    with _PERSIST_STATE:
        _PERSIST_PENDING += 1

    item = (message, data_dir)
    try:
        if _running_in_event_loop():
            _PERSIST_QUEUE.put_nowait(item)
        else:
            _put_persist_item_with_backpressure(item, message.id)
    except queue.Full as exc:
        _decrement_pending_persist()
        raise AgentMessagePersistBackpressureError(
            f"agent_bus persist queue is full; refusing to block event loop for message {message.id}"
        ) from exc
    except AgentMessagePersistBackpressureError:
        _decrement_pending_persist()
        raise


def _put_persist_item_with_backpressure(item: tuple[AgentMessage, str], message_id: str) -> None:
    deadline = time.monotonic() + max(0.0, _PERSIST_BACKPRESSURE_MAX_WAIT)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentMessagePersistBackpressureError(
                f"agent_bus persist queue stayed full for {_PERSIST_BACKPRESSURE_MAX_WAIT:.1f}s"
            )
        try:
            _PERSIST_QUEUE.put(item, timeout=min(max(0.001, _PERSIST_BACKPRESSURE_TIMEOUT), remaining))
            return
        except queue.Full:
            logger.error(
                "agent_bus: persist queue full after %.1fs backpressure; waiting to preserve durable message %s",
                _PERSIST_BACKPRESSURE_TIMEOUT,
                message_id,
            )


def _decrement_pending_persist() -> None:
    global _PERSIST_PENDING
    with _PERSIST_STATE:
        _PERSIST_PENDING -= 1
        _PERSIST_STATE.notify_all()


def _running_in_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def flush_agent_message_writes(timeout_seconds: float = 10.0) -> bool:
    """Block until all queued message writes are committed (or timeout)."""
    if threading.current_thread() is _PERSIST_THREAD:
        return True
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    with _PERSIST_STATE:
        while _PERSIST_PENDING > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _PERSIST_STATE.wait(remaining)
    return True


class AgentMessageReadConsistencyError(RuntimeError):
    """Raised when agent_messages reads would observe a stale timeline."""


class AgentMessagePersistBackpressureError(RuntimeError):
    """Raised when a publish cannot be durably queued without blocking asyncio."""


def _flush_agent_messages_for_read() -> None:
    if flush_agent_message_writes(timeout_seconds=10.0):
        return
    if flush_agent_message_writes(timeout_seconds=30.0):
        return
    logger.error("agent_messages write flush timed out; refusing stale read")
    raise AgentMessageReadConsistencyError("agent_messages write flush timed out; read refused to avoid stale timeline")


db.register_read_barrier("agent_messages", _flush_agent_messages_for_read)


class AgentBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]]] = (
            defaultdict(set)
        )
        self._global_subscriptions: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]]] = (
            defaultdict(set)
        )

    def publish(self, message: AgentMessage) -> AgentMessage:
        _enqueue_persist(message)
        self._publish_to_subscribers(message)
        return message

    def subscribe(self, task_id: str, *, max_queue_size: int = 100) -> asyncio.Queue[AgentMessage]:
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=max_queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscriptions[task_id].add((loop, queue))
        return queue

    def subscribe_global(
        self,
        event_type: str = _ALL_EVENT_TYPES,
        *,
        max_queue_size: int = 100,
    ) -> asyncio.Queue[AgentMessage]:
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=max_queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._global_subscriptions[event_type or _ALL_EVENT_TYPES].add((loop, queue))
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[AgentMessage]) -> None:
        with self._lock:
            subscribers = self._subscriptions.get(task_id)
            if not subscribers:
                return
            for subscription in list(subscribers):
                if subscription[1] is queue:
                    subscribers.discard(subscription)
            if not subscribers:
                self._subscriptions.pop(task_id, None)

    def unsubscribe_global(self, queue: asyncio.Queue[AgentMessage], event_type: str | None = None) -> None:
        with self._lock:
            keys = [event_type or _ALL_EVENT_TYPES] if event_type else list(self._global_subscriptions.keys())
            for key in keys:
                subscribers = self._global_subscriptions.get(key)
                if not subscribers:
                    continue
                for subscription in list(subscribers):
                    if subscription[1] is queue:
                        subscribers.discard(subscription)
                if not subscribers:
                    self._global_subscriptions.pop(key, None)

    def _publish_to_subscribers(self, message: AgentMessage) -> None:
        with self._lock:
            subscribers = list(self._subscriptions.get(message.task_id, set()))
            global_subscribers = self._matching_global_subscribers(message)
        for loop, subscriber_queue in subscribers:
            if loop.is_closed():
                self.unsubscribe(message.task_id, subscriber_queue)
                continue
            loop.call_soon_threadsafe(self._enqueue_message, subscriber_queue, message)
        for event_type, loop, subscriber_queue in global_subscribers:
            if loop.is_closed():
                self.unsubscribe_global(subscriber_queue, event_type)
                continue
            loop.call_soon_threadsafe(self._enqueue_message, subscriber_queue, message)

    @staticmethod
    def _enqueue_message(queue: asyncio.Queue[AgentMessage], message: AgentMessage) -> None:
        if queue.full():
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with suppress(asyncio.QueueFull):
            queue.put_nowait(message)

    def publish_text(
        self,
        task_id: str,
        from_agent: str,
        content: str,
        message_type: MessageType = MessageType.PROPOSAL,
        to_agent: str | None = None,
        step_id: str | None = None,
        structured_payload: dict | None = None,
        role: OpenAIMessageRole | str | None = None,
        name: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        openai_role = (
            OpenAIMessageRole(role)
            if role
            else (OpenAIMessageRole.USER if from_agent.lower() in {"user", "human"} else OpenAIMessageRole.ASSISTANT)
        )
        meta = dict(metadata or {})
        meta.setdefault("from_agent", from_agent)
        meta.setdefault("to_agent", to_agent)
        message_type_value = message_type.value if isinstance(message_type, MessageType) else str(message_type)
        meta.setdefault("message_type", message_type_value)
        if structured_payload:
            meta.setdefault("structured_payload", structured_payload)

        normalized_tool_calls = []
        for tool_call in tool_calls or []:
            normalized = dict(tool_call)
            function = dict(normalized.get("function") or {})
            arguments = function.get("arguments")
            if arguments is not None and not isinstance(arguments, str):
                function["arguments"] = json.dumps(arguments, ensure_ascii=False)
            normalized["function"] = function
            normalized_tool_calls.append(normalized)

        return self.publish(
            AgentMessage(
                task_id=task_id,
                step_id=step_id,
                role=openai_role,
                name=name or (None if openai_role == OpenAIMessageRole.TOOL else from_agent),
                tool_calls=normalized_tool_calls,
                tool_call_id=tool_call_id,
                metadata=meta,
                from_agent=from_agent,
                to_agent=to_agent,
                message_type=message_type,
                content=content,
                structured_payload=structured_payload or {},
            )
        )

    def publish_cross_task(
        self,
        from_agent: str,
        content: str,
        *,
        event_type: str = "",
        message_type: MessageType = MessageType.NOTIFICATION,
        structured_payload: dict | None = None,
        metadata: dict[str, Any] | None = None,
        to_agent: str | None = None,
    ) -> AgentMessage:
        payload = dict(structured_payload or {})
        if event_type:
            payload.setdefault("event_type", event_type)
        meta = dict(metadata or {})
        meta["cross_task"] = True
        if event_type:
            meta["event_type"] = event_type
        return self.publish_text(
            GLOBAL_TASK_ID,
            from_agent,
            content,
            message_type=message_type,
            to_agent=to_agent,
            structured_payload=payload,
            metadata=meta,
        )

    def get_messages(self, task_id: str, *, limit: int = 1000) -> list[AgentMessage]:
        return [
            AgentMessage.model_validate(item)
            for item in db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=max(1, limit))
        ]

    def get_messages_after(self, task_id: str, created_after: str | None, *, limit: int = 500) -> list[AgentMessage]:
        if not created_after:
            messages = self.get_messages(task_id, limit=limit)
        else:
            messages = [
                AgentMessage.model_validate(item)
                for item in db.fetch_many(
                    "agent_messages",
                    "task_id = ? AND created_at >= ?",
                    (task_id, created_after),
                    limit=limit,
                )
            ]
        return sorted(messages, key=lambda message: (message.created_at, message.id))

    def get_step_messages(self, task_id: str, step_id: str) -> list[AgentMessage]:
        return [
            AgentMessage.model_validate(item)
            for item in db.fetch_many("agent_messages", "task_id = ? AND step_id = ?", (task_id, step_id))
        ]

    def get_llm_messages(
        self,
        task_id: str,
        settings: AppSettings,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        read_limit = limit if limit > 0 else 1000
        messages = sorted(
            self.get_messages(task_id, limit=read_limit), key=lambda message: (message.created_at, message.id)
        )
        if limit > 0:
            messages = messages[-limit:]
        ledger = [message.to_openai_dict(include_legacy=False) for message in messages]
        return project_ledger_for_llm(ledger, settings, source=f"agent_bus:{task_id}").messages

    def broadcast_to_relevant_agents(self, task_id: str, content: str) -> None:
        for agent in ["FileAgent", "DocumentAgent", "ComputerAgent", "BrowserAgent", "SearchAgent"]:
            self.publish_text(task_id, "OrchestratorAgent", content, to_agent=agent)

    def _matching_global_subscribers(
        self,
        message: AgentMessage,
    ) -> list[tuple[str, asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]]:
        event_type = self._message_event_type(message)
        matches: list[tuple[str, asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]] = []
        for key in {_ALL_EVENT_TYPES, event_type} - {""}:
            for loop, subscriber_queue in self._global_subscriptions.get(key, set()):
                matches.append((key, loop, subscriber_queue))
        return matches

    def _message_event_type(self, message: AgentMessage) -> str:
        payload = message.structured_payload or {}
        meta = message.metadata or {}
        return str(
            payload.get("event_type")
            or meta.get("event_type")
            or meta.get("message_type")
            or message.message_type.value
        )
