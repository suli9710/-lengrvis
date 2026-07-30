from __future__ import annotations

import hmac
import re
from hashlib import sha256
from pathlib import Path

from app.config import env_flag, get_base_settings

APPROVAL_SESSION_GENERATION_FILE = "approval_session_generation.secret"  # noqa: S105 - file name, not a secret.

_GENERATION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SESSION_BINDING_LABEL = "desktop-approval-session-v1"
_GENERATION_BYTES = 43
_MAX_GENERATION_FILE_BYTES = _GENERATION_BYTES + 1


class ApprovalSessionGenerationError(RuntimeError):
    """The desktop approval-session generation is missing, malformed, or stale."""


def approval_session_generation_path() -> Path:
    return Path(get_base_settings().data_dir) / APPROVAL_SESSION_GENERATION_FILE


def current_approval_session_generation() -> str:
    path = approval_session_generation_path()
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_GENERATION_FILE_BYTES + 1)
    except OSError as exc:
        raise ApprovalSessionGenerationError("Desktop approval session generation is unavailable.") from exc
    if len(raw) == _MAX_GENERATION_FILE_BYTES and raw.endswith(b"\n"):
        raw = raw[:-1]
    elif len(raw) != _GENERATION_BYTES:
        raise ApprovalSessionGenerationError("Desktop approval session generation is malformed.")
    try:
        generation = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ApprovalSessionGenerationError("Desktop approval session generation is malformed.") from exc
    if not _GENERATION_PATTERN.fullmatch(generation):
        raise ApprovalSessionGenerationError("Desktop approval session generation is malformed.")
    return generation


def approval_session_generation_fingerprint(generation: str | None = None) -> str:
    value = generation if generation is not None else current_approval_session_generation()
    if not _GENERATION_PATTERN.fullmatch(value):
        raise ApprovalSessionGenerationError("Desktop approval session generation is malformed.")
    return sha256(value.encode("ascii")).hexdigest()


def challenge_approval_session_fingerprint() -> str:
    """Return the current binding, with legacy behavior only in explicit test mode."""
    try:
        return approval_session_generation_fingerprint()
    except ApprovalSessionGenerationError:
        if env_flag("LENGRVIS_TEST"):
            return ""
        raise


def bind_approval_session_generation(payload: str, generation: str) -> str:
    if not payload or not _GENERATION_PATTERN.fullmatch(generation):
        raise ApprovalSessionGenerationError("Desktop approval session signing input is malformed.")
    return "\n".join((payload, _SESSION_BINDING_LABEL, generation))


def session_bound_signing_payload(payload: str, expected_fingerprint: str) -> tuple[str, str]:
    """Bind a stored challenge to the exact current desktop session generation."""
    expected = str(expected_fingerprint or "").strip().lower()
    if not expected:
        if env_flag("LENGRVIS_TEST"):
            return payload, ""
        raise ApprovalSessionGenerationError("Native confirmation challenge is missing its session binding.")
    if not _FINGERPRINT_PATTERN.fullmatch(expected):
        raise ApprovalSessionGenerationError("Native confirmation challenge session binding is malformed.")
    generation = current_approval_session_generation()
    current = approval_session_generation_fingerprint(generation)
    if not hmac.compare_digest(expected, current):
        raise ApprovalSessionGenerationError("Desktop approval session changed after the challenge was created.")
    return bind_approval_session_generation(payload, generation), current


def approval_session_authorization_error(expected_fingerprint: object) -> str:
    """Validate an approved decision against the current session at final claim."""
    expected = str(expected_fingerprint or "").strip().lower()
    if not expected:
        if env_flag("LENGRVIS_TEST"):
            return ""
        return "Desktop approval session binding is missing."
    if not _FINGERPRINT_PATTERN.fullmatch(expected):
        return "Desktop approval session binding is malformed."
    try:
        current = approval_session_generation_fingerprint()
    except ApprovalSessionGenerationError:
        return "Desktop approval session generation is unavailable."
    if not hmac.compare_digest(expected, current):
        return "Desktop approval session has changed."
    return ""
