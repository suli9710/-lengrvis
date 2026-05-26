from __future__ import annotations

import asyncio
import threading
from collections import defaultdict

from app.core.schemas import Approval
from app.policy.approval_binding import redacted_preview
from app.policy.redaction import redact_value


class ApprovalEventBus:
    _lock = threading.RLock()
    _subscriptions: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict]]] = set()

    def publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscriptions)
        for loop, queue in subscribers:
            if loop.is_closed():
                self.unsubscribe(queue)
                continue
            loop.call_soon_threadsafe(self._enqueue_event, queue, event)

    def subscribe(self, *, max_queue_size: int = 100) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue_size)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscriptions.add((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        with self._lock:
            for subscription in list(self._subscriptions):
                if subscription[1] is queue:
                    self._subscriptions.discard(subscription)

    @staticmethod
    def _enqueue_event(queue: asyncio.Queue[dict], event: dict) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


_bus = ApprovalEventBus()


def get_approval_event_bus() -> ApprovalEventBus:
    return _bus


def publish_approval_created(approval: Approval) -> None:
    _bus.publish({"type": "approval_created", "approval": _safe_approval(approval)})


def publish_approval_decided(approval: Approval) -> None:
    _bus.publish({"type": "approval_decided", "approval": _safe_approval(approval)})


def _safe_approval(approval: Approval) -> dict:
    payload = approval.model_dump(mode="json")
    payload["message"] = redact_value(payload.get("message") or "")
    payload["diff_preview"] = redacted_preview(payload.get("diff_preview") or {})
    return payload
