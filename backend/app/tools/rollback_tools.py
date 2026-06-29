"""Reverse-execute completed tool results using captured rollback_info.

The orchestrator records rollback_info on every modifying file_tools result;
this module replays those entries in reverse order so the user can undo a
completed task. Some operations (Windows recycle-bin restore) cannot be
performed programmatically and surface as `requires_user_action`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

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
    safe_copy_file_between_scopes,
    safe_move_file,
)
from app.tools.managed_backups import managed_backup_root, resolve_managed_backup_path
from app.tools.tool_abort import ToolAbortedError, raise_if_tool_aborted

try:
    from send2trash import send2trash
except Exception:  # noqa: BLE001  # pragma: no cover - optional dependency guard
    send2trash = None


def rollback_tool_result(result: ToolResult, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    info = dict(result.rollback_info or {})
    context = _context or {}
    allowed = [str(path) for path in context.get("allowed_directories") or []]
    if not info:
        return {"ok": True, "action": "noop", "detail": "Nothing to roll back."}

    if "move_back" in info:
        spec = info["move_back"]
        return _move_back(spec.get("from"), spec.get("to"), allowed, context)

    if "rename_back" in info:
        spec = info["rename_back"]
        return _move_back(spec.get("from"), spec.get("to"), allowed, context)

    if "trash_created_file" in info:
        return _trash(info["trash_created_file"], allowed, context)

    if "delete_folder_if_empty" in info:
        return _delete_if_empty(info["delete_folder_if_empty"], allowed, context)

    if info.get("backup"):
        return _restore_backup(info["backup"], allowed, context)

    if "restore_from_recycle_bin" in info:
        target = info["restore_from_recycle_bin"]
        return {
            "ok": False,
            "action": "restore_from_recycle_bin",
            "requires_user_action": True,
            "detail": f"Windows recycle bin cannot be restored programmatically. Please restore '{target}' yourself.",
            "target": target,
        }

    if "permanent_delete_unrecoverable" in info:
        return {
            "ok": False,
            "action": "permanent_delete_unrecoverable",
            "requires_user_action": False,
            "detail": "Permanent cleanup deletions cannot be rolled back.",
            "targets": info["permanent_delete_unrecoverable"],
        }

    return {"ok": False, "action": "unknown", "detail": f"Unhandled rollback_info keys: {list(info)}"}


def build_rollback_plan(task_id: str) -> dict[str, Any]:
    results = _results_for_task(task_id)
    actions: list[dict[str, Any]] = []
    for result in reversed(results):
        info = dict(result.rollback_info or {})
        if not info:
            continue
        actions.append(
            {
                "tool_call_id": result.tool_call_id,
                "actions": list(info.keys()),
                "detail": info,
            }
        )
    return {"task_id": task_id, "steps": actions, "count": len(actions)}


def execute_rollback(task_id: str) -> dict[str, Any]:
    results = _results_for_task(task_id)
    context = _rollback_context()
    executed: list[dict[str, Any]] = []
    for result in reversed(results):
        if not result.rollback_info:
            continue
        outcome = rollback_tool_result(result, context)
        executed.append({"tool_call_id": result.tool_call_id, **outcome})
        record(
            "task.rollback_step",
            "RollbackTool",
            {"tool_call_id": result.tool_call_id, "ok": outcome.get("ok")},
            task_id=task_id,
        )
    return {"task_id": task_id, "executed": executed, "count": len(executed)}


def _rollback_context() -> dict[str, Any]:
    try:
        settings = get_effective_settings()
    except Exception:  # noqa: BLE001
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


def _results_for_task(task_id: str) -> list[ToolResult]:
    rows = db.fetch_many("tool_calls", "task_id = ?", (task_id,), limit=500)
    call_ids = [row["id"] for row in rows]
    if not call_ids:
        return []
    out: list[ToolResult] = []
    for call_id in call_ids:
        results = db.fetch_many("tool_results", "tool_call_id = ?", (call_id,), limit=10)
        for row in results:
            try:
                result = ToolResult.model_validate(row)
            except ValidationError:
                result = None
            if result is not None:
                out.append(result)
    return out


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


def _move_back(
    src: str | None,
    dst: str | None,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not src or not dst:
        return {"ok": False, "action": "move_back", "detail": "missing src/dst"}
    try:
        source = _authorize_rollback_path(src, allowed)
        target = _authorize_rollback_path(dst, allowed)
    except SecurityError as exc:
        return {"ok": False, "action": "move_back", "detail": str(exc)}
    if not source.exists():
        return {"ok": False, "action": "move_back", "detail": f"source path missing: {source}"}
    try:
        raise_if_tool_aborted(context)
        safe_move_file(source, target, allowed or [], context)
        return {"ok": True, "action": "move_back", "from": str(source), "to": str(target)}
    except ToolAbortedError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "move_back", "detail": str(exc)}


def _trash(
    path_str: str,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        path = _authorize_rollback_path(path_str, allowed)
    except SecurityError as exc:
        return {"ok": False, "action": "trash", "detail": str(exc)}
    if not path.exists():
        return {"ok": True, "action": "trash", "detail": "already absent", "path": str(path)}
    if send2trash is None:
        return {"ok": False, "action": "trash", "detail": "send2trash not installed"}
    try:
        raise_if_tool_aborted(context)
        ensure_mutation_path_safe(path, allowed or [], include_self=True, context=context)
        send2trash(str(path))
        return {"ok": True, "action": "trash", "path": str(path)}
    except ToolAbortedError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "trash", "detail": str(exc)}


def _delete_if_empty(
    path_str: str,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        path = _authorize_rollback_path(path_str, allowed)
    except SecurityError as exc:
        return {"ok": False, "action": "delete_folder_if_empty", "detail": str(exc)}
    if not path.exists():
        return {"ok": True, "action": "delete_folder_if_empty", "detail": "already absent"}
    if not path.is_dir():
        return {"ok": False, "action": "delete_folder_if_empty", "detail": "not a directory"}
    if any(path.iterdir()):
        return {"ok": False, "action": "delete_folder_if_empty", "detail": "directory not empty"}
    try:
        raise_if_tool_aborted(context)
        ensure_mutation_path_safe(path, allowed or [], include_self=True, context=context)
        path.rmdir()
        return {"ok": True, "action": "delete_folder_if_empty", "path": str(path)}
    except ToolAbortedError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "delete_folder_if_empty", "detail": str(exc)}


def _restore_backup(
    backup_spec: Any,
    allowed: list[str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    managed_backup = isinstance(backup_spec, dict)
    try:
        backup, original = _resolve_backup_restore_paths(backup_spec, allowed)
    except (SecurityError, ValueError) as exc:
        return {"ok": False, "action": "restore_backup", "detail": str(exc)}
    if not backup.exists():
        return {"ok": False, "action": "restore_backup", "detail": "backup missing"}
    if not backup.is_file():
        return {"ok": False, "action": "restore_backup", "detail": "backup is not a file"}
    try:
        raise_if_tool_aborted(context)
        backup_allowed = [str(managed_backup_root())] if managed_backup else list(allowed or [])
        ensure_mutation_path_safe(backup, backup_allowed, include_self=True, context=context)
        safe_copy_file_between_scopes(backup, original, backup_allowed, allowed or [], context)
        raise_if_tool_aborted(context)
        ensure_mutation_path_safe(original, allowed or [], include_self=True, context=context)
        backup.unlink()
        return {"ok": True, "action": "restore_backup", "restored": str(original)}
    except ToolAbortedError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "restore_backup", "detail": str(exc)}


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


def _authorize_rollback_path(path_str: str, allowed: list[str] | None = None) -> Path:
    allowed = list(allowed or [])
    if not allowed:
        raise SecurityError("No authorized directories configured for rollback.")
    return resolve_authorized(path_str, allowed)
