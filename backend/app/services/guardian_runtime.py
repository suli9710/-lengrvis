from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from app.config import get_env
from app.core.audit import record
from app.security.desktop_api import (
    DESKTOP_API_TOKEN_HEADER,
    desktop_api_token_headers,
)


GUARDIAN_PORT = int(get_env("LENGRVIS_GUARDIAN_PORT") or get_env("LENGRVIS_BACKEND_PORT") or "8000")
FULL_BACKEND_HOST = "127.0.0.1"
FULL_BACKEND_PORT = int(get_env("LENGRVIS_FULL_BACKEND_PORT") or "8001")
FULL_BACKEND_URL = get_env("LENGRVIS_FULL_BACKEND_URL") or f"http://{FULL_BACKEND_HOST}:{FULL_BACKEND_PORT}"
FULL_BACKEND_IDLE_TIMEOUT_SECONDS = int(get_env("LENGRVIS_FULL_BACKEND_IDLE_TIMEOUT_SECONDS") or "300")
_DISALLOWED_FULL_BACKEND_EXECUTABLES = {
    "bash",
    "bash.exe",
    "cmd",
    "cmd.exe",
    "cscript.exe",
    "powershell.exe",
    "pwsh.exe",
    "sh",
    "wscript.exe",
}


class ForegroundKind(StrEnum):
    WINDOW = "window"
    TRANSIENT = "transient"


@dataclass(slots=True)
class FullBackendProcess:
    process: asyncio.subprocess.Process
    started_at: float


