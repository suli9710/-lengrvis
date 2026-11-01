from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from app.core import process_tree


class FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.killed = False

    def poll(self):  # noqa: ANN201
        return None

    def wait(self, timeout=None):  # noqa: ANN001, ANN201
        return -9

    def kill(self) -> None:
        self.killed = True


def test_windows_kill_process_tree_uses_taskkill(monkeypatch):
    calls: list[list[str]] = []
    process = FakeProcess()

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "run", fake_run)

    process_tree.kill_process_tree(process)  # type: ignore[arg-type]

    assert calls == [["taskkill", "/F", "/T", "/PID", "4242"]]
    assert process.killed is False


def test_posix_kill_process_tree_uses_process_group(monkeypatch):
    calls: list[tuple[int, int]] = []
    process = FakeProcess()

    monkeypatch.setattr(process_tree.os, "name", "posix")
    monkeypatch.setattr(process_tree.os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False)

    process_tree.kill_process_tree(process)  # type: ignore[arg-type]

    assert calls == [(4242, getattr(process_tree.signal, "SIGKILL", process_tree.signal.SIGTERM))]
    assert process.killed is False


def test_process_tree_popen_kwargs_creates_new_windows_process_group(monkeypatch):
    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    kwargs = process_tree.process_tree_popen_kwargs(hide_window=True)

    assert kwargs["creationflags"] & 0x200
    assert kwargs["creationflags"] & 0x08000000


def test_process_tree_popen_kwargs_starts_posix_session(monkeypatch):
    monkeypatch.setattr(process_tree.os, "name", "posix")

    assert process_tree.process_tree_popen_kwargs() == {"start_new_session": True}


