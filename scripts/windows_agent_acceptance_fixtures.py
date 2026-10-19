"""Owned native windows for source-checkout acceptance tests."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def native_windows_hosts(
    root: Path, *, discoverable: bool = True, lifetime_seconds: int = 120
):
    if sys.platform != "win32":
        raise RuntimeError("Windows native UIA acceptance requires Windows")
    host_script = (
        Path(__file__).resolve().parents[1]
        / "backend/tests/fixtures/windows_uia_host.ps1"
    )
    processes = []
    hosts = []
    try:
        for role in ("source", "destination"):
            directory = root / role
            directory.mkdir(parents=True, exist_ok=False)
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(host_script),
                "-StateDirectory",
                str(directory),
                "-LifetimeSeconds",
                str(lifetime_seconds),
            ]
            if discoverable:
                command.append("-Discoverable")
            process = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append((process, directory))
            deadline = time.monotonic() + 15
            ready = directory / "ready.json"
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            if not ready.exists():
                raise RuntimeError(
                    f"Native UIA fixture failed to start (exit={process.poll()})"
                )
            host = json.loads(ready.read_text(encoding="utf-8-sig"))
            if host.get("process_id") != process.pid or process.poll() is not None:
                raise RuntimeError(
                    "Native UIA fixture ownership could not be established"
                )
            hosts.append(host)
        yield hosts
    finally:
        for process, directory in processes:
            (directory / "stop").touch()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.communicate(timeout=5)