class GuardianRuntime:
    def __init__(self) -> None:
        self.shell_mode = "background"
        self.last_wake_reason = ""
        self._full_backend: FullBackendProcess | None = None
        self._lock = asyncio.Lock()
        self._last_foreground_at = 0.0
        self._foreground_kind = ForegroundKind.TRANSIENT
        self._idle_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._idle_task = asyncio.create_task(self._idle_loop(), name="lengrvis-guardian-idle")

    async def stop(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_task
            self._idle_task = None
        await self.stop_full_backend(reason="guardian_stop")

    async def enter_foreground(self, reason: str = "foreground_requested") -> dict[str, Any]:
        self.shell_mode = "foreground"
        self.last_wake_reason = reason
        self._last_foreground_at = time.monotonic()
        self._foreground_kind = ForegroundKind.WINDOW
        await self.ensure_full_backend(reason=reason)
        await self._notify_full_foreground()
        return await self.status()

    async def wake_transient(self, reason: str = "transient_wake") -> dict[str, Any]:
        self.shell_mode = "foreground"
        self.last_wake_reason = reason
        self._last_foreground_at = time.monotonic()
        self._foreground_kind = ForegroundKind.TRANSIENT
        await self.ensure_full_backend(reason=reason)
        return await self.status()

    async def enter_background(self, reason: str = "background_requested") -> dict[str, Any]:
        self.shell_mode = "background"
        self.last_wake_reason = reason
        self._foreground_kind = ForegroundKind.TRANSIENT
        await self._notify_full_background()
        await self.stop_full_backend(reason=reason)
        return await self.status()

    async def ensure_full_backend(self, *, reason: str = "") -> None:
        async with self._lock:
            if await self._is_full_backend_healthy():
                self.last_wake_reason = reason or self.last_wake_reason
                return
            await self._stop_full_backend_locked()
            command = self._full_backend_command()
            env = {
                **os.environ,
                "LENGRVIS_FULL_BACKEND": "1",
                "LENGRVIS_BACKEND_HOST": FULL_BACKEND_HOST,
                "LENGRVIS_BACKEND_PORT": str(FULL_BACKEND_PORT),
                "LENGRVIS_BACKEND_URL": FULL_BACKEND_URL,
            }
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=os.getcwd(),
            )
            self._full_backend = FullBackendProcess(process=process, started_at=time.monotonic())
            self.last_wake_reason = reason or self.last_wake_reason

        await self._wait_for_full_backend()

    async def stop_full_backend(self, *, reason: str = "") -> None:
        async with self._lock:
            self.last_wake_reason = reason or self.last_wake_reason
            await self._stop_full_backend_locked()

    async def status(self) -> dict[str, Any]:
        healthy = await self._is_full_backend_healthy()
        process = self._full_backend.process if self._full_backend else None
        full_state = "running" if healthy else "starting" if process and process.returncode is None else "stopped"
        return {
            "shellMode": self.shell_mode,
            "guardianState": "running",
            "fullBackendState": full_state,
            "fullBackendUrl": FULL_BACKEND_URL,
            "fullBackendPort": FULL_BACKEND_PORT,
            "lastWakeReason": self.last_wake_reason,
            "foregroundKind": self._foreground_kind.value,
            "idleTimeoutSeconds": FULL_BACKEND_IDLE_TIMEOUT_SECONDS,
        }

    async def proxy(
        self,
        method: str,
        path: str,
        *,
        query: bytes = b"",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> httpx.Response:
        if self.shell_mode != "foreground":
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "full_backend_required",
                        "message": "This endpoint requires the full backend. Open Lengrvis to continue.",
                    }
                },
            )
        await self.ensure_full_backend(reason=f"proxy:{path}")
        url = httpx.URL(FULL_BACKEND_URL).join(path)
        if query:
            url = url.copy_with(query=query)
        filtered_headers = {
            key: value
            for key, value in (headers or {}).items()
            if key.lower() not in {"host", "content-length", "connection", DESKTOP_API_TOKEN_HEADER}
        }
        filtered_headers.update(desktop_api_token_headers())
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.request(method, url, headers=filtered_headers, content=body)

    async def _notify_full_foreground(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{FULL_BACKEND_URL}/api/runtime/foreground", headers=desktop_api_token_headers())
        except Exception:
            return

    async def _notify_full_background(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{FULL_BACKEND_URL}/api/runtime/background", headers=desktop_api_token_headers())
        except Exception:
            return

    def _full_backend_command(self) -> list[str]:
        raw = get_env("LENGRVIS_FULL_BACKEND_COMMAND")
        if raw:
            command = _split_command(raw)
            _validate_custom_full_backend_command(command)
            record(
                "guardian.full_backend_command",
                "GuardianRuntime",
                {"executable": command[0], "argv_len": len(command)},
            )
            return command
        if getattr(sys, "frozen", False):
            return [sys.executable]
        return [sys.executable, "-m", "uvicorn", "backend.main:full_app", "--host", FULL_BACKEND_HOST, "--port", str(FULL_BACKEND_PORT)]

    async def _wait_for_full_backend(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            process = self._full_backend.process if self._full_backend else None
            if process is not None and process.returncode is not None:
                raise RuntimeError(f"Full backend exited during startup with code {process.returncode}.")
            if await self._is_full_backend_healthy():
                return
            await asyncio.sleep(0.25)
        raise TimeoutError("Full backend did not become ready within 30 seconds.")

    async def _is_full_backend_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.get(f"{FULL_BACKEND_URL}/api/runtime/status", headers=desktop_api_token_headers())
            return 200 <= response.status_code < 300
        except Exception:
            return False

    async def _stop_full_backend_locked(self) -> None:
        process = self._full_backend.process if self._full_backend else None
        self._full_backend = None
        if process is None:
            return
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=8)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            if self.shell_mode != "foreground":
                continue
            if self._foreground_kind == ForegroundKind.WINDOW:
                continue
            if time.monotonic() - self._last_foreground_at < FULL_BACKEND_IDLE_TIMEOUT_SECONDS:
                continue
            if await self._full_backend_has_active_runs():
                self._last_foreground_at = time.monotonic()
                continue
            self.shell_mode = "background"
            self._foreground_kind = ForegroundKind.TRANSIENT
            self.last_wake_reason = "idle_timeout"
            await self.stop_full_backend(reason="idle_timeout")

    async def _full_backend_has_active_runs(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                response = await client.get(f"{FULL_BACKEND_URL}/api/runtime/status", headers=desktop_api_token_headers())
            if not 200 <= response.status_code < 300:
                return False
            data = response.json()
        except Exception:
            return False
        active = data.get("activeRunIds") if isinstance(data, dict) else None
        return bool(active)


def _resolve_full_backend_executable(executable: str) -> Path:
    candidate = Path(executable).expanduser()
    if not candidate.is_absolute():
        found = shutil.which(executable)
        if not found:
            raise RuntimeError(f"Full backend executable not found: {executable}")
        candidate = Path(found)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_file():
        raise RuntimeError(f"Full backend executable is not a file: {executable}")
    return resolved


def _full_backend_executable_allowlist() -> set[Path]:
    allowed = {Path(sys.executable).expanduser().resolve(strict=False)}
    raw_allowlist = get_env("LENGRVIS_FULL_BACKEND_COMMAND_ALLOWLIST") or ""
    for entry in raw_allowlist.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        allowed.add(Path(entry).expanduser().resolve(strict=False))
    return allowed


def _validate_custom_full_backend_command(command: list[str]) -> None:
    if not command:
        raise RuntimeError("Full backend command must not be empty.")
    executable = _resolve_full_backend_executable(command[0])
    if executable.name.lower() in _DISALLOWED_FULL_BACKEND_EXECUTABLES:
        raise RuntimeError("Shell interpreters are not allowed for LENGRVIS_FULL_BACKEND_COMMAND.")
    if executable not in _full_backend_executable_allowlist():
        raise RuntimeError("LENGRVIS_FULL_BACKEND_COMMAND executable is not allowed.")


def _split_command(raw: str) -> list[str]:
    if os.name == "nt":
        return _split_windows_command(raw)
    import shlex

    return shlex.split(raw)


def _split_windows_command(raw: str) -> list[str]:
    import ctypes

    ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argc = ctypes.c_int()
    argv = ctypes.windll.shell32.CommandLineToArgvW(raw, ctypes.byref(argc))
    if not argv:
        return [raw]
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


runtime = GuardianRuntime()