def test_run_process_tree_fails_closed_when_isolation_is_required_off_windows(monkeypatch):
    monkeypatch.setattr(process_tree.os, "name", "posix")

    def unexpected_popen(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("Popen must not run before the isolation preflight")

    monkeypatch.setattr(process_tree.subprocess, "Popen", unexpected_popen)

    from app.core.windows_job import WindowsJobIsolationError

    with pytest.raises(WindowsJobIsolationError):
        process_tree.run_process_tree([sys.executable, "-c", "print('must not run')"], require_windows_isolation=True)


def test_run_process_tree_fails_closed_for_windows_limits_off_windows(monkeypatch):
    from app.core.windows_job import WindowsJobIsolationError, WindowsJobLimits

    monkeypatch.setattr(process_tree.os, "name", "posix")

    def unexpected_popen(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("Popen must not run before the isolation preflight")

    monkeypatch.setattr(process_tree.subprocess, "Popen", unexpected_popen)

    with pytest.raises(WindowsJobIsolationError):
        process_tree.run_process_tree(
            [sys.executable, "-c", "print('must not run')"],
            windows_job_limits=WindowsJobLimits(),
        )


def test_run_process_tree_assigns_job_before_resuming_suspended_child(monkeypatch):
    from app.core import windows_job

    events: list[str] = []
    popen_kwargs: dict = {}

    class SuspendedProcess:
        pid = 4242
        _handle = 31337

        def communicate(self, input=None, timeout=None):  # noqa: ANN001, ANN201, A002
            events.append("communicate")
            return ("ok", "")

        def poll(self):  # noqa: ANN201
            return 0

    def fake_popen(_command, **kwargs):  # noqa: ANN001, ANN202
        popen_kwargs.update(kwargs)
        events.append("spawn-suspended")
        return SuspendedProcess()

    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(process_tree.subprocess, "CREATE_SUSPENDED", 0x00000004, raising=False)
    monkeypatch.setattr(process_tree.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        windows_job,
        "attach_process_to_job",
        lambda _process, _limits: events.append("attach-job") or 77,
    )
    monkeypatch.setattr(
        windows_job,
        "resume_suspended_process",
        lambda _process: events.append("resume-child"),
    )
    monkeypatch.setattr(
        windows_job,
        "close_job_handle",
        lambda _handle: events.append("close-job"),
    )

    completed = process_tree.run_process_tree(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True,
        require_windows_isolation=True,
    )

    assert completed.returncode == 0
    assert int(popen_kwargs["creationflags"]) & 0x00000004
    assert events == ["spawn-suspended", "attach-job", "resume-child", "communicate", "close-job"]


def test_run_process_tree_cancel_kills_descendant_processes(tmp_path: Path):
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8'); "
        "time.sleep(30)"
    )
    cancel = threading.Event()

    def request_cancel() -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_path.exists():
            time.sleep(0.01)
        cancel.set()

    canceller = threading.Thread(target=request_cancel, daemon=True)
    canceller.start()
    started = time.monotonic()

    with pytest.raises(process_tree.ProcessCancelledError):
        process_tree.run_process_tree(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=30,
            cancel_event=cancel,
        )

    canceller.join(timeout=1)
    assert time.monotonic() - started < 2
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and psutil.pid_exists(child_pid):
        time.sleep(0.02)
    assert not psutil.pid_exists(child_pid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group regression")
@pytest.mark.parametrize("cancelled", [False, True])
def test_timeout_or_cancel_kills_children_after_leader_exits(tmp_path: Path, cancelled: bool):
    child_pid_path = tmp_path / "orphan.pid"
    script = (
        "import pathlib, subprocess, sys; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8')"
    )
    cancel = threading.Event() if cancelled else None
    timer = threading.Timer(0.5, cancel.set) if cancel is not None else None
    if timer is not None:
        timer.start()
    started = time.monotonic()
    try:
        expected = process_tree.ProcessCancelledError if cancelled else subprocess.TimeoutExpired
        with pytest.raises(expected):
            process_tree.run_process_tree(
                [sys.executable, "-c", script],
                capture_output=True,
                timeout=10 if cancelled else 0.5,
                cancel_event=cancel,
            )
        assert time.monotonic() - started < 3
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 1
        while True:
            try:
                child_status = psutil.Process(child_pid).status()
            except psutil.NoSuchProcess:
                break
            if child_status in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                break
            assert time.monotonic() < deadline, "Descendant is still running after termination"
            time.sleep(0.01)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1)
        if child_pid_path.exists():
            try:
                psutil.Process(int(child_pid_path.read_text(encoding="utf-8"))).kill()
            except psutil.NoSuchProcess:
                # Normal after the assertion confirmed the descendant was terminated.
                pass


def test_kill_process_tree_does_not_signal_an_already_reaped_pid(monkeypatch):
    process = FakeProcess()
    process.returncode = 0

    def unexpected_kill(*_args):
        raise AssertionError("An already reaped PID may have been reused")

    monkeypatch.setattr(process_tree.os, "killpg", unexpected_kill, raising=False)
    process_tree.kill_process_tree(process)


def test_timeout_closes_windows_job_before_draining_output(monkeypatch):
    from app.core import windows_job

    events: list[str] = []

    class TimedOutProcess:
        pid = 4242
        returncode = None

        def communicate(self, input=None, timeout=None):  # noqa: ANN001, ANN201, A002
            events.append("communicate" if len(events) == 0 else "drain")
            if events == ["communicate"]:
                raise subprocess.TimeoutExpired("test", timeout)
            assert timeout == 5.0
            return b"partial output", b""

    process = TimedOutProcess()

    def close_job(_handle):
        events.append("close-job")
        process.returncode = -9

    monkeypatch.setattr(process_tree.os, "name", "nt")
    monkeypatch.setattr(process_tree.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(windows_job, "attach_process_to_job", lambda *_args: 77)
    monkeypatch.setattr(windows_job, "resume_suspended_process", lambda *_args: None)
    monkeypatch.setattr(windows_job, "close_job_handle", close_job)

    with pytest.raises(subprocess.TimeoutExpired) as error:
        process_tree.run_process_tree(["test"], timeout=0.1, capture_output=True, require_windows_isolation=True)
    assert events == ["communicate", "close-job", "drain"]
    assert error.value.output == b"partial output"


def test_timeout_output_drain_is_bounded(monkeypatch):
    from io import BytesIO

    timeouts: list[float] = []

    class TimedOutProcess:
        pid = 4242
        returncode = None
        stdout = BytesIO()
        stderr = BytesIO()

        def communicate(self, input=None, timeout=None):  # noqa: ANN001, ANN201, A002
            timeouts.append(timeout)
            raise subprocess.TimeoutExpired("test", timeout, output=b"partial", stderr=b"error")

    process = TimedOutProcess()
    monkeypatch.setattr(process_tree.os, "name", "posix")
    monkeypatch.setattr(process_tree.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(process_tree, "kill_process_tree", lambda *_args: None)
    with pytest.raises(subprocess.TimeoutExpired) as error:
        process_tree.run_process_tree(["test"], timeout=0.1, capture_output=True)
    assert timeouts == [0.1, 5.0]
    assert error.value.output == b"partial"
    assert error.value.stderr == b"error"
    assert process.stdout.closed and process.stderr.closed
