from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Union

from app.core.audit import record
from app.core.schemas import MessageType

if TYPE_CHECKING:
    from app.orchestration.agent_bus import AgentBus

logger = logging.getLogger(__name__)

EventHandler = Union[Callable[..., Any], Callable[..., Awaitable[Any]]]

_WILDCARD = "*"


def _event_payload(event: Any) -> dict[str, Any]:
    """Extract payload dict from an event object.

    Works with Pydantic models (``model_dump``) and plain objects that carry a
    ``payload`` attribute.  Base event fields are stripped so the result only
    contains event-specific data.
    """
    if hasattr(event, "model_dump"):
        data = event.model_dump()
        for key in ("id", "event_type", "task_id", "timestamp", "source_agent", "payload"):
            data.pop(key, None)
        return data
    if hasattr(event, "payload"):
        return event.payload  # type: ignore[return-value]
    return {}


def _event_to_dict_payload(event: Any) -> dict[str, Any]:
    """Build a full dict representation suitable for structured_payload."""
    if hasattr(event, "model_dump"):
        return event.model_dump()
    result: dict[str, Any] = {}
    for attr in ("id", "event_type", "task_id", "timestamp", "source_agent", "payload"):
        if hasattr(event, attr):
            result[attr] = getattr(event, attr)
    return result


class EventDispatcher:
    """Central event dispatcher with handler registration, ordered execution,
    and AgentBus / audit integration.

    Handlers are invoked in registration order for a given event type.  Both
    sync and async callables are supported.  The optional queue (``start`` /
    ``stop``) enables ordered background processing via ``dispatch_async``.
    """

    def __init__(self, bus: AgentBus | None = None) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._bus = bus
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._running = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type.

        Use ``"*"`` as the event type to receive **all** events (wildcard).
        """
        self._handlers[event_type].append(handler)

    def register_many(self, event_type: str, handlers: list[EventHandler]) -> None:
        """Register multiple handlers for an event type."""
        for handler in handlers:
            self.register(event_type, handler)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: Any) -> list[Any]:
        """Dispatch *event* to all registered handlers, then to the bus and
        audit log.

        Handlers are called in registration order.  If a handler raises, the
        error is logged and remaining handlers still execute.

        Returns the list of handler return values (errors produce ``None``).
        """
        event_type: str = getattr(event, "event_type", "")
        task_id: str = getattr(event, "task_id", "")
        source_agent: str = getattr(event, "source_agent", "") or "EventDispatcher"

        # Collect handlers: specific first, then wildcard.
        handlers: list[EventHandler] = list(self._handlers.get(event_type, []))
        if event_type != _WILDCARD:
            handlers.extend(self._handlers.get(_WILDCARD, []))

        results: list[Any] = []
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(event)
                else:
                    result = handler(event)
                results.append(result)
            except Exception:
                logger.exception(
                    "Handler %r failed for event %s",
                    handler,
                    event_type,
                )
                results.append(None)

        # Publish to AgentBus when available.
        if self._bus is not None:
            try:
                summary = event.summary() if hasattr(event, "summary") else str(event)
                self._bus.publish_text(
                    task_id,
                    source_agent,
                    summary,
                    message_type=MessageType.NOTIFICATION,
                    structured_payload=_event_to_dict_payload(event),
                )
            except Exception:
                logger.exception("Failed to publish event %s to AgentBus", event_type)

        # Record in audit log.
        try:
            payload = _event_payload(event)
            base_payload = getattr(event, "payload", None)
            if isinstance(base_payload, dict) and base_payload:
                payload.update(base_payload)
            record(event_type, source_agent, payload, task_id=task_id or None)
        except Exception:
            logger.exception("Failed to record audit for event %s", event_type)

        return results

    # ------------------------------------------------------------------
    # Async queue processing
    # ------------------------------------------------------------------

    async def dispatch_async(self, event: Any) -> None:
        """Enqueue *event* for background processing.  Does **not** wait for
        handlers to run."""
        await self._queue.put(event)

    async def start(self) -> None:
        """Start the background event processing loop."""
        self._running = True
        await self._process_queue()

    async def stop(self) -> None:
        """Signal the processing loop to stop after draining current items."""
        self._running = False
        # Push a sentinel so _process_queue wakes up and exits.
        await self._queue.put(None)

    async def _process_queue(self) -> None:
        """Internal loop that processes queued events in order."""
        while self._running:
            event = await self._queue.get()
            if event is None:
                # Sentinel received -- exit cleanly.
                break
            try:
                await self.dispatch(event)
            except Exception:
                logger.exception("Unhandled error while processing queued event")
            finally:
                self._queue.task_done()
