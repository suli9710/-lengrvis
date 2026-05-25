from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class AgentBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        self._subs[topic].append(callback)

    def publish(self, topic: str, event: Any = None) -> None:
        for callback in self._subs.get(topic, []):
            callback(event)


EventBus = AgentBus
MessageBus = AgentBus

