from __future__ import annotations

import re
import unicodedata
from pathlib import PureWindowsPath
from typing import Any

from app.policy.policy_rules import PATH_ARG_KEYS, SYSTEM_PATH_PREFIXES
from app.policy.risk import RiskLevel

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_DEVICE_PREFIXES = ("//?/", "/?/", "//./", "/./", "/??/")
_PATH_CONTROL_CHARS = frozenset(chr(index) for index in range(32))


def contains_sensitive_arg(value: Any, sensitive_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(term in normalized for term in sensitive_keys):
                return True
            if contains_sensitive_arg(item, sensitive_keys):
                return True
        return False
    if isinstance(value, list | tuple | set):
        return any(contains_sensitive_arg(item, sensitive_keys) for item in value)
    if isinstance(value, str):
        text = value.casefold()
        return any(
            term in text
            for term in {"password", "token", "cookie", "credential", "private key", "payment", "otp", "2fa"}
        )
    return False


def ui_selector_text(args: dict[str, Any]) -> str:
    selector = args.get("selector")
    parts: list[Any] = []
    if isinstance(selector, dict):
        parts.extend(selector.values())
    elif selector:
        parts.append(selector)
    for key in (
        "name",
        "name_contains",
        "nameContains",
        "text_contains",
        "textContains",
        "automation_id",
        "automationId",
        "class_name",
        "className",
        "control_type",
        "controlType",
    ):
        if key in args:
            parts.append(args.get(key))
    return " ".join(str(part) for part in parts if part is not None).casefold()


def contains_system_path(args: dict[str, Any]) -> bool:
    return any(is_system_path(path) for path in candidate_paths(args))


def cleanup_args_touch_system_or_sensitive_path(args: dict[str, Any]) -> bool:
    sensitive_terms = {
        ".ssh",
        "api_key",
        "apikey",
        "cookie",
        "credential",
        "credentials",
        "id_rsa",
        "key",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
    }
    for path in candidate_paths(args):
        normalized = normalized_path(path)
        if is_system_path(path) or any(term in normalized for term in sensitive_terms):
            return True
    return False


def cleanup_has_trash_with_prompt(args: dict[str, Any]) -> bool:
    if str(args.get("action") or "").casefold() == "trash_with_prompt":
        return True
    for item in args.get("items") or args.get("selected_items") or []:
        if isinstance(item, dict) and str(item.get("action") or "").casefold() == "trash_with_prompt":
            return True
    return False


def candidate_paths(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in PATH_ARG_KEYS or "path" in normalized_key:
                result.extend(_path_values(item))
            elif isinstance(item, dict | list | tuple | set):
                # Nested objects may contain their own explicit path fields,
                # but arbitrary strings in unrelated lists (for example a
                # developer tool's ``allowed_tools``) are not paths.  Losing
                # that key context here previously turned values such as
                # ``Bash(pytest:*)`` into malformed path candidates and
                # denied an otherwise valid workspace.
                result.extend(_nested_candidate_paths(item))
        return result
    if isinstance(value, list | tuple | set):
        for item in value:
            result.extend(candidate_paths(item))
        return result
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(text)
    return result


def _path_values(value: Any) -> list[str]:
    """Flatten values owned by an explicitly path-like argument key."""

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_path_values(item))
        return result
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            result.extend(_path_values(item))
        return result
    return []


def _nested_candidate_paths(value: Any) -> list[str]:
    """Find explicit path fields below a non-path container, ignoring scalar siblings."""

    if isinstance(value, dict):
        return candidate_paths(value)
    if isinstance(value, list | tuple | set):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict | list | tuple | set):
                result.extend(_nested_candidate_paths(item))
        return result
    return []


