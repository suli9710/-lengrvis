"""Reverse-execute completed tool results using captured rollback_info.

The orchestrator records rollback_info on every modifying file_tools result;
this module replays those entries in reverse order so the user can undo a
completed task. Some operations (Windows recycle-bin restore) cannot be
performed programmatically and surface as `requires_user_action`.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.core.schemas import Approval, ApprovalStatus, ToolResult, now_iso
from app.llm.registry import get_effective_settings
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.tools.filesystem_safety import (
    ensure_mutation_path_safe,
    path_exists_or_reparse_point,
    safe_copy_file_between_scopes,
    safe_move_file,
)
from app.tools.managed_backups import managed_backup_root, resolve_managed_backup_path
from app.tools.rollback_inventory import (
    RollbackSnapshot,
    RollbackSnapshotEntry,
    load_rollback_snapshot,
    plan_for_snapshot,
    require_snapshot_task,
)
from app.tools.rollback_inventory import rollback_journal_hmac as rollback_journal_hmac
from app.tools.rollback_inventory import (
    rollback_snapshot_hmac as rollback_snapshot_hmac,
)
from app.tools.rollback_verification import (
    absent_success as _absent_success,
)
from app.tools.rollback_verification import (
    backup_identity_error as _backup_identity_error,
)
from app.tools.rollback_verification import (
    managed_backup_identity as _managed_backup_identity,
)
from app.tools.rollback_verification import (
    rollback_failure as _rollback_failure,
)
from app.tools.rollback_verification import (
    safe_rollback_detail as _safe_rollback_detail,
)
from app.tools.rollback_verification import (
    sha256_file as _sha256_file,
)
from app.tools.rollback_verification import (
    verification_failure as _verification_failure,
)
from app.tools.rollback_verification import (
    verify_absent as _verify_absent,
)
from app.tools.rollback_verification import (
    verify_moved_file as _verify_moved_file,
)
from app.tools.rollback_verification import (
    verify_restored_backup_cleanup as _verify_restored_backup_cleanup,
)
from app.tools.rollback_verification import (
    verify_restored_file as _verify_restored_file,
)
from app.tools.tool_abort import ToolAbortedError, raise_if_tool_aborted

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - optional dependency guard
    send2trash = None


_ROLLBACK_FILESYSTEM_ERRORS = (OSError, SecurityError, ValueError, shutil.Error)
_POST_RESOURCE_STATE_KEYS = ("_post_resource_state", "_post_state")


class RollbackLeaseLostError(RuntimeError):
    pass


def rollback_tool_result(result: ToolResult, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    info = dict(result.rollback_info or {})
    context = _context or {}
    allowed = [str(path) for path in context.get("allowed_directories") or []]
    if not info:
        return {
            "ok": True,
            "action": "noop",
            "detail": "Nothing to roll back.",
            "verified": True,
            "verification": {"status": "passed", "method": "no_side_effect"},
        }

    if "move_back" in info:
        spec = info["move_back"]
        return _move_back_then_restore(
            spec,
            info.get("dst_backup"),
            allowed,
            context,
            _post_resource_states(info),
        )

    if "rename_back" in info:
        spec = info["rename_back"]
        return _move_back_then_restore(
            spec,
            info.get("dst_backup"),
            allowed,
            context,
            _post_resource_states(info),
        )

    if "trash_created_file" in info:
        return _trash(info["trash_created_file"], allowed, context, _post_resource_states(info))

    if "delete_folder_if_empty" in info:
        return _delete_if_empty(info["delete_folder_if_empty"], allowed, context, _post_resource_states(info))

    if info.get("backup"):
        return _restore_backup(info["backup"], allowed, context, _post_resource_states(info))

    if "restore_from_recycle_bin" in info:
        target = info["restore_from_recycle_bin"]
        return {
            "ok": False,
            "action": "restore_from_recycle_bin",
            "requires_user_action": True,
            "verified": False,
            "verification": {"status": "manual_required", "method": "user_confirmation"},
            "detail": f"Windows recycle bin cannot be restored programmatically. Please restore '{target}' yourself.",
            "target": target,
        }

    if "permanent_delete_unrecoverable" in info:
        return {
            "ok": False,
            "action": "permanent_delete_unrecoverable",
            "requires_user_action": False,
            "verified": False,
            "verification": {"status": "unrecoverable", "method": "not_applicable"},
            "detail": "Permanent cleanup deletions cannot be rolled back.",
            "targets": info["permanent_delete_unrecoverable"],
        }

    return {
        "ok": False,
        "action": "unknown",
        "verified": False,
        "verification": {"status": "unsupported", "method": "not_applicable"},
        "detail": f"Unhandled rollback_info keys: {list(info)}",
    }


def build_rollback_plan(task_id: str, *, snapshot: RollbackSnapshot | None = None) -> dict[str, Any]:
    frozen = snapshot or load_rollback_snapshot(task_id)
    require_snapshot_task(frozen, task_id)
    return plan_for_snapshot(frozen)


def execute_rollback(
    task_id: str,
    *,
    snapshot: RollbackSnapshot | None = None,
    heartbeat: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    frozen = snapshot or load_rollback_snapshot(task_id)
    require_snapshot_task(frozen, task_id)
    executed: list[dict[str, Any]] = []
    if any(entry.blocker for entry in frozen.entries):
        for entry in frozen.entries:
            _require_rollback_heartbeat(heartbeat)
            outcome = _manual_inventory_blocker(entry) if entry.blocker else _skipped_incomplete_inventory(entry)
            executed.append({"tool_call_id": entry.tool_call_id, **outcome})
            _record_rollback_step(task_id, entry.tool_call_id, outcome)
        _require_rollback_heartbeat(heartbeat)
        summary = _summarize_rollback(executed)
        return {"task_id": task_id, "executed": executed, "count": summary["attempted"], **summary}

    context = _rollback_context()
    if heartbeat is not None:
        context["_rollback_lease_alive"] = heartbeat
        lost_event = getattr(heartbeat, "lost_event", None)
        if lost_event is not None:
            context["_tool_abort_event"] = lost_event
    for entry in frozen.entries:
        _require_rollback_heartbeat(heartbeat)
        if entry.blocker:
            outcome = _manual_inventory_blocker(entry)
        elif entry.result is not None:
            outcome = rollback_tool_result(entry.result, context)
        else:  # pragma: no cover - RollbackSnapshotEntry construction invariant
            outcome = _manual_inventory_blocker(
                RollbackSnapshotEntry(
                    tool_call_id=entry.tool_call_id,
                    effect_at=entry.effect_at,
                    stable_id=entry.stable_id,
                    blocker="invalid_snapshot_entry",
                    detail="Rollback inventory entry is incomplete.",
                )
            )
        executed.append({"tool_call_id": entry.tool_call_id, **outcome})
        _record_rollback_step(task_id, entry.tool_call_id, outcome)
    _require_rollback_heartbeat(heartbeat)
    summary = _summarize_rollback(executed)
    return {"task_id": task_id, "executed": executed, "count": summary["attempted"], **summary}


def _summarize_rollback(executed: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = sum(1 for item in executed if item.get("ok") is True)
    verified = sum(1 for item in executed if (item.get("verification") or {}).get("status") == "passed")
    verification_failed = sum(1 for item in executed if (item.get("verification") or {}).get("status") == "failed")
    manual_required = sum(1 for item in executed if item.get("requires_user_action") is True)
    unrecoverable = sum(1 for item in executed if item.get("action") == "permanent_delete_unrecoverable")
    failed = len(executed) - succeeded - manual_required - unrecoverable

    if unrecoverable:
        state = "unrecoverable"
    elif manual_required:
        state = "manual_required"
    elif failed and succeeded:
        state = "partial"
    elif failed:
        state = "failed"
    else:
        state = "succeeded"

    return {
        "state": state,
        "attempted": len(executed),
        "succeeded": succeeded,
        "verified": verified,
        "verification_failed": verification_failed,
        "failed": failed,
        "manual_required": manual_required,
        "unrecoverable": unrecoverable,
    }


def _rollback_context() -> dict[str, Any]:
    try:
        settings = get_effective_settings()
    except Exception:  # noqa: BLE001 - broad-exception-boundary
        return {"allowed_directories": []}
    return {"allowed_directories": [str(path) for path in settings.allowed_directories or []], "settings": settings}


def rollback_cleanup_result(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    info = dict(args.get("rollback_info") or {})
    context = context or {}
    steps = _cleanup_rollback_steps(info, context)
    if args.get("dry_run", True):
        return {
            "ok": True,
            "dry_run": True,
            "steps": steps,
            "message": "Cleanup rollback preview only. Recycle-bin restores require user action.",
        }
    if not args.get("approved") or not args.get("approval_id"):
        raise SecurityError("cleanup_rollback requires approved=true and approval_id for live execution.")
    approval = _claim_valid_cleanup_rollback_approval(args, context)
    record(
        "file.cleanup_rollback",
        "RollbackTool",
        {"approval_id": approval.id, "steps": steps},
        task_id=context.get("task_id"),
    )
    return {
        "ok": False,
        "dry_run": False,
        "steps": steps,
        "requires_user_action": any(step.get("requires_user_action") for step in steps),
        "rollback_info": {},
        "changed_paths": [],
        "message": "Recycle-bin cleanup items must be restored by the user; permanent deletes are unrecoverable.",
    }


def _claim_valid_cleanup_rollback_approval(args: dict[str, Any], context: dict[str, Any]) -> Approval:
    if args.get("approved") is not True:
        raise SecurityError("cleanup_rollback requires approved=true and a valid approved approval_id.")
    approval_id = str(args.get("approval_id") or "").strip()
    if not approval_id:
        raise SecurityError("cleanup_rollback requires a valid approved approval_id.")

    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise SecurityError("cleanup_rollback requires an approval_id that exists in the approval database.")
    approval = Approval.model_validate(data)
    binding_error = _cleanup_rollback_approval_binding_error(approval, args, context, allow_consumed=False)
    if binding_error:
        raise SecurityError(binding_error)

    claimed = db.claim_approval_for_execution(approval.id, now_iso())
    if not claimed:
        raise SecurityError("cleanup_rollback approval has already been consumed or is no longer approved.")
    claimed_approval = Approval.model_validate(claimed)
    binding_error = _cleanup_rollback_approval_binding_error(claimed_approval, args, context, allow_consumed=True)
    if binding_error:
        raise SecurityError(binding_error)
    return claimed_approval


def _cleanup_rollback_approval_binding_error(
    approval: Approval,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    allow_consumed: bool,
) -> str:
    if approval.approval_type != "tool_call":
        return "cleanup_rollback approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"cleanup_rollback approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "cleanup_rollback approval has already been consumed."
    required = {
        "tool_name": approval.tool_name,
        "args_binding_hmac": approval.args_binding_hmac,
        "preview_hmac": approval.preview_hmac,
        "settings_fingerprint": approval.settings_fingerprint,
        "permission_policy_version": approval.permission_policy_version,
        "tool_version": approval.tool_version,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return f"cleanup_rollback approval lacks binding metadata: {', '.join(missing)}."
    if approval.tool_name != "file.cleanup_rollback":
        return "cleanup_rollback approval tool name does not match file.cleanup_rollback."
    if approval.risk_level and approval.risk_level != RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value:
        return "cleanup_rollback approval risk level does not match cleanup_rollback."
    if approval.tool_version != "1":
        return "cleanup_rollback approval tool version does not match cleanup_rollback."

    expected_args = args_binding_hmac(
        "file.cleanup_rollback",
        args,
        task_id=approval.task_id,
        step_id=approval.step_id,
    )
    if not _hmac_equal(approval.args_binding_hmac, expected_args):
        return "cleanup_rollback approval arguments do not match the current request."

    expected_preview = preview_hmac(approval.diff_preview)
    if not _hmac_equal(approval.preview_hmac, expected_preview):
        return "cleanup_rollback approval preview was modified after review."

    expected_settings = settings_fingerprint(
        context.get("settings"),
        allowed_directories=[str(path) for path in context.get("allowed_directories") or []],
    )
    if not _hmac_equal(approval.settings_fingerprint, expected_settings):
        return "cleanup_rollback runtime settings changed after approval preview."

    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not _hmac_equal(approval.permission_policy_version, expected_policy):
        return "cleanup_rollback permission policy changed after approval preview."
    return ""


def _hmac_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(str(left or ""), str(right or ""))


def _manual_inventory_blocker(entry: RollbackSnapshotEntry) -> dict[str, Any]:
    return {
        "ok": False,
        "action": "manual_review",
        "requires_user_action": True,
        "verified": False,
        "verification": {"status": "manual_required", "method": "rollback_inventory"},
        "reason": entry.blocker,
        "detail": entry.detail,
    }


def _skipped_incomplete_inventory(entry: RollbackSnapshotEntry) -> dict[str, Any]:
    return {
        "ok": False,
        "action": "skipped",
        "requires_user_action": True,
        "verified": False,
        "verification": {"status": "manual_required", "method": "rollback_inventory"},
        "reason": "inventory_incomplete",
        "detail": "Automatic rollback was skipped because another task effect lacks trustworthy evidence.",
    }


def _record_rollback_step(task_id: str, tool_call_id: str, outcome: dict[str, Any]) -> None:
    record(
        "task.rollback_step",
        "RollbackTool",
        {
            "tool_call_id": tool_call_id,
            "ok": outcome.get("ok"),
            "verification_status": (outcome.get("verification") or {}).get("status"),
        },
        task_id=task_id,
    )


def _require_rollback_heartbeat(heartbeat: Callable[[], bool] | None) -> None:
    if heartbeat is not None and not heartbeat():
        raise RollbackLeaseLostError("Rollback claim lease was lost during execution.")


def _require_rollback_context_heartbeat(context: dict[str, Any] | None) -> None:
    heartbeat = (context or {}).get("_rollback_lease_alive")
    _require_rollback_heartbeat(heartbeat if callable(heartbeat) else None)


def _cleanup_rollback_steps(info: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = [str(path) for path in context.get("allowed_directories") or []]
    steps: list[dict[str, Any]] = []
    for target in _as_list(info.get("restore_from_recycle_bin")):
        path = _authorize_cleanup_rollback_target(target, allowed)
        steps.append(
            {
                "action": "restore_from_recycle_bin",
                "path": str(path),
                "requires_user_action": True,
                "detail": "Restore this item from the OS recycle bin.",
            }
        )
    for item in _as_list(info.get("permanent_delete_unrecoverable")):
        path = item.get("path") if isinstance(item, dict) else item
        authorized = _authorize_cleanup_rollback_target(path, allowed)
        steps.append(
            {
                "action": "permanent_delete_unrecoverable",
                "path": str(authorized),
                "requires_user_action": False,
                "detail": "This direct cleanup deletion cannot be recovered by Lengrvis.",
            }
        )
    return steps


def _authorize_cleanup_rollback_target(target: Any, allowed: list[str]) -> Path:
    if isinstance(target, dict):
        target = target.get("path")
    if not target:
        raise SecurityError("Cleanup rollback target is missing.")
    if not allowed:
        raise SecurityError("No authorized directories configured for cleanup rollback.")
    return resolve_authorized(str(target), allowed)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _move_back_then_restore(
    spec: dict[str, Any],
    dst_backup: Any,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
    post_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Undo a move/rename, then (if the destination was overwritten) restore the
    destination's original content from its managed backup."""
    if dst_backup:
        preflight = _preflight_destination_backup(dst_backup, spec.get("from"), allowed, context)
        if preflight is not None:
            return preflight
    result = _move_back(spec.get("from"), spec.get("to"), allowed, context, post_states)
    if not dst_backup or not result.get("ok"):
        return result
    _require_rollback_context_heartbeat(context)
    try:
        _backup_path, original = _resolve_backup_restore_paths(dst_backup, allowed)
        moved_source = _authorize_rollback_path(str(spec.get("from") or ""), allowed)
    except (SecurityError, ValueError) as exc:
        restore = _rollback_precondition_failure("restore_backup", exc)
    else:
        if _path_key(original) != _path_key(moved_source):
            restore = _rollback_precondition_failure(
                "restore_backup",
                "Destination backup does not match the path verified absent by move rollback.",
            )
        else:
            restore = _restore_backup(
                dst_backup,
                allowed,
                context,
                [{"path": str(original), "exists": False}],
            )
    result["dst_restore"] = restore
    if not restore.get("ok"):
        result["ok"] = False
        result["verified"] = False
    return result


