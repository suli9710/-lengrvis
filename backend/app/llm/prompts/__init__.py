from __future__ import annotations

import atexit
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Mapping

from app.indexer.file_watcher import DirectoryChangeWatcher


PROMPT_DIR = Path(__file__).resolve().parent
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CachedPrompt:
    content: str
    mtime_ns: int
    size: int


_CACHE: dict[Path, _CachedPrompt] = {}
_CACHE_LOCK = threading.RLock()
_WATCHER_LOCK = threading.RLock()
_PROMPT_WATCHER: DirectoryChangeWatcher | None = None
_WATCHED_PROMPT_DIR: Path | None = None


def load_prompt(file_name: str, variables: Mapping[str, Any] | None = None) -> str:
    """Load a prompt markdown file, with mtime-based hot reload in development."""
    path = prompt_path(file_name)
    _ensure_prompt_watcher()
    try:
        content = _load_prompt_text(path)
    except OSError:
        return ""
    if variables:
        return _render(content, variables)
    return content


def render_prompt(file_name: str, variables: Mapping[str, Any]) -> str:
    return load_prompt(file_name, variables)


def clear_prompt_cache() -> None:
    invalidate_prompt_cache()


def invalidate_prompt_cache(path: str | Path | None = None) -> None:
    """Clear all cached prompts, or one cached prompt resolved from a changed path."""
    with _CACHE_LOCK:
        if path is None:
            _CACHE.clear()
            return

        prompt_file = Path(path).expanduser().resolve(strict=False)
        _CACHE.pop(prompt_file, None)


def start_prompt_watcher() -> bool:
    """Start watching prompt markdown files when hot reload is enabled."""
    if not hot_reload_enabled():
        return False

    prompt_dir = PROMPT_DIR.resolve(strict=False)
    if not prompt_dir.is_dir():
        return False

    global _PROMPT_WATCHER, _WATCHED_PROMPT_DIR
    with _WATCHER_LOCK:
        if _PROMPT_WATCHER is not None and _WATCHED_PROMPT_DIR == prompt_dir:
            return True

        _stop_prompt_watcher_locked()

        watcher = DirectoryChangeWatcher(
            lambda path, _action: invalidate_prompt_cache(path),
            suffixes={".md"},
        )
        if not watcher.start([prompt_dir]):
            return False
        _PROMPT_WATCHER = watcher
        _WATCHED_PROMPT_DIR = prompt_dir
        return True


def stop_prompt_watcher() -> None:
    with _WATCHER_LOCK:
        _stop_prompt_watcher_locked()


def prompt_path(file_name: str) -> Path:
    raw = Path(file_name)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError(f"Prompt path must stay inside {PROMPT_DIR}: {file_name}")
    path = PROMPT_DIR / raw
    if not path.suffix:
        path = path.with_suffix(".md")
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(PROMPT_DIR):
        raise ValueError(f"Prompt path must stay inside {PROMPT_DIR}: {file_name}")
    return resolved


def hot_reload_enabled() -> bool:
    raw = os.environ.get("MARVIS_PROMPT_HOT_RELOAD") or os.environ.get("MAVRIS_PROMPT_HOT_RELOAD")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    env = (
        os.environ.get("MARVIS_ENV")
        or os.environ.get("MAVRIS_ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    if env in {"prod", "production"}:
        return False
    return True


def _load_prompt_text(path: Path) -> str:
    with _CACHE_LOCK:
        cached = _CACHE.get(path)
        if cached is not None and not hot_reload_enabled():
            return cached.content

        stat = path.stat()
        if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.content

        content = path.read_text(encoding="utf-8").strip()
        _CACHE[path] = _CachedPrompt(content=content, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
    return content


def _ensure_prompt_watcher() -> None:
    if not hot_reload_enabled():
        return
    try:
        start_prompt_watcher()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Prompt hot reload watcher is unavailable: %s", exc)


def _stop_prompt_watcher_locked() -> None:
    global _PROMPT_WATCHER, _WATCHED_PROMPT_DIR
    if _PROMPT_WATCHER is None:
        _WATCHED_PROMPT_DIR = None
        return

    watcher = _PROMPT_WATCHER
    _PROMPT_WATCHER = None
    _WATCHED_PROMPT_DIR = None
    watcher.stop()


def _render(content: str, variables: Mapping[str, Any]) -> str:
    values = {key: _stringify(value) for key, value in variables.items()}
    return Template(content).safe_substitute(values).strip()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


atexit.register(stop_prompt_watcher)
