from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any


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
        stdout, stderr = process.communicate(input, timeout=timeout)
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
