from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from app.core.errors import SecurityError

SENSITIVE_PATH_PARTS = {
    ".ssh",
    "ssh",
    "cookies",
    "passwords",
    "credentials",
}
SENSITIVE_PART_TOKENS = ("credential", "password", "cookies")
SENSITIVE_FILE_NAMES = {
    ".npmrc",
    ".netrc",
    "cert9.db",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "cookies.sqlite",
    "key3.db",
    "key4.db",
    "login data",
    "logins.json",
}
SENSITIVE_SUFFIXES = {".pem"}
SENSITIVE_PATH_FRAGMENTS = (
    ("microsoft", "credentials"),
    ("google", "chrome", "user data"),
    ("microsoft", "edge", "user data"),
    ("mozilla", "firefox", "profiles"),
    (".docker", "config.json"),
    (".kube", "config"),
    (".aws", "sso", "cache"),
)
WINDOWS_SYSTEM_ROOT_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
}

SYSTEM_ROOTS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("C:/ProgramData"),
]


def normalize_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_sensitive_path(path: Path) -> bool:
    parts = _normalized_parts(path)
    name = parts[-1] if parts else ""
    if any(part in SENSITIVE_PATH_PARTS or _has_sensitive_part_token(part) for part in parts):
        return True
    if name in SENSITIVE_FILE_NAMES or name.endswith(tuple(SENSITIVE_SUFFIXES)):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    return any(_contains_fragment(parts, fragment) for fragment in SENSITIVE_PATH_FRAGMENTS)


def is_system_path(path: Path) -> bool:
    if _is_windows_system_path(path):
        return True
    path = Path(path)
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


def _normalized_parts(path: str | Path) -> tuple[str, ...]:
    text = str(path).replace("\\", "/")
    return tuple(part.strip().lower() for part in text.split("/") if part.strip())


def _contains_fragment(parts: tuple[str, ...], fragment: tuple[str, ...]) -> bool:
    if not fragment or len(fragment) > len(parts):
        return False
    fragment = tuple(part.lower() for part in fragment)
    last_start = len(parts) - len(fragment)
    return any(parts[index : index + len(fragment)] == fragment for index in range(last_start + 1))


def _has_sensitive_part_token(part: str) -> bool:
    return any(token in part for token in SENSITIVE_PART_TOKENS)


def _is_windows_system_path(path: str | Path) -> bool:
    return any(_parsed_windows_system_path(candidate) for candidate in _windows_path_candidates(str(path)))


def _windows_path_candidates(text: str) -> tuple[str, ...]:
    candidates = [text]
    for index in range(max(0, len(text) - 2)):
        if text[index].isalpha() and text[index + 1] == ":" and text[index + 2] in "\\/":
            candidates.append(text[index:])
    return tuple(candidates)


def _parsed_windows_system_path(text: str) -> bool:
    parsed = PureWindowsPath(text)
    if not parsed.drive:
        return False
    skipped_roots = {
        parsed.anchor.strip("\\/").lower(),
        parsed.drive.strip("\\/").lower(),
        parsed.drive.rstrip(":").lower(),
    }
    parts = [
        str(part).strip("\\/").lower()
        for part in parsed.parts
        if str(part).strip("\\/") and str(part).strip("\\/").lower() not in skipped_roots
    ]
    return bool(parts and parts[0] in WINDOWS_SYSTEM_ROOT_NAMES)


def path_within_explicit_scope(path: str | Path, scope_text: str) -> bool:
    from app.agents.path_detection import find_explicit_path

    explicit_raw = find_explicit_path(scope_text)
    if not explicit_raw:
        return False
    try:
        explicit = normalize_path(explicit_raw)
        candidate = normalize_path(path)
    except OSError:
        return False
    if candidate == explicit:
        return True
    if not _explicit_scope_allows_children(explicit_raw, explicit):
        return False
    try:
        return candidate.is_relative_to(explicit)
    except ValueError:
        return False


def _explicit_scope_allows_children(explicit_raw: str, explicit: Path) -> bool:
    raw = str(explicit_raw).strip().strip("\"'")
    if raw.endswith(("/", "\\")):
        return True
    try:
        return explicit.exists() and explicit.is_dir()
    except OSError:
        return False


def resolve_standalone_explicit_absolute(path: str | Path) -> Path:
    if _has_windows_alternate_data_stream(path):
        raise SecurityError("Windows alternate data streams are not allowed.")
    candidate = normalize_path(path)
    if ".." in Path(path).parts:
        raise SecurityError("Path traversal is not allowed.")
    if not candidate.is_absolute():
        raise SecurityError("Relative paths require authorized directories.")
    if is_system_path(candidate) or is_sensitive_path(candidate):
        raise SecurityError("Sensitive or system paths are not allowed.")
    return candidate


def resolve_task_path(
    path: str | Path,
    allowed_directories: list[str],
    *,
    explicit_scope_text: str | None = None,
) -> Path:
    if allowed_directories:
        return resolve_authorized(path, allowed_directories)
    if explicit_scope_text and path_within_explicit_scope(path, explicit_scope_text):
        return resolve_standalone_explicit_absolute(path)
    raise SecurityError("No authorized directories configured.")


def resolve_authorized(path: str | Path, allowed_directories: list[str]) -> Path:
    if _has_windows_alternate_data_stream(path):
        raise SecurityError("Windows alternate data streams are not allowed.")
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
                # P1 fix: Check for symlinks even when the candidate doesn't exist
                # yet (e.g. a file about to be created). Walk the path's existing
                # ancestors to detect symlinks that could escape the authorized
                # directory before the final path is materialized.
                _check_no_symlink_escape(candidate, base)
                if candidate.exists() and os.path.islink(candidate):
                    target = candidate.resolve(strict=True)
                    if not (target == base or target.is_relative_to(base)):
                        raise SecurityError("Symbolic link escapes the authorized directory.")
                return candidate
        except ValueError:
            continue
    raise SecurityError("Path is outside authorized directories.")


def _check_no_symlink_escape(candidate: Path, base: Path) -> None:
    """Walk existing ancestor directories of candidate to detect symlinks
    that resolve outside the authorized base."""
    current = candidate.parent
    while current != base and current != current.parent:
        if current.exists() and os.path.islink(current):
            target = current.resolve(strict=True)
            if not (target == base or target.is_relative_to(base)):
                raise SecurityError("Symbolic link in path escapes the authorized directory.")
        current = current.parent


def _has_windows_alternate_data_stream(path: str | Path) -> bool:
    parsed = Path(path)
    parts = parsed.parts
    if not parts:
        return False
    # The first part may be a drive or UNC anchor such as "C:\\". Colons in
    # later components indicate NTFS stream syntax like "safe.txt:stream".
    return any(":" in part for part in parts[1:])
