from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI


class LazyASGIApp:
    def __init__(self, factory: Callable[[], FastAPI], *, title: str, version: str) -> None:
        self._factory = factory
        self._app: FastAPI | None = None
        self._lock = threading.Lock()
        self.title = title
        self.version = version

    def resolve(self) -> FastAPI:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = self._factory()
        return self._app

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[[], Awaitable[Any]], send: Callable[[Any], Awaitable[None]]
    ) -> None:
        await self.resolve()(scope, receive, send)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resolve(), name)
