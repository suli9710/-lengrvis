from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import threading
import time
from typing import Any


class ProcessCancelledError(RuntimeError):
    """Raised after a cooperative cancel request terminates the process tree."""

    def __init__(self, command: list[str] | str, *, output: Any = None, stderr: Any = None) -> None:
        super().__init__(f"Process was cancelled: {command}")
        self.cmd = command
        self.output = output
        self.stdout = output
        self.stderr = stderr


def process_tree_popen_kwargs(*, hide_window: bool = False) -> dict[str, Any]:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if hide_window:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_process_tree(process: subprocess.Popen[Any], *, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        _kill_windows_tree(process, timeout=timeout)
    else:
        _kill_posix_tree(process)
    try:
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            return


def kill_process_id_tree(pid: int, *, timeout: float = 5.0) -> None:
    if os.name == "nt":
        _kill_windows_pid_tree(int(pid), timeout=timeout)
    else:
        _kill_posix_pid_tree(int(pid))


async def terminate_async_process_tree(
    process: Any,
    *,
    grace_timeout: float = 1.0,
    kill_timeout: float = 5.0,
) -> None:
    if getattr(process, "returncode", None) is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_timeout)
    except TimeoutError:
        await kill_async_process_tree(process, timeout=kill_timeout)


async def kill_async_process_tree(process: Any, *, timeout: float = 5.0) -> None:
    if getattr(process, "returncode", None) is not None:
        return
    kill_process_id_tree(int(process.pid), timeout=timeout)
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except (OSError, TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def run_process_tree(
    command: list[str] | str,
    *,
    timeout: float | None = None,
    input: str | bytes | None = None,  # noqa: A002 - matches subprocess.run.
    capture_output: bool = False,
    check: bool = False,
    hide_window: bool = False,
    cancel_event: threading.Event | None = None,
    cancel_poll_interval: float = 0.05,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr may not be used with capture_output.")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    kwargs.update(process_tree_popen_kwargs(hide_window=hide_window))
    process = subprocess.Popen(command, **kwargs)  # noqa: S603 - callers perform command validation.
    try:
        stdout, stderr = _communicate_cancellable(
            process,
            command,
            input=input,
            timeout=timeout,
            cancel_event=cancel_event,
            poll_interval=cancel_poll_interval,
        )
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            exc.cmd,
            exc.timeout,
            output=stdout,
            stderr=stderr,
        ) from None

    completed = subprocess.CompletedProcess(command, process.poll(), stdout, stderr)
    if check:
        completed.check_returncode()
    return completed


def _communicate_cancellable(
    process: subprocess.Popen[Any],
    command: list[str] | str,
    *,
    input: str | bytes | None,
    timeout: float | None,
    cancel_event: threading.Event | None,
    poll_interval: float,
) -> tuple[Any, Any]:
    if cancel_event is None:
        return process.communicate(input, timeout=timeout)

    interval = max(0.01, float(poll_interval))
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
    communicate_input = input
    while True:
        if cancel_event.is_set():
            kill_process_tree(process)
            stdout, stderr = process.communicate()
            raise ProcessCancelledError(command, output=stdout, stderr=stderr)
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        wait_for = interval if remaining is None else min(interval, remaining)
        try:
            return process.communicate(communicate_input, timeout=wait_for)
        except subprocess.TimeoutExpired:
            communicate_input = None


def _kill_windows_tree(process: subprocess.Popen[Any], *, timeout: float) -> None:
    _kill_windows_pid_tree(int(process.pid), timeout=timeout)


def _kill_windows_pid_tree(pid: int, *, timeout: float) -> None:
    try:
        subprocess.run(  # noqa: S603,S607 - fixed Windows system utility; PID is not shell-expanded.
            ["taskkill", "/F", "/T", "/PID", str(pid)],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _kill_posix_tree(process: subprocess.Popen[Any]) -> None:
    _kill_posix_pid_tree(int(process.pid))


def _kill_posix_pid_tree(pid: int) -> None:
    try:
        os.killpg(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        return
    except OSError:
        return
