from __future__ import annotations

import os
import threading
from pathlib import Path
from string import Template
from typing import Any, Mapping

from app.config import AppSettings, get_base_settings


_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent
PROMPT_DIR = _DEFAULT_PROMPTS_DIR
_PROMPTS_DIR = PROMPT_DIR
_CACHE_LOCK = threading.RLock()
_cache: dict[str, tuple[str, float]] = {}
_CACHE = _cache
_DEV_MODE_CACHE: bool | None = None


def load_prompt(
    name: str,
    variables: Mapping[str, Any] | None = None,
    *,
    force_reload: bool = False,
) -> str:
    """Load a prompt markdown file, rechecking mtimes only in development."""
    path = prompt_path(name)
    cache_key = _cache_key(path)
    dev = _dev_mode()

    try:
        if dev or force_reload:
            mtime = path.stat().st_mtime
        else:
            with _CACHE_LOCK:
                cached = _cache.get(cache_key)
            if cached:
                content = cached[0]
                return _render(content, variables) if variables else content
            mtime = path.stat().st_mtime
    except OSError:
        if dev or force_reload:
            with _CACHE_LOCK:
                _cache.pop(cache_key, None)
        return ""

    with _CACHE_LOCK:
        cached = _cache.get(cache_key)
        if cached and not force_reload:
            cached_content, cached_mtime = cached
            if not dev or mtime == cached_mtime:
                return _render(cached_content, variables) if variables else cached_content

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        _cache[cache_key] = (content, mtime)

    return _render(content, variables) if variables else content


def render_prompt(file_name: str, variables: Mapping[str, Any]) -> str:
    return load_prompt(file_name, variables)


def clear_prompt_cache() -> None:
    _clear_dev_mode_cache()
    invalidate_prompt_cache()


def invalidate_prompt_cache(path: str | Path | None = None) -> None:
    """Clear all cached prompts, or one cached prompt resolved from a path."""
    with _CACHE_LOCK:
        if path is None:
            _cache.clear()
            return
        _cache.pop(_cache_key(prompt_path(str(path)) if not Path(path).is_absolute() else Path(path)), None)


def reload_prompt_cache() -> dict[str, int]:
    """Clear and eagerly reload all prompt files."""
    clear_prompt_cache()
    count = 0
    for path in sorted(_prompt_dir().glob("*.md")):
        load_prompt(path.name, force_reload=True)
        count += 1
    return {"reloaded": count}


def start_prompt_watcher() -> bool:
    """Compatibility no-op: prompt hot reload now happens through mtime checks."""
    return _dev_mode()


def stop_prompt_watcher() -> None:
    """Compatibility no-op for older tests and callers."""


def prompt_path(file_name: str) -> Path:
    raw = Path(file_name)
    prompt_dir = _prompt_dir()
    if raw.is_absolute() or raw.drive or ".." in raw.parts:
        raise ValueError(f"Prompt path must stay inside {prompt_dir}: {file_name}")
    path = prompt_dir / raw
    if not path.suffix:
        path = path.with_suffix(".md")
    return path


def hot_reload_enabled() -> bool:
    return _dev_mode()


def _dev_mode(settings: AppSettings | None = None) -> bool:
    if _truthy(os.environ.get("MAVRIS_DEV")) or _truthy(os.environ.get("MARVIS_DEV")):
        return True
    env_mode = os.environ.get("MARVIS_MODE") or os.environ.get("MAVRIS_MODE")
    if env_mode is not None:
        return env_mode.strip().lower() == "dev"
    global _DEV_MODE_CACHE
    if settings is None and _DEV_MODE_CACHE is not None:
        return _DEV_MODE_CACHE
    if settings is None:
        try:
            settings = get_base_settings()
        except Exception:  # noqa: BLE001
            settings = None
    dev = (getattr(settings, "mode", "") or "").lower() == "dev"
    if settings is not None:
        _DEV_MODE_CACHE = dev
    return dev


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clear_dev_mode_cache() -> None:
    global _DEV_MODE_CACHE
    _DEV_MODE_CACHE = None


def _cache_key(path: Path) -> str:
    try:
        return path.relative_to(_prompt_dir()).as_posix()
    except ValueError:
        return path.name


def _prompt_dir() -> Path:
    if PROMPT_DIR != _DEFAULT_PROMPTS_DIR:
        return PROMPT_DIR
    return _PROMPTS_DIR


def _render(content: str, variables: Mapping[str, Any] | None) -> str:
    if not variables:
        return content
    values = {key: _stringify(value) for key, value in variables.items()}
    return Template(content).safe_substitute(values).strip()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
