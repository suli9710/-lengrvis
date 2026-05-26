from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from typing import Any

from app.config import AppSettings
from app.context_management import project_ledger_for_llm
from app.core import db
from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole


GLOBAL_TASK_ID = "__global__"
_ALL_EVENT_TYPES = "*"


class AgentBus:
    _lock = threading.RLock()
    _subscriptions: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]]] = defaultdict(set)
    _global_subscriptions: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[AgentMessage]]]] = defaultdict(set)

    def publish(self, message: AgentMessage) -> AgentMessage:
        db.init_db()
        db.upsert_model("agent_messages", message)
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
        for loop, queue in subscribers:
            if loop.is_closed():
                self.unsubscribe(message.task_id, queue)
                continue
            loop.call_soon_threadsafe(self._enqueue_message, queue, message)
        for event_type, loop, queue in global_subscribers:
            if loop.is_closed():
                self.unsubscribe_global(queue, event_type)
                continue
            loop.call_soon_threadsafe(self._enqueue_message, queue, message)

    @staticmethod
    def _enqueue_message(queue: asyncio.Queue[AgentMessage], message: AgentMessage) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass

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
        openai_role = OpenAIMessageRole(role) if role else (
            OpenAIMessageRole.USER if from_agent.lower() in {"user", "human"} else OpenAIMessageRole.ASSISTANT
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

    def get_messages(self, task_id: str) -> list[AgentMessage]:
        return [AgentMessage.model_validate(item) for item in db.fetch_many("agent_messages", "task_id = ?", (task_id,))]

    def get_messages_after(self, task_id: str, created_after: str | None, *, limit: int = 500) -> list[AgentMessage]:
        if not created_after:
            messages = self.get_messages(task_id)
        else:
            messages = [
                AgentMessage.model_validate(item)
                for item in db.fetch_many(
                    "agent_messages",
                    "task_id = ? AND created_at > ?",
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
        messages = sorted(self.get_messages(task_id), key=lambda message: (message.created_at, message.id))
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
            for loop, queue in self._global_subscriptions.get(key, set()):
                matches.append((key, loop, queue))
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
