"""In-process broker for the authenticated Electron BrowserHost bridge."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import re
import threading
import uuid
from dataclasses import dataclass
from itertools import islice
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import WebSocket

_MAX_BRIDGE_SESSIONS = 32
_MAX_BRIDGE_EVENTS = 300
_MAX_STRING_CHARS = 4096
_MAX_NESTING = 8
_MAX_CONTAINER_ITEMS = 64
_MAX_SNAPSHOT_CHARS = 512_000
_MAX_SCREENSHOT_ARTIFACT_URL_CHARS = 2048
_SCREENSHOT_DIRECTORY_RE = re.compile(r"^lengrvis-browser-screenshots-\d+-[0-9a-f-]{36}$", re.IGNORECASE)
_SCREENSHOT_FILE_RE = re.compile(r"^[0-9a-f-]{36}\.png$", re.IGNORECASE)


class BrowserHostBridgeUnavailable(RuntimeError):
    """Raised when no authenticated Desktop BrowserHost bridge is connected."""


class BrowserHostBridgeHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._websocket: WebSocket | None = None
        self._socket_loop: asyncio.AbstractEventLoop | None = None
        self._send_lock: asyncio.Lock | None = None
        self._snapshot: dict[str, Any] | None = None
        self._pending: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}

    def connect(self, websocket: WebSocket) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._fail_pending_locked("Desktop BrowserHost bridge was replaced.")
            self._websocket = websocket
            self._socket_loop = loop
            self._send_lock = asyncio.Lock()

    def disconnect(self, websocket: WebSocket) -> None:
        with self._lock:
            if self._websocket is not websocket:
                return
            self._websocket = None
            self._socket_loop = None
            self._send_lock = None
            self._snapshot = None
            self._fail_pending_locked("Desktop BrowserHost bridge disconnected.")

    def receive_snapshot(self, websocket: WebSocket, value: Any) -> None:
        if not isinstance(value, dict):
            return
        with self._lock:
            if self._websocket is not websocket:
                return
            self._snapshot = _sanitize_snapshot(value)

    def receive_result(self, websocket: WebSocket, value: Any) -> None:
        if not isinstance(value, dict):
            return
        request_id = value.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return
        with self._lock:
            if self._websocket is not websocket:
                return
            pending = self._pending.get(request_id)
        if pending is not None and not pending.done():
            pending.set_result(_sanitize_result(value))

    async def request_read_only_action(
        self,
        *,
        session_id: str,
        action: dict[str, str],
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        response: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        with self._lock:
            websocket = self._websocket
            socket_loop = self._socket_loop
            if websocket is None or socket_loop is None:
                raise BrowserHostBridgeUnavailable("No authenticated Desktop BrowserHost bridge is connected.")
            self._pending[request_id] = response

        try:
            await self._send(
                websocket,
                socket_loop,
                {
                    "type": "action",
                    "request_id": request_id,
                    "session_id": session_id,
                    # The route has already reduced this to a strict, read-only action.
                    "action": action,
                },
            )
            return await asyncio.wait_for(asyncio.wrap_future(response), timeout=timeout_seconds)
        except BrowserHostBridgeUnavailable:
            raise
        except TimeoutError as exc:
            raise TimeoutError("Desktop BrowserHost bridge did not respond in time.") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    async def _send(
        self,
        websocket: WebSocket,
        socket_loop: asyncio.AbstractEventLoop,
        payload: dict[str, Any],
    ) -> None:
        async def send_in_socket_loop() -> None:
            with self._lock:
                if self._websocket is not websocket or self._send_lock is None:
                    raise BrowserHostBridgeUnavailable("Desktop BrowserHost bridge disconnected.")
                send_lock = self._send_lock
            async with send_lock:
                await websocket.send_json(payload)

        if asyncio.get_running_loop() is socket_loop:
            await send_in_socket_loop()
            return
        try:
            send = asyncio.run_coroutine_threadsafe(send_in_socket_loop(), socket_loop)
            await asyncio.wrap_future(send)
        except RuntimeError as exc:
            raise BrowserHostBridgeUnavailable("Desktop BrowserHost bridge disconnected.") from exc

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self._websocket is not None,
                "snapshot": copy.deepcopy(self._snapshot),
            }

    def _fail_pending_locked(self, message: str) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(BrowserHostBridgeUnavailable(message))


def _sanitize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    budget = _SanitizeBudget()
    sessions = snapshot.get("sessions")
    events = snapshot.get("events")
    return {
        "sessions": _sanitize_json(
            sessions[:_MAX_BRIDGE_SESSIONS] if isinstance(sessions, list) else [], depth=0, budget=budget
        ),
        "events": _sanitize_json(
            events[:_MAX_BRIDGE_EVENTS] if isinstance(events, list) else [],
            depth=0,
            budget=budget,
        ),
        "activeSessionId": _bounded_string(snapshot.get("activeSessionId"), budget=budget),
        "visible": bool(snapshot.get("visible")),
        "hostAvailable": bool(snapshot.get("hostAvailable")),
    }


def _sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    budget = _SanitizeBudget()
    raw_event = result.get("event")
    event = _sanitize_json(raw_event, depth=0, budget=budget)
    if isinstance(raw_event, dict) and isinstance(event, dict):
        screenshot_url = _safe_screenshot_artifact_url(raw_event.get("screenshot_url"))
        if screenshot_url:
            # The Electron bridge only preserves file URLs created by its
            # bounded screenshot store. Keep that reference for an explicit
            # screenshot command result; snapshots and data URLs remain redacted.
            event["screenshot_url"] = budget.take(screenshot_url)
    return {
        "ok": bool(result.get("ok")),
        "session": _sanitize_json(result.get("session"), depth=0, budget=budget),
        "event": event,
        "error": _bounded_string(result.get("error"), budget=budget),
    }


@dataclass
class _SanitizeBudget:
    remaining: int = _MAX_SNAPSHOT_CHARS

    def take(self, value: str) -> str:
        if self.remaining <= 0:
            return "[truncated]"
        bounded = value[: min(_MAX_STRING_CHARS, self.remaining)]
        self.remaining -= len(bounded)
        return bounded


def _sanitize_json(value: Any, *, depth: int, budget: _SanitizeBudget, key: str = "") -> Any:
    if depth >= _MAX_NESTING:
        return "[truncated]"
    normalized_key = key.casefold().replace("-", "_")
    if normalized_key in {"screenshot", "screenshot_url", "screenshoturl", "image_url", "imageurl"}:
        return "[redacted:screenshot]" if value else None
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for item_key, item in islice(value.items(), _MAX_CONTAINER_ITEMS):
            bounded_key = budget.take(str(item_key)[:128])
            if bounded_key == "[truncated]":
                break
            sanitized[bounded_key] = _sanitize_json(item, depth=depth + 1, budget=budget, key=str(item_key))
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_json(item, depth=depth + 1, budget=budget)
            for item in value[:_MAX_CONTAINER_ITEMS]
            if budget.remaining > 0
        ]
    if isinstance(value, str):
        return budget.take(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _bounded_string(value, budget=budget)


def _bounded_string(value: Any, *, budget: _SanitizeBudget) -> str | None:
    if value is None:
        return None
    return budget.take(str(value))


def _safe_screenshot_artifact_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_SCREENSHOT_ARTIFACT_URL_CHARS:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        return None
    if parsed.query or parsed.fragment:
        return None
    parts = [part for part in unquote(parsed.path).replace("\\", "/").split("/") if part]
    if len(parts) < 2:
        return None
    if not _SCREENSHOT_DIRECTORY_RE.fullmatch(parts[-2]):
        return None
    if not _SCREENSHOT_FILE_RE.fullmatch(parts[-1]):
        return None
    return value


browser_host_bridge_hub = BrowserHostBridgeHub()
