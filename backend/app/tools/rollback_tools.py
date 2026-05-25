"""Reverse-execute completed tool results using captured rollback_info.

The orchestrator records rollback_info on every modifying file_tools result;
this module replays those entries in reverse order so the user can undo a
completed task. Some operations (Windows recycle-bin restore) cannot be
performed programmatically and surface as `requires_user_action`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import ToolResult

try:
    from send2trash import send2trash
except Exception:  # pragma: no cover - optional dependency guard
    send2trash = None


def rollback_tool_result(result: ToolResult, _context: dict[str, Any] | None = None) -> dict[str, Any]:
    info = dict(result.rollback_info or {})
    if not info:
        return {"ok": True, "action": "noop", "detail": "Nothing to roll back."}

    if "move_back" in info:
        spec = info["move_back"]
        return _move_back(spec.get("from"), spec.get("to"))

    if "rename_back" in info:
        spec = info["rename_back"]
        return _move_back(spec.get("from"), spec.get("to"))

    if "trash_created_file" in info:
        return _trash(info["trash_created_file"])

    if "delete_folder_if_empty" in info:
        return _delete_if_empty(info["delete_folder_if_empty"])

    if info.get("backup"):
        return _restore_backup(info["backup"])

    if "restore_from_recycle_bin" in info:
        target = info["restore_from_recycle_bin"]
        return {
            "ok": False,
            "action": "restore_from_recycle_bin",
            "requires_user_action": True,
            "detail": f"Windows recycle bin cannot be restored programmatically. Please restore '{target}' yourself.",
            "target": target,
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
    executed: list[dict[str, Any]] = []
    for result in reversed(results):
        if not result.rollback_info:
            continue
        outcome = rollback_tool_result(result)
        executed.append({"tool_call_id": result.tool_call_id, **outcome})
        record(
            "task.rollback_step",
            "RollbackTool",
            {"tool_call_id": result.tool_call_id, "ok": outcome.get("ok")},
            task_id=task_id,
        )
    return {"task_id": task_id, "executed": executed, "count": len(executed)}


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
                out.append(ToolResult.model_validate(row))
            except Exception:
                continue
    return out


def _move_back(src: str | None, dst: str | None) -> dict[str, Any]:
    if not src or not dst:
        return {"ok": False, "action": "move_back", "detail": "missing src/dst"}
    source = Path(src)
    target = Path(dst)
    if not source.exists():
        return {"ok": False, "action": "move_back", "detail": f"source path missing: {source}"}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        return {"ok": True, "action": "move_back", "from": str(source), "to": str(target)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "move_back", "detail": str(exc)}


def _trash(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {"ok": True, "action": "trash", "detail": "already absent", "path": str(path)}
    if send2trash is None:
        return {"ok": False, "action": "trash", "detail": "send2trash not installed"}
    try:
        send2trash(str(path))
        return {"ok": True, "action": "trash", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "trash", "detail": str(exc)}


def _delete_if_empty(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        return {"ok": True, "action": "delete_folder_if_empty", "detail": "already absent"}
    if not path.is_dir():
        return {"ok": False, "action": "delete_folder_if_empty", "detail": "not a directory"}
    if any(path.iterdir()):
        return {"ok": False, "action": "delete_folder_if_empty", "detail": "directory not empty"}
    try:
        path.rmdir()
        return {"ok": True, "action": "delete_folder_if_empty", "path": str(path)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "delete_folder_if_empty", "detail": str(exc)}


def _restore_backup(backup_path: str) -> dict[str, Any]:
    backup = Path(backup_path)
    if not backup.exists():
        return {"ok": False, "action": "restore_backup", "detail": "backup missing"}
    original = backup.with_suffix("")
    try:
        shutil.copy2(backup, original)
        backup.unlink()
        return {"ok": True, "action": "restore_backup", "restored": str(original)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "restore_backup", "detail": str(exc)}
