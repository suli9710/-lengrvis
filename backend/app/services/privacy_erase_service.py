from __future__ import annotations

from pathlib import Path

from app.core import db
from app.core.errors import AppError, SecurityError
from app.core.local_data_lifecycle import erasing_local_data, local_data_lock, reserve_local_data_erase
from app.core.privacy_erase_state import public_erase_state, require_erase_reservation, save_erase_state
from app.tools.filesystem_safety import ensure_mutation_path_safe


def _has_active_work() -> bool:
    from app.services import run_service, scheduler_service, task_pool

    pool = task_pool.get_pool().status()
    scheduler = scheduler_service.get_scheduler()
    return bool(
        run_service.active_run_ids()
        or pool["running_count"]
        or pool["queued_count"]
        or any(not task.done() for task in scheduler._executions)
    )


def prepare_local_erase(*, include_settings: bool, confirmation_id: str) -> dict:
    return reserve_local_data_erase(
        include_settings=include_settings, confirmation_id=confirmation_id, has_active_work=_has_active_work
    )


def erase_reserved_content(data_dir: str | Path, *, operation_id: str, token: str) -> dict:
    with local_data_lock:
        state = require_erase_reservation(operation_id, token)
        if state["backend"] == "complete":
            return public_erase_state(state)
        # Save before deletion: a process crash keeps both writers excluded and
        # a retry can safely repeat the idempotent remaining steps.
        state["backend"] = "in_progress"
        save_erase_state(state)
        try:
            with erasing_local_data(has_active_work=_has_active_work, reserved=True):
                if state.get("database") != "complete":
                    counts = db.erase_local_user_data(include_settings=state["include_settings"])
                    state["database"] = "complete"
                    state["deleted"] = {"rows_by_table": counts, "rows_total": sum(counts.values())}
                    save_erase_state(state)
                files = _erase_managed_files(Path(data_dir).expanduser().resolve())
        except (
            Exception
        ) as exc:  # broad-exception-boundary: persist partial deletion before propagating storage failures.
            state["backend"] = "incomplete"
            state["status"] = "incomplete"
            partial_files = getattr(exc, "cleanup_counts", {})
            prior_files = state.get("deleted", {}).get("managed_files", {})
            if partial_files:
                state.setdefault("deleted", {})["managed_files"] = {
                    key: value + prior_files.get(key, 0) for key, value in partial_files.items()
                }
            save_erase_state(state)
            raise
        state["backend"] = "complete"
        prior_files = state["deleted"].get("managed_files", {})
        files = {key: value + prior_files.get(key, 0) for key, value in files.items()}
        state["deleted"].update({"diagnostic_packages": files["diagnostic_packages"], "managed_files": files})
        save_erase_state(state)
        return public_erase_state(state)


def finish_local_erase(*, operation_id: str, token: str, desktop_complete: bool) -> dict:
    with local_data_lock:
        state = require_erase_reservation(operation_id, token)
        if state["status"] == "complete":
            return public_erase_state(state)
        state["desktop"] = "complete" if desktop_complete else "incomplete"
        state["status"] = "complete" if state["backend"] == state["desktop"] == "complete" else "incomplete"
        save_erase_state(state)
        return public_erase_state(state)


def erase_local_content(
    data_dir: str | Path, *, include_settings: bool, reserved: bool = False
) -> tuple[dict[str, int], dict[str, int]]:
    with erasing_local_data(has_active_work=_has_active_work, reserved=reserved):
        counts = db.erase_local_user_data(include_settings=include_settings)
        try:
            files = _erase_managed_files(Path(data_dir).expanduser().resolve())
        except AppError:
            from app.core.audit import record

            record("privacy.local_data_erase_incomplete", "user", {"deleted_rows": sum(counts.values())})
            raise
        return counts, files


def _erase_managed_files(data_dir: Path) -> dict[str, int]:
    counts = {"diagnostic_packages": 0, "tool_results": 0, "file_tool_backups": 0}
    failed: set[str] = set()

    def erase_tree(path: Path, category: str) -> None:
        try:
            ensure_mutation_path_safe(path, [str(data_dir)], include_self=True)
            if not path.exists():
                return
            if path.is_dir():
                for child in path.iterdir():
                    erase_tree(child, category)
                path.rmdir()
            else:
                path.unlink()
                counts[category] += 1
        except (OSError, SecurityError):
            # Report partial cleanup without disclosing user paths. Never
            # traverse a junction/symlink even if it sits in a managed folder.
            failed.add(category)

    erase_tree(data_dir / "diagnostic-packages", "diagnostic_packages")
    erase_tree(data_dir / "file-tool-backups", "file_tool_backups")
    tasks = data_dir / "tasks"
    try:
        ensure_mutation_path_safe(tasks, [str(data_dir)], include_self=True)
        if tasks.is_dir():
            for task in tasks.iterdir():
                ensure_mutation_path_safe(task, [str(data_dir)], include_self=True)
                if task.is_dir():
                    erase_tree(task / "tool-results", "tool_results")
    except (OSError, SecurityError):
        failed.add("tool_results")
    if failed:
        labels = {"diagnostic_packages": "诊断包", "tool_results": "工具结果", "file_tool_backups": "文件备份"}
        error = AppError(
            "local_data_cleanup_incomplete",
            "部分应用生成文件未能删除，请关闭占用程序并检查目录后重试："
            + "、".join(labels[category] for category in sorted(failed)),
            500,
        )
        error.cleanup_counts = counts
        raise error
    return counts
