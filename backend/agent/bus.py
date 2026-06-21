from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

_log = logging.getLogger(__name__)


class AgentBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            self._subs[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            try:
                self._subs[topic].remove(callback)
            except ValueError:
                pass

    def publish(self, topic: str, event: Any = None) -> None:
        # P1-16 fix: snapshot the subscriber list under the lock, then invoke
        # callbacks outside the lock so a slow or reentrant subscriber cannot
        # deadlock or block other threads. Each callback is isolated so one
        # failing subscriber cannot prevent the others from receiving the event.
        with self._lock:
            callbacks = list(self._subs.get(topic, ()))
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                _log.exception("AgentBus subscriber error on topic %r", topic)


EventBus = AgentBus
MessageBus = AgentBus
