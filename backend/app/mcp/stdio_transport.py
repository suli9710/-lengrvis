"""MCP stdio transport with newline framing and a minimal child environment.

The transport deliberately does not use a shell.  Each high-level MCP client
operation owns one subprocess lifecycle so callers may safely use the client
from different asyncio event loops without retaining loop-bound process state.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.core.process_tree import kill_process_id_tree, process_tree_popen_kwargs

JSONRPC_VERSION = "2.0"
MAX_STDIO_MESSAGE_BYTES = 4 * 1024 * 1024
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_BASE_ENV_KEYS = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
)


class MCPStdioTransportError(RuntimeError):
    """Raised when a local MCP process violates its transport contract."""


class MCPStdioTransport:
    def __init__(
        self,
        *,
        command: str,
        args: Sequence[str] | None,
        env: Mapping[str, str] | None,
        inherit_env: Sequence[str] | None,
        timeout: float,
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.command = str(command or "").strip()
        self.args = tuple(str(item) for item in (args or ()))
        self.env = dict(env or {})
        self.inherit_env = tuple(str(item) for item in (inherit_env or ()))
        self.timeout = float(timeout)
        self.on_notification = on_notification
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: dict[str | int, asyncio.Future[dict[str, Any]]] = {}
        self._protocol_error = ""

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_started()
        request_id = payload.get("id")
        if request_id is None:
            await self._write(payload)
            return {}
        if isinstance(request_id, bool) or not isinstance(request_id, str | int):
            raise MCPStdioTransportError("MCP stdio request id is invalid")
        if request_id in self._pending:
            raise MCPStdioTransportError("MCP stdio request id is already active")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._write(payload)
            if self.timeout > 0:
                return await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def close(self) -> None:
        process = self._process
        reader = self._reader_task
        self._process = None
        self._reader_task = None
        self._fail_pending("MCP stdio transport closed")
        if process is not None:
            stdin = process.stdin
            if stdin is not None and not stdin.is_closing():
                stdin.close()
                try:
                    await stdin.wait_closed()
                except (BrokenPipeError, ConnectionError):
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                kill_process_id_tree(int(process.pid), timeout=2.0)
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        if reader is not None and reader is not asyncio.current_task():
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    async def _ensure_started(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            if not self.command or "\x00" in self.command:
                raise MCPStdioTransportError("MCP stdio transport requires a valid command")
            if any("\x00" in item or len(item) > 32768 for item in self.args):
                raise MCPStdioTransportError("MCP stdio command arguments are invalid")
            child_env = _build_child_environment(self.env, self.inherit_env)
            kwargs = process_tree_popen_kwargs(hide_window=True)
            try:
                self._process = await asyncio.create_subprocess_exec(
                    self.command,
                    *self.args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=child_env,
                    limit=MAX_STDIO_MESSAGE_BYTES + 1,
                    **kwargs,
                )
            except (OSError, ValueError) as exc:
                raise MCPStdioTransportError(f"MCP stdio process could not start: {type(exc).__name__}") from exc
            self._protocol_error = ""
            self._reader_task = asyncio.create_task(self._reader_loop())

    async def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise MCPStdioTransportError(self._protocol_error or "MCP stdio process is not running")
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise MCPStdioTransportError("MCP stdio request is not JSON serializable") from exc
        if len(encoded) > MAX_STDIO_MESSAGE_BYTES:
            raise MCPStdioTransportError("MCP stdio request exceeded the size limit")
        async with self._write_lock:
            try:
                process.stdin.write(encoded + b"\n")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionError, RuntimeError) as exc:
                raise MCPStdioTransportError(self._protocol_error or "MCP stdio process closed its input") from exc

    async def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        error = "MCP stdio process exited before completing active requests"
        try:
            while True:
                try:
                    raw = await process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise MCPStdioTransportError("MCP stdio response exceeded the size limit") from exc
                if not raw:
                    break
                if len(raw) > MAX_STDIO_MESSAGE_BYTES or not raw.endswith(b"\n"):
                    raise MCPStdioTransportError("MCP stdio response framing is invalid")
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise MCPStdioTransportError("MCP stdio server wrote a non-JSON message to stdout") from exc
                if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
                    raise MCPStdioTransportError("MCP stdio server returned an invalid JSON-RPC message")
                await self._dispatch(message)
        except asyncio.CancelledError:
            return
        except MCPStdioTransportError as exc:
            error = str(exc)
            self._protocol_error = error
        finally:
            self._fail_pending(error)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            future = self._pending.get(request_id)
            if future is None or future.done():
                raise MCPStdioTransportError("MCP stdio server returned an unknown response id")
            future.set_result(message)
            return
        method = message.get("method")
        if isinstance(method, str) and method.startswith("notifications/") and request_id is None:
            await self.on_notification(message)
            return
        if isinstance(method, str) and request_id is not None:
            await self._write(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
            return
        raise MCPStdioTransportError("MCP stdio server returned an unsupported message")

    def _fail_pending(self, message: str) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(MCPStdioTransportError(message))


def _build_child_environment(
    configured: Mapping[str, str],
    inherited_names: Sequence[str],
) -> dict[str, str]:
    child: dict[str, str] = {
        key: value for key, value in os.environ.items() if key.upper() in _BASE_ENV_KEYS and "\x00" not in value
    }
    child["PYTHONIOENCODING"] = "utf-8"
    child["PYTHONUTF8"] = "1"
    for raw_name in inherited_names:
        name = _valid_env_name(raw_name)
        if name in os.environ:
            child[name] = os.environ[name]
    for raw_name, raw_value in configured.items():
        name = _valid_env_name(raw_name)
        value = str(raw_value)
        if "\x00" in value or len(value) > 32768:
            raise MCPStdioTransportError(f"MCP stdio environment value for {name!r} is invalid")
        child[name] = value
    return child


def _valid_env_name(value: str) -> str:
    name = str(value or "").strip()
    if not _ENV_NAME_RE.fullmatch(name):
        raise MCPStdioTransportError("MCP stdio environment variable name is invalid")
    return name
