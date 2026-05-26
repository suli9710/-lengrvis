from __future__ import annotations

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.audit import record
from app.core.paths import resolve_authorized


_DEBOUNCE_SECONDS = 2.0
FileChangeCallback = Callable[[str, str], None]
AsyncFileChangeCallback = Callable[[str, str], Awaitable[None] | None]

# File name patterns to skip
_SKIP_PREFIXES = (".", "~$")
_SKIP_SUFFIXES = (".tmp",)


def _should_skip(path: str) -> bool:
    """Return True if the file should be ignored by the watcher."""
    name = os.path.basename(path)
    if any(name.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return True
    if any(name.endswith(suffix) for suffix in _SKIP_SUFFIXES):
        return True
    return False


class _FileChangeHandler(FileSystemEventHandler):
    """Watchdog handler that normalizes file events before invoking a callback."""

    def __init__(
        self,
        callback: FileChangeCallback,
        *,
        allowed_directories: list[str] | None = None,
        suffixes: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._callback = callback
        self._allowed_directories = allowed_directories
        self._suffixes = suffixes

    def _emit(self, path: str, action: str) -> None:
        if _should_skip(path):
            return
        if self._suffixes is not None and Path(path).suffix.lower() not in self._suffixes:
            return
        if self._allowed_directories is not None:
            try:
                resolve_authorized(path, self._allowed_directories)
            except Exception:
                return
        self._callback(path, action)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path, "upsert")

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path, "upsert")

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path, "delete")

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._emit(event.src_path, "delete")
            self._emit(event.dest_path, "upsert")


class DirectoryChangeWatcher:
    """Small reusable watchdog wrapper for directory file-change callbacks."""

    def __init__(
        self,
        callback: FileChangeCallback,
        *,
        allowed_directories: list[str] | None = None,
        suffixes: set[str] | None = None,
        recursive: bool = True,
    ) -> None:
        self._callback = callback
        self._allowed_directories = allowed_directories
        self._suffixes = suffixes
        self._recursive = recursive
        self._observer: Observer | None = None

    def start(self, directories: list[str | Path]) -> bool:
        if self._observer is not None:
            return True

        watch_dirs = [
            Path(raw_dir).expanduser().resolve(strict=False)
            for raw_dir in directories
            if Path(raw_dir).expanduser().resolve(strict=False).is_dir()
        ]
        if not watch_dirs:
            return False

        observer = Observer()
        handler = _FileChangeHandler(
            self._callback,
            allowed_directories=self._allowed_directories,
            suffixes=self._suffixes,
        )
        for dir_path in watch_dirs:
            observer.schedule(handler, str(dir_path), recursive=self._recursive)
        observer.start()
        self._observer = observer
        return True

    def stop(self) -> None:
        if self._observer is None:
            return
        observer = self._observer
        self._observer = None
        observer.stop()
        observer.join(timeout=5)


class FileWatcher:
    """Singleton file watcher that incrementally indexes file changes."""

    def __init__(self, *, debounce_seconds: float = _DEBOUNCE_SECONDS) -> None:
        self._observer: Observer | None = None
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        from app.indexer.fts_index import FTSIndex

        self._fts_index = FTSIndex()
        self._consumer_task: asyncio.Task[None] | None = None
        self._allowed_directories: list[str] = []
        self._debounce_seconds = debounce_seconds
        self._change_callbacks: list[AsyncFileChangeCallback] = []

    def subscribe_changes(self, callback: AsyncFileChangeCallback) -> None:
        if callback not in self._change_callbacks:
            self._change_callbacks.append(callback)

    def unsubscribe_changes(self, callback: AsyncFileChangeCallback) -> None:
        self._change_callbacks = [item for item in self._change_callbacks if item is not callback]

    async def start(self, allowed_directories: list[str]) -> None:
        """Start watching all allowed directories for file changes."""
        if self._observer is not None:
            return

        self._allowed_directories = list(allowed_directories)
        loop = asyncio.get_running_loop()

        def enqueue(path: str, action: str) -> None:
            loop.call_soon_threadsafe(self._queue.put_nowait, (path, action))

        self._observer = Observer()
        handler = _FileChangeHandler(
            enqueue,
            allowed_directories=self._allowed_directories,
        )

        for raw_dir in self._allowed_directories:
            dir_path = Path(raw_dir).expanduser().resolve(strict=False)
            if dir_path.is_dir():
                self._observer.schedule(handler, str(dir_path), recursive=True)

        self._observer.start()
        self._consumer_task = asyncio.create_task(
            self._consume(), name="mavris-file-watcher"
        )
        record(
            "file_watcher.started",
            "FileWatcher",
            {"directories": self._allowed_directories},
        )

    async def stop(self) -> None:
        """Stop the observer and consumer task."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

        record("file_watcher.stopped", "FileWatcher", {})

    async def _consume(self) -> None:
        """Consumer loop: read events, debounce, and index/remove files."""
        pending: dict[str, tuple[str, float]] = {}

        while True:
            # Drain available events from the queue
            try:
                path, action = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5
                )
                pending[path] = (action, time.monotonic())
                # Drain any additional queued events without waiting
                while not self._queue.empty():
                    try:
                        path, action = self._queue.get_nowait()
                        pending[path] = (action, time.monotonic())
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                pass

            # Process entries that have been quiet for debounce_seconds
            now = time.monotonic()
            ready = [
                p
                for p, (_, ts) in pending.items()
                if now - ts >= self._debounce_seconds
            ]

            for path in ready:
                action, _ = pending.pop(path)
                try:
                    if action == "upsert":
                        normalized = str(
                            Path(path).expanduser().resolve(strict=False)
                        )
                        self._fts_index.index_file(
                            normalized, self._allowed_directories
                        )
                    elif action == "delete":
                        normalized = str(
                            Path(path).expanduser().resolve(strict=False)
                        )
                        self._fts_index.remove_file(normalized)
                except Exception as exc:
                    record(
                        "file_watcher.error",
                        "FileWatcher",
                        {"path": path, "action": action, "error": str(exc)},
                    )
                await self._notify_change(path, action)

    async def _notify_change(self, path: str, action: str) -> None:
        for callback in list(self._change_callbacks):
            try:
                result = callback(path, action)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                record(
                    "file_watcher.change_callback_error",
                    "FileWatcher",
                    {
                        "path": path,
                        "action": action,
                        "callback": repr(callback),
                        "error": str(exc),
                    },
                )


_instance: FileWatcher | None = None


def get_file_watcher(**kwargs: Any) -> FileWatcher:
    """Return the singleton FileWatcher instance."""
    global _instance
    if _instance is None:
        _instance = FileWatcher(**kwargs)
    return _instance


def watch_directories(paths: list[str]) -> dict:
    """Backward-compatible entry point."""
    watcher = get_file_watcher()
    return {"watching": paths, "watcher": "FileWatcher", "status": "use await watcher.start(paths)"}