def _move_back(
    src: str | None,
    dst: str | None,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
    post_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not src or not dst:
        return _rollback_failure("move_back", "missing src/dst")
    try:
        source = _authorize_rollback_path(src, allowed)
        target = _authorize_rollback_path(dst, allowed)
    except SecurityError as exc:
        return _rollback_failure("move_back", exc)
    if not source.exists():
        return _rollback_failure("move_back", f"source path missing: {source}")
    try:
        precondition = _check_rollback_preconditions(
            ((source, _state_for_path(post_states, source)), (target, _state_for_path(post_states, target))),
            allowed or [],
            context,
            action="move_back",
        )
        if precondition is not None:
            return precondition
        ensure_mutation_path_safe(source, allowed or [], include_self=True, context=context)
        expected_digest = _sha256_file(source, context)
        raise_if_tool_aborted(context)
        safe_move_file(source, target, allowed or [], context)
        _require_rollback_context_heartbeat(context)
        verification = _verify_moved_file(source, target, expected_digest, allowed or [], context)
        if verification["status"] != "passed":
            return _verification_failure("move_back", verification)
        return {
            "ok": True,
            "action": "move_back",
            "from": str(source),
            "to": str(target),
            "verified": True,
            "verification": verification,
        }
    except ToolAbortedError:
        raise
    except _ROLLBACK_FILESYSTEM_ERRORS as exc:
        return _rollback_failure("move_back", exc)


def _trash(
    path_str: str,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
    post_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        path = _authorize_rollback_path(path_str, allowed)
    except SecurityError as exc:
        return _rollback_failure("trash", exc)
    expected = _state_for_path(post_states, path)
    if _unsafe_rollback_state(expected):
        return _rollback_precondition_failure("trash", "Rollback evidence contains a filesystem link.")
    if not path_exists_or_reparse_point(path):
        return _absent_success("trash", path, detail="already absent")
    if send2trash is None:
        return _rollback_failure("trash", "send2trash not installed")
    try:
        precondition = _check_rollback_preconditions(((path, expected),), allowed or [], context, action="trash")
        if precondition is not None:
            return precondition
        raise_if_tool_aborted(context)
        ensure_mutation_path_safe(path, allowed or [], include_self=True, context=context)
        send2trash(str(path))
        _require_rollback_context_heartbeat(context)
        verification = _verify_absent(path)
        if verification["status"] != "passed":
            return _verification_failure("trash", verification)
        return {"ok": True, "action": "trash", "path": str(path), "verified": True, "verification": verification}
    except ToolAbortedError:
        raise
    except RollbackLeaseLostError:
        raise
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        return _rollback_failure("trash", exc)


def _delete_if_empty(
    path_str: str,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
    post_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        path = _authorize_rollback_path(path_str, allowed)
    except SecurityError as exc:
        return _rollback_failure("delete_folder_if_empty", exc)
    expected = _state_for_path(post_states, path)
    if _unsafe_rollback_state(expected):
        return _rollback_precondition_failure(
            "delete_folder_if_empty",
            "Rollback evidence contains a filesystem link.",
        )
    if not path_exists_or_reparse_point(path):
        return _absent_success("delete_folder_if_empty", path, detail="already absent")
    try:
        precondition = _check_rollback_preconditions(
            ((path, expected),), allowed or [], context, action="delete_folder_if_empty"
        )
        if precondition is not None:
            return precondition
        if not path.is_dir():
            return _rollback_failure("delete_folder_if_empty", "not a directory")
        if any(path.iterdir()):
            return _rollback_failure("delete_folder_if_empty", "directory not empty")
        raise_if_tool_aborted(context)
        ensure_mutation_path_safe(path, allowed or [], include_self=True, context=context)
        path.rmdir()
        _require_rollback_context_heartbeat(context)
        verification = _verify_absent(path)
        if verification["status"] != "passed":
            return _verification_failure("delete_folder_if_empty", verification)
        return {
            "ok": True,
            "action": "delete_folder_if_empty",
            "path": str(path),
            "verified": True,
            "verification": verification,
        }
    except ToolAbortedError:
        raise
    except (OSError, SecurityError, ValueError) as exc:
        return _rollback_failure("delete_folder_if_empty", exc)


def _restore_backup(
    backup_spec: Any,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
    post_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    managed_backup = isinstance(backup_spec, dict)
    try:
        backup, original = _resolve_backup_restore_paths(backup_spec, allowed)
    except (SecurityError, ValueError) as exc:
        return _rollback_failure("restore_backup", exc)
    if not backup.exists():
        return _rollback_failure("restore_backup", "backup missing")
    if not backup.is_file():
        return _rollback_failure("restore_backup", "backup is not a file")
    try:
        backup_allowed = [str(managed_backup_root())] if managed_backup else list(allowed or [])
        identity_error = _backup_identity_error(backup_spec, backup, backup_allowed, context)
        if identity_error:
            return _rollback_precondition_failure("restore_backup", identity_error)
        precondition = _check_rollback_preconditions(
            ((original, _state_for_path(post_states, original)),),
            allowed or [],
            context,
            action="restore_backup",
        )
        if precondition is not None:
            return precondition
        ensure_mutation_path_safe(backup, backup_allowed, include_self=True, context=context)
        identity = _managed_backup_identity(backup_spec)
        expected_digest = str(identity["sha256"]) if identity is not None else _sha256_file(backup, context)
        raise_if_tool_aborted(context)
        identity_error = _backup_identity_error(backup_spec, backup, backup_allowed, context)
        if identity_error:
            return _rollback_precondition_failure("restore_backup", identity_error)
        safe_copy_file_between_scopes(backup, original, backup_allowed, allowed or [], context)
        _require_rollback_context_heartbeat(context)
        verification = _verify_restored_file(original, expected_digest, allowed or [], context)
        if verification["status"] != "passed":
            return _verification_failure("restore_backup", verification)
        raise_if_tool_aborted(context)
        _require_rollback_context_heartbeat(context)
        ensure_mutation_path_safe(backup, backup_allowed, include_self=True, context=context)
        backup.unlink()
        verification = _verify_restored_backup_cleanup(backup, original, expected_digest, allowed or [], context)
        if verification["status"] != "passed":
            return _verification_failure("restore_backup", verification)
        return {
            "ok": True,
            "action": "restore_backup",
            "restored": str(original),
            "verified": True,
            "verification": verification,
        }
    except ToolAbortedError:
        raise
    except _ROLLBACK_FILESYSTEM_ERRORS as exc:
        return _rollback_failure("restore_backup", exc)


def _post_resource_states(info: dict[str, Any]) -> list[dict[str, Any]] | None:
    for key in _POST_RESOURCE_STATE_KEYS:
        value = info.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return None


def _state_for_path(
    states: list[dict[str, Any]] | None,
    path: Path,
) -> dict[str, Any] | None:
    if not states:
        return None
    expected_key = _path_key(path)
    for state in states:
        if _path_key(Path(str(state.get("path") or ""))) == expected_key:
            return state
    return None


def _path_key(path: Path) -> str:
    normalized = Path(os.path.abspath(str(path.expanduser())))
    return os.path.normcase(str(normalized))


def _check_rollback_preconditions(
    entries: tuple[tuple[Path, dict[str, Any] | None], ...],
    allowed: list[str],
    context: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any] | None:
    """Compare every target with its post-tool snapshot before mutating it."""

    for path, expected in entries:
        if expected is None:
            return _rollback_precondition_failure(
                action,
                f"Rollback evidence for '{path}' has no post-tool state; manual repair is required.",
            )
        if not isinstance(expected, dict) or "exists" not in expected:
            return _rollback_precondition_failure(action, "Rollback post-tool state is malformed.")
        if _unsafe_rollback_state(expected):
            return _rollback_precondition_failure(action, "Rollback evidence contains a filesystem link.")
        try:
            exists_now = path_exists_or_reparse_point(path)
            expected_exists = bool(expected.get("exists"))
            if not expected_exists:
                if exists_now:
                    return _rollback_precondition_failure(
                        action,
                        f"Rollback target changed after the tool completed: {path}",
                    )
                continue
            if not exists_now:
                return _rollback_precondition_failure(
                    action,
                    f"Rollback target is missing or changed after the tool completed: {path}",
                )
            ensure_mutation_path_safe(path, allowed, include_self=True, context=context)
            stat = path.stat()
            expected_file = bool(expected.get("is_file"))
            expected_dir = bool(expected.get("is_dir"))
            if expected_file != path.is_file() or expected_dir != path.is_dir():
                return _rollback_precondition_failure(action, f"Rollback target type changed: {path}")
            expected_size = expected.get("size")
            if expected_size is not None and int(expected_size) != int(stat.st_size):
                return _rollback_precondition_failure(action, f"Rollback target size changed: {path}")
            expected_inode = int(expected.get("inode") or 0)
            current_inode = int(getattr(stat, "st_ino", 0) or 0)
            # A verified rollback move recreates regular files by copy+delete,
            # so their inode legitimately changes before an earlier action is
            # undone. File identity is instead protected by type, size and
            # SHA-256; directories retain the inode check because they have no
            # content digest and must not be replaced underneath the rollback.
            if expected_dir and expected_inode and current_inode and expected_inode != current_inode:
                return _rollback_precondition_failure(action, f"Rollback target identity changed: {path}")
            expected_digest = str(expected.get("sha256") or "")
            if expected_file and expected_digest and _sha256_file(path, context) != expected_digest:
                return _rollback_precondition_failure(action, f"Rollback target content changed: {path}")
        except _ROLLBACK_FILESYSTEM_ERRORS as exc:
            return _rollback_precondition_failure(action, _safe_rollback_detail(exc))
    return None


def _rollback_precondition_failure(action: str, detail: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "requires_user_action": True,
        "verified": False,
        "verification": {"status": "manual_required", "method": "post_tool_state_compare"},
        "detail": _safe_rollback_detail(detail),
    }


def _unsafe_rollback_state(state: dict[str, Any] | None) -> bool:
    return bool(state and (state.get("is_reparse_point") or state.get("auto_rollback_safe") is False))


def _resolve_backup_restore_paths(backup_spec: Any, allowed: list[str] | None) -> tuple[Path, Path]:
    if isinstance(backup_spec, dict):
        backup_path = backup_spec.get("path")
        original_path = backup_spec.get("original_path")
        if not backup_path or not original_path:
            raise ValueError("Managed backup rollback requires path and original_path.")
        return resolve_managed_backup_path(str(backup_path)), _authorize_rollback_path(str(original_path), allowed)

    if not backup_spec:
        raise ValueError("Backup path is missing.")
    backup = _authorize_rollback_path(str(backup_spec), allowed)
    return backup, backup.with_suffix("")


def _preflight_destination_backup(
    backup_spec: Any,
    expected_original: Any,
    allowed: list[str] | None,
    context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        backup, original = _resolve_backup_restore_paths(backup_spec, allowed)
        expected = _authorize_rollback_path(str(expected_original or ""), allowed)
        if _path_key(original) != _path_key(expected):
            return _rollback_precondition_failure(
                "move_back",
                "Destination backup is bound to a different overwritten path.",
            )
        backup_allowed = [str(managed_backup_root())] if isinstance(backup_spec, dict) else list(allowed or [])
        identity_error = _backup_identity_error(backup_spec, backup, backup_allowed, context)
        if identity_error:
            return _rollback_precondition_failure("move_back", identity_error)
    except (OSError, SecurityError, ValueError) as exc:
        return _rollback_precondition_failure("move_back", exc)
    return None


def _authorize_rollback_path(path_str: str, allowed: list[str] | None = None) -> Path:
    allowed = list(allowed or [])
    if not allowed:
        raise SecurityError("No authorized directories configured for rollback.")
    resolve_authorized(path_str, allowed)
    return Path(os.path.abspath(str(Path(path_str).expanduser())))
