"""Cross-platform explicit filesystem path detection.

The supervisor heuristic, deterministic planner, MockProvider, and the
task_service explicit-path overrides all need to decide whether a
natural-language request names a concrete filesystem path (for example a
Windows drive path, or a POSIX path like "/home/me/old"). Path detection used
to be Windows-only (a drive-letter regex), so POSIX absolute paths were never
recognized on Linux/macOS, which broke the file.trash flow and explicit-path
authorization on those platforms. These helpers recognize both styles in one
place.
"""

from __future__ import annotations

import re

from app.agents.delegation_rules import WINDOWS_PATH_RE

# Absolute POSIX paths (Linux/macOS). Require at least two path segments so that
# incidental slashes ("and/or") and URL paths are not misread as filesystem
# paths. The leading lookbehind rejects matches preceded by a word char, ":" or
# "/", which excludes URL components such as "https://host/a/b".
POSIX_PATH_RE = re.compile(r'(?<![\w:/])(?P<path>/(?:[^/\s<>|?*"]+/)+[^/\s<>|?*"]+)')


def find_explicit_path(text: str) -> str | None:
    """Return the first explicit absolute filesystem path found in ``text``.

    Recognizes Windows drive paths and POSIX absolute paths (for example
    "/home/me/notes"). Returns ``None`` when no explicit path is present. The
    original casing is preserved so case-sensitive filesystems keep working.
    """
    windows = WINDOWS_PATH_RE.search(text)
    if windows:
        return windows.group(0)
    posix = POSIX_PATH_RE.search(text)
    if posix:
        return posix.group("path")
    return None


def has_explicit_path(text: str) -> bool:
    """True when ``text`` contains an explicit Windows or POSIX absolute path."""
    return find_explicit_path(text) is not None
