from __future__ import annotations

import os
from pathlib import Path

from app.core.errors import SecurityError


SENSITIVE_PATH_NAMES = {
    ".ssh",
    "ssh",
    "cookies",
    "passwords",
    "credentials",
    "microsoft\\credentials",
    "google\\chrome\\user data",
    "appdata\\local\\google\\chrome\\user data",
}

SYSTEM_ROOTS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
]


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_sensitive_path(path: Path) -> bool:
    lower = str(path).lower()
    return any(name in lower for name in SENSITIVE_PATH_NAMES)


def is_system_path(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    for root in SYSTEM_ROOTS:
        try:
            if resolved == root or resolved.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def resolve_authorized(path: str | Path, allowed_directories: list[str]) -> Path:
    candidate = normalize_path(path)
    if ".." in Path(path).parts:
        raise SecurityError("Path traversal is not allowed.")
    if is_system_path(candidate) or is_sensitive_path(candidate):
        raise SecurityError("Sensitive or system paths are not allowed.")
    if not allowed_directories:
        raise SecurityError("No authorized directories configured.")

    for raw_base in allowed_directories:
        base = normalize_path(raw_base)
        try:
            if candidate == base or candidate.is_relative_to(base):
                if candidate.exists() and os.path.islink(candidate):
                    target = candidate.resolve(strict=True)
                    if not (target == base or target.is_relative_to(base)):
                        raise SecurityError("Symbolic link escapes the authorized directory.")
                return candidate
        except ValueError:
            continue
    raise SecurityError("Path is outside authorized directories.")

