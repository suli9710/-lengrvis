from __future__ import annotations

import time
from pathlib import Path

from app.orchestration.background_tasks import BackgroundTask, _watch_process, refresh_background_task


class _ExitedProcess:
    def __init__(self, returncode: int) -> None:
        self._returncode = returncode

    def wait(self, timeout=None):  # noqa: ANN001, ANN202
        return self._returncode

    def poll(self) -> int:
        return self._returncode


def _task(tmp_path: Path, *, status: str = "running") -> BackgroundTask:
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    return BackgroundTask(
        id="bgtask_test",
        command=["python", "--version"],
        cwd=str(tmp_path),
        started_at=time.time() - 10,
        timeout_seconds=1,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        status=status,
        _process=_ExitedProcess(1),
    )


def test_watch_process_does_not_overwrite_timed_out_status(tmp_path: Path):
    task = _task(tmp_path, status="timed_out")
    task.error = "Background task exceeded 1s timeout."

    _watch_process(task)

    assert task.status == "timed_out"
    assert task.error == "Background task exceeded 1s timeout."


def test_refresh_background_task_does_not_overwrite_terminal_status(tmp_path: Path):
    task = _task(tmp_path, status="timed_out")

    refresh_background_task(task)

    assert task.status == "timed_out"
