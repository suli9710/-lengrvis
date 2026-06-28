from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.core.paths import normalize_path, path_within_explicit_scope
from app.tools.tool_abort import raise_if_tool_aborted


def prepare_parent_for_mutation(path: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    ensure_mutation_path_safe(path.parent, allowed, include_self=True, context=context)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_mutation_path_safe(path.parent, allowed, include_self=True, context=context)


def ensure_mutation_path_safe(
    path: Path,
    allowed: list[str],
    *,
    include_self: bool,
    context: dict[str, Any] | None = None,
) -> None:
    target = path if include_self else path.parent
    real_target = target.expanduser().resolve(strict=False)
    scope = _explicit_scope(context) if context else None
    base = authorized_real_base(real_target, allowed, explicit_scope_text=scope)
    reject_reparse_points(base, target)


def safe_write_text(path: Path, text: str, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    prepare_parent_for_mutation(path, allowed, context)
    ensure_mutation_path_safe(path, allowed, include_self=path_exists_or_reparse_point(path), context=context)
    if supports_dir_fd_no_follow():
        write_text_with_dir_fd_no_follow(path, text)
        return
    path.write_text(text, encoding="utf-8")


def authorized_real_base(
    real_target: Path,
    allowed: list[str],
    *,
    explicit_scope_text: str | None = None,
) -> Path:
    if allowed:
        for raw_base in allowed:
            base = Path(raw_base).expanduser().resolve(strict=False)
            try:
                if real_target == base or real_target.is_relative_to(base):
                    return base
            except ValueError:
                continue
        raise SecurityError("Path resolves outside authorized directories.")
    if explicit_scope_text and path_within_explicit_scope(real_target, explicit_scope_text):
        from app.agents.path_detection import find_explicit_path

        explicit_raw = find_explicit_path(explicit_scope_text)
        if explicit_raw:
            explicit = normalize_path(explicit_raw)
            if explicit.is_dir():
                return explicit
            return explicit.parent
    raise SecurityError("No authorized directories configured.")


def reject_reparse_points(base: Path, target: Path) -> None:
    try:
        relative = target.relative_to(base)
    except ValueError:
        return

    current = base
    for part in relative.parts:
        current = current / part
        if is_reparse_point(current):
            raise SecurityError("Filesystem links inside authorized directories are not writable.")


def path_exists_or_reparse_point(path: Path) -> bool:
    return path.exists() or is_reparse_point(path)


def is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def supports_dir_fd_no_follow() -> bool:
    return hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd


def write_text_with_dir_fd_no_follow(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    mode = 0o666
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path.name, flags, mode, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(text)
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(dir_fd)


def _explicit_scope(context: dict[str, Any] | None) -> str | None:
    value = (context or {}).get("explicit_path_scope")
    if isinstance(value, str) and value.strip():
        return value
    return None