def canonicalize_path(path: str, *, allow_glob: bool = False) -> str | None:
    """Return one stable, lexical path identity without touching the filesystem.

    Policy and risk decisions run before filesystem authorization, so resolving
    through ``Path`` is both platform-dependent and unsafe for paths that do
    not exist yet.  This helper intentionally handles Windows syntax even on
    non-Windows hosts, while retaining the POSIX-looking paths used by the
    existing Linux test fixtures.

    ``None`` means that the input is ambiguous or malformed (for example a
    drive-relative path, a device namespace path, an incomplete UNC root, or a
    ``..`` that would walk above the lexical root).  Callers should fail closed
    rather than comparing the untrusted original string.
    """

    if not isinstance(path, str):
        return None
    text = unicodedata.normalize("NFC", path.strip())
    if not text or any(char in _PATH_CONTROL_CHARS or char == "\x7f" for char in text):
        return None
    text = text.replace("\\", "/")
    if any(text.startswith(prefix) for prefix in _WINDOWS_DEVICE_PREFIXES):
        return None

    # A colon is only valid as the second character of an absolute drive
    # anchor.  Reject ADS/URI/drive-relative forms instead of guessing what
    # the caller intended.
    drive_match = _WINDOWS_DRIVE_RE.match(text)
    if drive_match:
        if len(text) < 3 or text[2] != "/":
            return None
        anchor = f"{text[0].casefold()}:/"
        body = text[3:]
        floor = 0
        kind = "drive"
    elif ":" in text:
        return None
    elif text.startswith("//"):
        # UNC paths must contain both a server and a share.  Repeated leading
        # separators or an empty server/share are intentionally ambiguous.
        if text.startswith("///"):
            return None
        components = text[2:].split("/")
        if len(components) < 2 or not components[0] or not components[1]:
            return None
        server, share = components[0], components[1]
        if any(char in server + share for char in "\x00\r\n"):
            return None
        anchor = f"//{server.casefold()}/{share.casefold()}"
        body = "/".join(components[2:])
        # The server/share pair is the lexical floor; ``..`` may not escape it.
        floor = 2
        kind = "unc"
    elif text.startswith("/"):
        anchor = "/"
        body = text[1:]
        floor = 0
        kind = "posix"
    else:
        anchor = ""
        body = text
        floor = 0
        kind = "relative"

    parts: list[str] = []
    if kind == "unc":
        # Keep the UNC anchor as components so traversal cannot pop the share.
        anchor_parts = anchor[2:].split("/")
        parts.extend(anchor_parts)

    for component in body.split("/"):
        if not component or component == ".":
            continue
        if component == "..":
            if len(parts) <= floor:
                return None
            parts.pop()
            continue
        # NTFS alternate data streams and other colon-bearing components are
        # ambiguous to policy; execution helpers reject them as well.
        if ":" in component:
            return None
        # Win32 trims trailing dots/spaces, so accepting them would make the
        # displayed policy resource differ from the object actually opened.
        if component.endswith((".", " ")):
            return None
        if not allow_glob and any(token in component for token in "*?["):
            return None
        parts.append(unicodedata.normalize("NFC", component).casefold())

    if kind == "drive":
        return anchor + "/".join(parts)
    if kind == "unc":
        return "//" + "/".join(parts)
    if kind == "posix":
        return "/" + "/".join(parts) if parts else "/"
    if not parts:
        return None
    return "/".join(parts)


def canonicalize_paths(value: Any) -> tuple[list[str], list[str]]:
    """Canonicalize all path-like arguments, preserving invalid raw values."""

    valid: list[str] = []
    invalid: list[str] = []
    for raw in candidate_paths(value):
        normalized = canonicalize_path(raw)
        if normalized is None:
            invalid.append(raw)
        else:
            valid.append(normalized)
    return valid, invalid


def is_system_path(path: str) -> bool:
    normalized = normalized_path(path)
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in SYSTEM_PATH_PREFIXES)


def normalized_path(path: str) -> str:
    canonical = canonicalize_path(path)
    if canonical is not None:
        return canonical.rstrip("/")
    # Keep this legacy helper total for non-policy callers.  Security-sensitive
    # policy/risk code uses ``canonicalize_path`` and treats ``None`` as deny.
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return ""
    try:
        pure = PureWindowsPath(text)
        if pure.drive:
            text = pure.as_posix()
    except (TypeError, ValueError):
        return text.rstrip("/").casefold()
    return text.rstrip("/").casefold()


def browser_activity_risk(args: dict[str, Any] | None) -> RiskLevel:
    payload = args or {}
    kind = str(payload.get("kind") or "").strip().casefold().replace("_", "-")
    action = payload.get("action")
    if not kind and isinstance(action, dict):
        kind = str(action.get("kind") or "").strip().casefold().replace("_", "-")
    if kind in {"open", "navigate"}:
        return RiskLevel.R1_OPEN_ONLY
    if kind in {"observe", "screenshot", "wait"}:
        return RiskLevel.R0_READ_ONLY
    if kind in {"click", "fill", "scroll"}:
        return RiskLevel.R2_REVERSIBLE_MODIFY
    if kind in {"submit", "cua", "computer-use"}:
        return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    return RiskLevel.R2_REVERSIBLE_MODIFY
