from __future__ import annotations

import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.process_tree import kill_process_tree, process_tree_popen_kwargs
from app.core.subprocess_output import decode_process_output


@dataclass(slots=True)
class BackgroundTask:
    id: str
    command: list[str]
    cwd: str
    started_at: float
    timeout_seconds: int
    stdout_path: str
    stderr_path: str
    status: str = "running"
    returncode: int | None = None
    error: str = ""
    completed_at: float | None = None
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    def snapshot(self, *, preview_chars: int = 4000) -> dict[str, Any]:
        refresh_background_task(self)
        stdout_preview, stdout_size = _tail_preview(Path(self.stdout_path), preview_chars)
        stderr_preview, stderr_size = _tail_preview(Path(self.stderr_path), preview_chars)
        with self._lock:
            return {
                "task_id": self.id,
                "status": self.status,
                "command": list(self.command),
                "cwd": self.cwd,
                "returncode": self.returncode,
                "error": self.error,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "stdout_path": self.stdout_path,
                "stderr_path": self.stderr_path,
                "stdout_preview": stdout_preview,
                "stderr_preview": stderr_preview,
                "stdout_bytes": stdout_size,
                "stderr_bytes": stderr_size,
            }


_TASKS: dict[str, BackgroundTask] = {}
_LOCK = threading.RLock()
# Completed/failed tasks beyond this count are evicted (oldest first, along
# with their log files) so the registry doesn't grow for the process lifetime.
_COMPLETED_HISTORY_LIMIT = 200


def _prune_completed_locked() -> None:
    finished = [task for task in _TASKS.values() if task.status != "running"]
    excess = len(finished) - _COMPLETED_HISTORY_LIMIT
    for task in finished[: max(0, excess)]:
        refresh_background_task(task)
        if task.status == "running":
            continue
        _TASKS.pop(task.id, None)
        for log_path in (task.stdout_path, task.stderr_path):
            with suppress(OSError):
                Path(log_path).unlink()


def start_background_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    output_dir: Path,
    timeout_seconds: int,
) -> BackgroundTask:
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = f"bgtask_{uuid4().hex}"
    stdout_path = output_dir / f"{task_id}.stdout.log"
    stderr_path = output_dir / f"{task_id}.stderr.log"
    stdout_file = stdout_path.open("w", encoding="utf-8", errors="replace")
    stderr_file = stderr_path.open("w", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(  # noqa: S603 - dev background tasks receive validated command lists.
            command,
            cwd=str(cwd),
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
            **process_tree_popen_kwargs(),
        )
    finally:
        # Popen has duplicated the file descriptors it needs on supported
        # platforms, so the parent can close its handles immediately.
        stdout_file.close()
        stderr_file.close()

    task = BackgroundTask(
        id=task_id,
        command=list(command),
        cwd=str(cwd),
        started_at=time.time(),
        timeout_seconds=timeout_seconds,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        _process=process,
    )
    with _LOCK:
        _TASKS[task.id] = task
        _prune_completed_locked()
    thread = threading.Thread(target=_watch_process, args=(task,), name=f"lengrvis-{task.id}", daemon=True)
    thread.start()
    return task


def get_background_task(task_id: str) -> BackgroundTask | None:
    with _LOCK:
        task = _TASKS.get(task_id)
    if task is not None:
        refresh_background_task(task)
    return task


def background_task_status(task_id: str) -> dict[str, Any]:
    task = get_background_task(task_id)
    if task is None:
        return {"ok": False, "error": f"Background task not found: {task_id}"}
    return {"ok": task.status in {"running", "succeeded"}, **task.snapshot()}


def refresh_background_task(task: BackgroundTask) -> None:
    process_to_kill: subprocess.Popen | None = None
    with task._lock:
        process = task._process
        if process is None or task.status != "running":
            return
        returncode = process.poll()
        if returncode is None:
            if time.time() - task.started_at <= task.timeout_seconds:
                return
            process_to_kill = _mark_task_timed_out_locked(task)
        else:
            _finish_task_if_running_locked(
                task,
                "succeeded" if returncode == 0 else "failed",
                returncode=returncode,
            )
            return

    if process_to_kill is not None:
        kill_process_tree(process_to_kill)
        with task._lock:
            if task.status == "timed_out":
                task.returncode = process_to_kill.poll()


def _watch_process(task: BackgroundTask) -> None:
    process = task._process
    if process is None:
        return
    try:
        returncode = process.wait(timeout=task.timeout_seconds)
        with task._lock:
            _finish_task_if_running_locked(
                task,
                "succeeded" if returncode == 0 else "failed",
                returncode=returncode,
            )
    except subprocess.TimeoutExpired:
        with task._lock:
            process_to_kill = _mark_task_timed_out_locked(task)
        if process_to_kill is None:
            return
        kill_process_tree(process_to_kill)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None
        with task._lock:
            if process_to_kill is not None and task.status == "timed_out":
                task.returncode = returncode
    except Exception as exc:  # noqa: BLE001
        with task._lock:
            _finish_task_if_running_locked(task, "failed", error=str(exc))


def _finish_task_if_running_locked(
    task: BackgroundTask,
    status: str,
    *,
    returncode: int | None = None,
    error: str = "",
) -> bool:
    if task.status != "running":
        return False
    task.status = status
    task.returncode = returncode
    if error:
        task.error = error
    task.completed_at = time.time()
    return True


def _mark_task_timed_out_locked(task: BackgroundTask) -> subprocess.Popen | None:
    process = task._process
    if process is None:
        raise RuntimeError("Background task has no process.")
    if task.status != "running":
        return None
    _finish_task_if_running_locked(
        task,
        "timed_out",
        returncode=process.poll(),
        error=f"Background task exceeded {task.timeout_seconds}s timeout.",
    )
    return process


def _tail_preview(path: Path, limit: int) -> tuple[str, int]:
    try:
        size = path.stat().st_size
    except OSError:
        return "", 0
    if size <= 0:
        return "", 0
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(max(0, size - limit))
        data = fh.read(limit)
    return decode_process_output(data), size
