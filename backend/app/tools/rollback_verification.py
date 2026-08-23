"""Filesystem identity and read-back verification for task rollback."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.policy.redaction import redact_public_text, redact_value
from app.tools.filesystem_safety import ensure_mutation_path_safe, path_exists_or_reparse_point
from app.tools.tool_abort import raise_if_tool_aborted

ROLLBACK_FILESYSTEM_ERRORS = (OSError, SecurityError, ValueError)
POST_RESOURCE_STATE_KEYS = ("_post_resource_state", "_post_state")
MANAGED_BACKUP_IDENTITY_SCHEMA = "managed-backup-identity/v1"


def post_resource_states(info: dict[str, Any]) -> list[dict[str, Any]] | None:
    for key in POST_RESOURCE_STATE_KEYS:
        value = info.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return None


def state_for_path(states: list[dict[str, Any]] | None, path: Path) -> dict[str, Any] | None:
    if not states:
        return None
    expected_key = path_key(path)
    for state in states:
        if path_key(Path(str(state.get("path") or ""))) == expected_key:
            return state
    return None


def path_key(path: Path) -> str:
    normalized = Path(os.path.abspath(str(path.expanduser())))
    return os.path.normcase(str(normalized))


def check_rollback_preconditions(
    entries: tuple[tuple[Path, dict[str, Any] | None], ...],
    allowed: list[str],
    context: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any] | None:
    """Compare every target with its post-tool snapshot before mutating it."""

    for path, expected in entries:
        if expected is None:
            return rollback_precondition_failure(
                action,
                f"Rollback evidence for '{path}' has no post-tool state; manual repair is required.",
            )
        if not isinstance(expected, dict) or "exists" not in expected:
            return rollback_precondition_failure(action, "Rollback post-tool state is malformed.")
        if unsafe_rollback_state(expected):
            return rollback_precondition_failure(action, "Rollback evidence contains a filesystem link.")
        try:
            exists_now = path_exists_or_reparse_point(path)
            expected_exists = bool(expected.get("exists"))
            if not expected_exists:
                if exists_now:
                    return rollback_precondition_failure(
                        action,
                        f"Rollback target changed after the tool completed: {path}",
                    )
                continue
            if not exists_now:
                return rollback_precondition_failure(
                    action,
                    f"Rollback target is missing or changed after the tool completed: {path}",
                )
            ensure_mutation_path_safe(path, allowed, include_self=True, context=context)
            stat = path.stat()
            expected_file = bool(expected.get("is_file"))
            expected_dir = bool(expected.get("is_dir"))
            if expected_file != path.is_file() or expected_dir != path.is_dir():
                return rollback_precondition_failure(action, f"Rollback target type changed: {path}")
            expected_size = expected.get("size")
            if expected_size is not None and int(expected_size) != int(stat.st_size):
                return rollback_precondition_failure(action, f"Rollback target size changed: {path}")
            expected_inode = int(expected.get("inode") or 0)
            current_inode = int(getattr(stat, "st_ino", 0) or 0)
            if expected_dir and expected_inode and current_inode and expected_inode != current_inode:
                return rollback_precondition_failure(action, f"Rollback target identity changed: {path}")
            expected_digest = str(expected.get("sha256") or "")
            if expected_file and expected_digest and sha256_file(path, context) != expected_digest:
                return rollback_precondition_failure(action, f"Rollback target content changed: {path}")
        except ROLLBACK_FILESYSTEM_ERRORS as exc:
            return rollback_precondition_failure(action, safe_rollback_detail(exc))
    return None


def rollback_precondition_failure(action: str, detail: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "requires_user_action": True,
        "verified": False,
        "verification": {"status": "manual_required", "method": "post_tool_state_compare"},
        "detail": safe_rollback_detail(detail),
    }


def unsafe_rollback_state(state: dict[str, Any] | None) -> bool:
    return bool(state and (state.get("is_reparse_point") or state.get("auto_rollback_safe") is False))


def sha256_file(path: Path, context: dict[str, Any] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            raise_if_tool_aborted(context)
            digest.update(chunk)
    return digest.hexdigest()


def verify_moved_file(
    source: Path,
    target: Path,
    expected_digest: str,
    allowed: list[str],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    source_absent = not path_exists_or_reparse_point(source)
    target_is_file = verified_regular_file(target, allowed, context)
    content_match = target_is_file and sha256_file(target, context) == expected_digest
    return {
        "status": "passed" if source_absent and target_is_file and content_match else "failed",
        "method": "filesystem_readback_sha256",
        "checks": {
            "source_absent": source_absent,
            "target_is_file": target_is_file,
            "content_match": content_match,
        },
    }


def verify_absent(path: Path) -> dict[str, Any]:
    absent = not path_exists_or_reparse_point(path)
    return {
        "status": "passed" if absent else "failed",
        "method": "filesystem_readback",
        "checks": {"target_absent": absent},
    }


def verify_restored_file(
    original: Path,
    expected_digest: str,
    allowed: list[str],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    original_is_file = verified_regular_file(original, allowed, context)
    content_match = original_is_file and sha256_file(original, context) == expected_digest
    return {
        "status": "passed" if original_is_file and content_match else "failed",
        "method": "filesystem_readback_sha256",
        "checks": {"original_is_file": original_is_file, "content_match": content_match},
    }


def verify_restored_backup_cleanup(
    backup: Path,
    original: Path,
    expected_digest: str,
    allowed: list[str],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    restored = verify_restored_file(original, expected_digest, allowed, context)
    backup_absent = not path_exists_or_reparse_point(backup)
    checks = {**restored["checks"], "backup_absent": backup_absent}
    return {
        "status": "passed" if restored["status"] == "passed" and backup_absent else "failed",
        "method": "filesystem_readback_sha256",
        "checks": checks,
    }


def verified_regular_file(path: Path, allowed: list[str], context: dict[str, Any] | None) -> bool:
    try:
        ensure_mutation_path_safe(path, allowed, include_self=True, context=context)
    except ROLLBACK_FILESYSTEM_ERRORS:
        return False
    return path.is_file()


def absent_success(action: str, path: Path, *, detail: str) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "detail": detail,
        "path": str(path),
        "verified": True,
        "verification": verify_absent(path),
    }


def verification_failure(action: str, verification: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "verified": False,
        "verification": verification,
        "detail": "Rollback action completed but post-action resource verification failed.",
    }


def rollback_failure(action: str, detail: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "verified": False,
        "verification": {"status": "not_run", "method": "filesystem_readback"},
        "detail": safe_rollback_detail(detail),
    }


def safe_rollback_detail(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))


def managed_backup_identity(backup_spec: Any) -> dict[str, Any] | None:
    if not isinstance(backup_spec, dict) or "identity" not in backup_spec:
        return None
    identity = backup_spec.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"schema", "sha256", "size", "inode"}:
        raise ValueError("Managed backup identity is malformed.")
    digest = identity.get("sha256")
    if identity.get("schema") != MANAGED_BACKUP_IDENTITY_SCHEMA:
        raise ValueError("Managed backup identity schema is unsupported.")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Managed backup identity digest is malformed.")
    if any(type(identity.get(key)) is not int or identity[key] < 0 for key in ("size", "inode")):
        raise ValueError("Managed backup identity numbers are malformed.")
    return identity


def backup_identity_error(
    backup_spec: Any,
    backup: Path,
    backup_allowed: list[str],
    context: dict[str, Any] | None,
) -> str:
    try:
        if isinstance(backup_spec, dict):
            raw_path = Path(str(backup_spec.get("path") or "")).expanduser()
            ensure_mutation_path_safe(raw_path, backup_allowed, include_self=True, context=context)
        identity = managed_backup_identity(backup_spec)
        if not path_exists_or_reparse_point(backup):
            return "Managed backup is missing."
        ensure_mutation_path_safe(backup, backup_allowed, include_self=True, context=context)
        if not backup.is_file():
            return "Managed backup is not a regular file."
        if identity is None:
            return ""
        stat = backup.stat()
        if int(stat.st_size) != identity["size"]:
            return "Managed backup size changed after evidence capture."
        if int(getattr(stat, "st_ino", 0)) != identity["inode"]:
            return "Managed backup file identity changed after evidence capture."
        if sha256_file(backup, context) != identity["sha256"]:
            return "Managed backup content changed after evidence capture."
    except ROLLBACK_FILESYSTEM_ERRORS as exc:
        return safe_rollback_detail(exc)
    return ""
