from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shlex
import shutil
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from app.config import PROJECT_ROOT, AppSettings, get_env
from app.integrations.lengrvis_code_constants import (  # noqa: F401
    ERROR_BAD_NDJSON,
    ERROR_CANCELLED,
    ERROR_LAUNCH_FAILURE,
    ERROR_LENGRVIS_RESULT,
    ERROR_NON_ZERO_EXIT,
    ERROR_PERMISSION_DENIAL,
    LENGRVIS_CODE_ADAPTER_NAME,
    LENGRVIS_CODE_DISPLAY_NAME,
    MAX_ADAPTER_EVENTS,
    TERMINAL_ERROR_TYPES,
)
from app.integrations.lengrvis_code_errors import classify_lengrvis_code_error
from app.integrations.lengrvis_code_events import (  # noqa: F401
    _adapter_events,
    _assistant_text,
    _assistant_tool_names,
    _assistant_tool_uses,
    _base_event_payload,
    _diagnostics,
    _error_reason,
    _event_summary,
    _lengrvis_events,
    _lengrvis_events_for_source_event,
    _record_event,
    _result_output_payload,
    _stderr_diagnostics,
    _summary_message,
    _summary_payload,
    _terminal_lengrvis_event,
    _terminal_status,
    _tool_input_summary,
    _tool_name_from_summary_event,
    _tool_result_message,
    _tool_use_summary_message,
    _user_tool_results,
)
from app.integrations.lengrvis_code_redaction import (  # noqa: F401
    _public_lengrvis_code_json,
    _public_lengrvis_code_result,
    _public_lengrvis_code_text,
    _public_lengrvis_code_value,
    _redacted_command,
    _short_json,
)
from app.orchestration.execution_models import EngineTurnResult, RunObservation, RunPhase, RunState

VENDORED_LENGRVIS_CODE_ROOT = PROJECT_ROOT / "vendor" / "lengrvis-code"
VENDOR_ROOT_ENV = "LENGRVIS_CODE_VENDOR_ROOT"
COMMAND_ENV = "LENGRVIS_CODE_COMMAND"
DEFAULT_PERMISSION_MODE = "default"
DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
    "Bash(npm test:*)",
    "Bash(pnpm test:*)",
)
FORBIDDEN_ALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Bash(*)", "Edit", "Write", "Agent")
WRITE_CAPABLE_ALLOWED_TOOLS: tuple[str, ...] = ("Write", "Edit")
BLOCKED_ENV_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# P1-11 fix: Keys the adapter intentionally injects for the OpenAI-compatible
# provider. These match sensitive-key patterns (e.g. API_KEY) but are required
# for the subprocess to authenticate, so the safety scan must not treat them as
# leaked parent credentials.
ADAPTER_INJECTED_ENV_KEYS: tuple[str, ...] = ("OPENAI_API_KEY",)
FORBIDDEN_CLI_FLAGS: tuple[str, ...] = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")
OPENAI_MODEL_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_DEFAULT_SONNET_MODEL",
    "OPENAI_DEFAULT_OPUS_MODEL",
    "OPENAI_DEFAULT_HAIKU_MODEL",
    "OPENAI_SMALL_FAST_MODEL",
)
# P1-11 fix: Whitelist of environment variable keys allowed in the subprocess env.
# Only Lengrvis-specific and standard system vars are passed to the child process.
# This prevents leaking sensitive environment variables (e.g. cloud credentials,
# database passwords) from the parent process into the Lengrvis Code subprocess.
_ALLOWED_ENV_PREFIXES: tuple[str, ...] = (
    "LENGRVIS_",
    "OPENAI_",
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "COMPUTERNAME",
    "USERNAME",
)
_SENSITIVE_ENV_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"PASSWORD", re.IGNORECASE),
    re.compile(r"SECRET", re.IGNORECASE),
    re.compile(r"TOKEN", re.IGNORECASE),
    re.compile(r"CREDENTIAL", re.IGNORECASE),
    re.compile(r"API_KEY", re.IGNORECASE),
    re.compile(r"PRIVATE_KEY", re.IGNORECASE),
)

@dataclass(slots=True)
class LengrvisCodeRuntime:
    source_root: Path = VENDORED_LENGRVIS_CODE_ROOT
    command: tuple[str, ...] = ()
    reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.command)


@dataclass(slots=True)
class LengrvisCodeRuntimeHealth:
    ok: bool
    available: bool
    configured_command: bool
    source_root: str
    command: list[str] = field(default_factory=list)
    reason: str = ""
    diagnostic: str = ""
    node_dist_present: bool = False
    bun_dist_present: bool = False
    node_available: bool = False
    bun_available: bool = False
    build_required: bool = False
    build_hint: str = ""
    safety_error: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "available": self.available,
            "configured_command": self.configured_command,
            "source_root": self.source_root,
            "command": self.command,
            "reason": self.reason,
            "diagnostic": self.diagnostic,
            "node_dist_present": self.node_dist_present,
            "bun_dist_present": self.bun_dist_present,
            "node_available": self.node_available,
            "bun_available": self.bun_available,
            "build_required": self.build_required,
            "build_hint": self.build_hint,
            "safety_error": self.safety_error,
        }


@dataclass(slots=True)
class LengrvisCodeConfig:
    command: tuple[str, ...] = ()
    executable: str = ""
    executable_args: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    max_turns: int = 1
    permission_mode: str = DEFAULT_PERMISSION_MODE
    extra_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LengrvisCodeStreamSummary:
    events: list[dict[str, Any]] = field(default_factory=list)
    assistant_text: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    system_events: list[dict[str, Any]] = field(default_factory=list)
    invalid_lines: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    stderr: str = ""
    returncode: int | None = None
    cancelled: bool = False
    command: list[str] = field(default_factory=list)
    launch_error: str = ""
    runtime_health: dict[str, Any] = field(default_factory=dict)

    @property
    def final_text(self) -> str:
        if self.result and isinstance(self.result.get("result"), str):
            return str(self.result["result"]).strip()
        return "\n".join(text for text in self.assistant_text if text).strip()

    @property
    def is_error(self) -> bool:
        classification = classify_lengrvis_code_error(self)
        return classification is not None and classification != ERROR_CANCELLED

    @property
    def usage(self) -> dict[str, Any]:
        if self.result and isinstance(self.result.get("usage"), Mapping):
            return dict(self.result["usage"])
        return {}

    @property
    def permission_denials(self) -> list[Any]:
        if self.result and isinstance(self.result.get("permission_denials"), list):
            return list(self.result["permission_denials"])
        return []

    @property
    def error_classification(self) -> str | None:
        return classify_lengrvis_code_error(self)


class LengrvisCodeProcessRegistry:
    """Tracks Lengrvis Code subprocesses by Lengrvis run_id for cancellation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._processes: dict[str, tuple[asyncio.subprocess.Process, asyncio.AbstractEventLoop]] = {}
        self._cancel_requested: set[str] = set()

    def register(self, run_id: str, process: asyncio.subprocess.Process) -> None:
        if not run_id:
            return
        with self._lock:
            self._processes[run_id] = (process, asyncio.get_running_loop())

    def unregister(self, run_id: str, process: asyncio.subprocess.Process | None = None) -> None:
        if not run_id:
            return
        with self._lock:
            entry = self._processes.get(run_id)
            if process is not None and (entry is None or entry[0] is not process):
                return
            self._processes.pop(run_id, None)

    def get(self, run_id: str) -> asyncio.subprocess.Process | None:
        with self._lock:
            entry = self._processes.get(run_id)
            return entry[0] if entry is not None else None

    async def cancel(self, run_id: str, *, timeout_seconds: float = 1.0) -> bool:
        with self._lock:
            entry = self._processes.get(run_id)
            self._cancel_requested.add(run_id)
        if entry is None:
            return False
        process, loop = entry
        if process.returncode is not None:
            self.unregister(run_id, process)
            return False
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            return await self._cancel_on_owner_loop(run_id, process, timeout_seconds=timeout_seconds)
        coro = self._cancel_on_owner_loop(run_id, process, timeout_seconds=timeout_seconds)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            return self._terminate_without_owner_loop(run_id, process)
        try:
            return await asyncio.wait_for(
                asyncio.wrap_future(future),
                timeout=max(timeout_seconds + 0.5, timeout_seconds),
            )
        except (TimeoutError, RuntimeError, concurrent.futures.CancelledError):
            future.cancel()
            return self._terminate_without_owner_loop(run_id, process)

    async def _cancel_on_owner_loop(
        self,
        run_id: str,
        process: asyncio.subprocess.Process,
        *,
        timeout_seconds: float,
    ) -> bool:
        try:
            process.terminate()
        except ProcessLookupError:
            self.unregister(run_id, process)
            return False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        finally:
            self.unregister(run_id, process)
        return True

    def _terminate_without_owner_loop(self, run_id: str, process: asyncio.subprocess.Process) -> bool:
        try:
            process.terminate()
        except ProcessLookupError:
            self.unregister(run_id, process)
            return False
        self.unregister(run_id, process)
        return True

    def active_run_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._processes)

    def consume_cancel_requested(self, run_id: str) -> bool:
        if not run_id:
            return False
        with self._lock:
            if run_id not in self._cancel_requested:
                return False
            self._cancel_requested.remove(run_id)
            return True


lengrvis_code_process_registry = LengrvisCodeProcessRegistry()


def resolve_lengrvis_code_runtime(
    command: Sequence[str] | None = None,
    *,
    source_root: str | Path | None = None,
) -> LengrvisCodeRuntime:
    """Resolve a stable command without coupling callers to vendored internals."""

    health = diagnose_lengrvis_code_runtime(command, source_root=source_root)
    if health.command:
        return LengrvisCodeRuntime(
            source_root=Path(health.source_root).expanduser().resolve(strict=False),
            command=tuple(health.command),
            reason=health.reason,
        )
    return LengrvisCodeRuntime(
        source_root=Path(health.source_root).expanduser().resolve(strict=False),
        reason=health.diagnostic or health.reason,
    )


def diagnose_lengrvis_code_runtime(
    command: Sequence[str] | None = None,
    *,
    source_root: str | Path | None = None,
) -> LengrvisCodeRuntimeHealth:
    """Return a deterministic health check for the supported CLI/headless runtime."""

    explicit = tuple(str(part) for part in (command or ())) or lengrvis_code_command_from_env()
    root = _vendor_source_root(source_root)
    node_cli = root / "dist" / "cli-node.js"
    bun_cli = root / "dist" / "cli-bun.js"
    node = shutil.which("node")
    bun = shutil.which("bun")
    base = {
        "configured_command": bool(explicit),
        "source_root": str(root),
        "node_dist_present": node_cli.exists(),
        "bun_dist_present": bun_cli.exists(),
        "node_available": bool(node),
        "bun_available": bool(bun),
        "build_hint": _build_hint(root),
    }

    if explicit:
        resolved = _resolve_command(explicit)
        safety_error = _command_safety_error(resolved)
        diagnostic = f"{LENGRVIS_CODE_DISPLAY_NAME} runtime command configured explicitly."
        if safety_error:
            diagnostic = f"Configured {LENGRVIS_CODE_DISPLAY_NAME} command is unsafe: {safety_error}"
        return LengrvisCodeRuntimeHealth(
            ok=not safety_error,
            available=bool(resolved) and not safety_error,
            command=list(resolved) if not safety_error else [],
            reason="explicit command",
            diagnostic=diagnostic,
            safety_error=safety_error,
            **base,
        )

    if node_cli.exists():
        if node:
            return LengrvisCodeRuntimeHealth(
                ok=True,
                available=True,
                command=[node, str(node_cli)],
                reason="vendored dist/cli-node.js",
                diagnostic=f"{LENGRVIS_CODE_DISPLAY_NAME} vendored Node CLI is ready.",
                **base,
            )
        return LengrvisCodeRuntimeHealth(
            ok=False,
            available=False,
            reason="node runtime missing",
            diagnostic=(
                f"{LENGRVIS_CODE_DISPLAY_NAME} vendored dist exists at {node_cli}, but node was not found. "
                f"Install Node.js or set {COMMAND_ENV} explicitly."
            ),
            **base,
        )

    if bun_cli.exists():
        if bun:
            return LengrvisCodeRuntimeHealth(
                ok=True,
                available=True,
                command=[bun, str(bun_cli)],
                reason="vendored dist/cli-bun.js",
                diagnostic=f"{LENGRVIS_CODE_DISPLAY_NAME} vendored Bun CLI is ready.",
                **base,
            )
        return LengrvisCodeRuntimeHealth(
            ok=False,
            available=False,
            reason="bun runtime missing",
            diagnostic=(
                f"{LENGRVIS_CODE_DISPLAY_NAME} vendored Bun CLI exists at {bun_cli}, but bun was not found. "
                f"Install Bun, build dist/cli-node.js for Node, or set {COMMAND_ENV} explicitly."
            ),
            **base,
        )

    return LengrvisCodeRuntimeHealth(
        ok=False,
        available=False,
        reason="vendored dist missing",
        diagnostic=(
            f"{LENGRVIS_CODE_DISPLAY_NAME} CLI is unavailable at {root}. "
            "Vendored dist/cli-node.js or dist/cli-bun.js is missing. "
            f"Build the vendored snapshot or set {COMMAND_ENV} explicitly."
        ),
        build_required=True,
        **base,
    )


def lengrvis_code_command_from_env() -> tuple[str, ...]:
    raw = str(get_env(COMMAND_ENV) or "").strip()
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw, posix=True))
    except ValueError:
        return (raw,)


def _vendor_source_root(source_root: str | Path | None = None) -> Path:
    raw = source_root or get_env(VENDOR_ROOT_ENV) or VENDORED_LENGRVIS_CODE_ROOT
    return Path(raw).expanduser().resolve(strict=False)


def _build_hint(root: Path) -> str:
    return (
        f"Build {LENGRVIS_CODE_DISPLAY_NAME} under {root} using the repository's vendored build workflow; "
        f"tests should set {COMMAND_ENV} to a fake CLI instead of running the build."
    )


def _is_sensitive_env_key(key: str) -> bool:
    """P1-11 fix: Check if an environment variable key looks sensitive."""
    upper = key.upper()
    for pattern in _SENSITIVE_ENV_KEY_PATTERNS:
        if pattern.search(upper):
            return True
    return False


def _sanitize_subprocess_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """P1-11 fix: Build a sanitized env dict with only whitelisted keys.

    Only keys matching _ALLOWED_ENV_PREFIXES are passed through, and
    sensitive-key patterns (PASSWORD, SECRET, TOKEN, etc.) are always
    stripped even if they match an allowed prefix. Explicitly set
    BLOCKED_ENV_KEYS (Anthropic credentials) are always removed.
    """
    raw_env = dict(os.environ if base_env is None else base_env)
    sanitized: dict[str, str] = {}
    for key, value in raw_env.items():
        # Always strip blocked keys.
        if key in BLOCKED_ENV_KEYS:
            continue
        # Always strip sensitive-looking keys.
        if _is_sensitive_env_key(key):
            continue
        # Only allow whitelisted prefixes.
        upper_key = key.upper()
        if any(upper_key.startswith(prefix) for prefix in _ALLOWED_ENV_PREFIXES):
            sanitized[key] = value
    return sanitized


def build_lengrvis_code_env(
    settings: AppSettings,
    *,
    base_env: Mapping[str, str] | None = None,
    passthrough_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Map Lengrvis OpenAI-compatible settings into Lengrvis Code's OpenAI provider env."""

    # P1-11 fix: Start from a sanitized env instead of blindly passing all parent env vars.
    env = _sanitize_subprocess_env(base_env)
    # P1-11 follow-up fix: Caller-provided config env (LengrvisCodeConfig.env) is
    # explicit, trusted intent (e.g. adapter-specific vars, test record paths) and
    # must bypass the parent-env prefix allowlist that _sanitize_subprocess_env
    # applies. Blocked credentials and sensitive-looking keys are still stripped so a
    # misconfigured caller cannot leak secrets, and the adapter-injected OpenAI keys
    # below always take precedence.
    if passthrough_env:
        for passthrough_key, passthrough_value in passthrough_env.items():
            if passthrough_key in BLOCKED_ENV_KEYS:
                continue
            if _is_sensitive_env_key(passthrough_key):
                continue
            env[passthrough_key] = passthrough_value
    for key in BLOCKED_ENV_KEYS:
        env.pop(key, None)
    model = str(settings.model or "").strip()
    env.update(
        {
            "LENGRVIS_CODE_USE_OPENAI": "1",
            "OPENAI_API_KEY": str(settings.api_key or ""),
            "OPENAI_BASE_URL": str(settings.base_url or ""),
            "OPENAI_MODEL": model,
            "OPENAI_DEFAULT_SONNET_MODEL": model,
            "OPENAI_DEFAULT_OPUS_MODEL": model,
            "OPENAI_DEFAULT_HAIKU_MODEL": model,
            "OPENAI_SMALL_FAST_MODEL": model,
        }
    )
    return env


def default_allowed_tools(*, writes_enabled: bool = False) -> tuple[str, ...]:
    return allowed_tools_for_developer(writes_enabled=writes_enabled)


def allowed_tools_for_developer(*, writes_enabled: bool = False) -> tuple[str, ...]:
    tools = DEFAULT_ALLOWED_TOOLS
    if writes_enabled:
        tools = DEFAULT_ALLOWED_TOOLS + WRITE_CAPABLE_ALLOWED_TOOLS
    return validate_allowed_tools(tools, allow_write_tools=writes_enabled)


def validate_allowed_tools(allowed_tools: Sequence[str], *, allow_write_tools: bool = False) -> tuple[str, ...]:
    normalized = tuple(str(tool).strip() for tool in allowed_tools if str(tool).strip())
    for tool in normalized:
        if _is_forbidden_allowed_tool(tool, allow_write_tools=allow_write_tools):
            raise ValueError(f"Unsafe {LENGRVIS_CODE_DISPLAY_NAME} allowedTools entry is not permitted: {tool}")
    return normalized


def _is_forbidden_allowed_tool(tool: str, *, allow_write_tools: bool) -> bool:
    tool_name = tool.split("(", 1)[0]
    if tool_name == "Agent" or tool in {"Bash", "Bash(*)"}:
        return True
    if tool_name in WRITE_CAPABLE_ALLOWED_TOOLS:
        return not allow_write_tools
    if tool.startswith("Bash("):
        return not _is_allowed_bash_tool(tool)
    return False


def build_lengrvis_code_command(
    prompt: str,
    *,
    cwd: str | Path,
    settings: AppSettings | None = None,
    config: LengrvisCodeConfig | None = None,
) -> list[str]:
    active = config or LengrvisCodeConfig()
    workspace = Path(cwd).expanduser().resolve(strict=False)
    extra_args = tuple(str(arg) for arg in active.extra_args)
    _assert_no_forbidden_flags(extra_args)
    runtime = resolve_lengrvis_code_runtime(_configured_command(active))
    if not runtime.command:
        raise RuntimeError(runtime.reason)

    model = str((settings.model if settings is not None else "") or "").strip()
    allow_write_tools = any(str(tool).split("(", 1)[0] in WRITE_CAPABLE_ALLOWED_TOOLS for tool in active.allowed_tools)
    allowed_tools = validate_allowed_tools(active.allowed_tools, allow_write_tools=allow_write_tools)

    command = [
        *runtime.command,
        *extra_args,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--bare",
        "--model",
        model,
        "--max-turns",
        str(max(1, int(active.max_turns))),
        "--add-dir",
        str(workspace),
        "--permission-mode",
        active.permission_mode,
        "--allowedTools",
        ",".join(allowed_tools),
        prompt,
    ]
    assert_safe_lengrvis_code_invocation(command, build_env=active.env)
    return command


def assert_safe_lengrvis_code_invocation(command: Sequence[str], *, build_env: Mapping[str, Any]) -> None:
    error = _command_safety_error(command, build_env=build_env)
    if error:
        raise ValueError(error)


def parse_lengrvis_code_ndjson_lines(lines: Iterable[str]) -> LengrvisCodeStreamSummary:
    summary = LengrvisCodeStreamSummary()
    for raw_line in lines:
        event = _parse_ndjson_line(raw_line)
        if event is None:
            if raw_line.strip():
                summary.invalid_lines.append(raw_line.rstrip("\r\n"))
            continue
        _record_event(summary, event)
    return summary


async def iter_lengrvis_code_ndjson(stream: asyncio.StreamReader) -> AsyncIterator[dict[str, Any]]:
    while True:
        line = await stream.readline()
        if not line:
            break
        event = _parse_ndjson_line(line.decode("utf-8", errors="replace"))
        if event is not None:
            yield event


async def run_lengrvis_code(
    prompt: str,
    *,
    cwd: str | Path,
    settings: AppSettings,
    config: LengrvisCodeConfig | None = None,
    run_id: str = "",
    cancel_event: asyncio.Event | None = None,
    registry: LengrvisCodeProcessRegistry = lengrvis_code_process_registry,
) -> LengrvisCodeStreamSummary:
    active = config or LengrvisCodeConfig(max_turns=settings.agent_loop_max_turns)
    # P1-11 fix: build_lengrvis_code_env starts from a sanitized parent-env
    # whitelist; caller-provided config env is passed through separately so it is
    # not dropped by the prefix allowlist (P1-11 follow-up fix).
    env = build_lengrvis_code_env(settings, base_env=os.environ, passthrough_env=active.env)
    launch_config = LengrvisCodeConfig(
        command=active.command,
        executable=active.executable,
        executable_args=active.executable_args,
        allowed_tools=active.allowed_tools,
        max_turns=active.max_turns,
        permission_mode=active.permission_mode,
        extra_args=active.extra_args,
        env=env,
    )
    runtime_health = diagnose_lengrvis_code_runtime(_configured_command(launch_config))
    try:
        command = build_lengrvis_code_command(prompt, cwd=cwd, settings=settings, config=launch_config)
        assert_safe_lengrvis_code_invocation(command, build_env=env)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        return LengrvisCodeStreamSummary(
            launch_error=str(exc),
            runtime_health=runtime_health.as_payload(),
        )

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(Path(cwd).expanduser().resolve(strict=False)),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        return LengrvisCodeStreamSummary(
            command=_redacted_command(command),
            launch_error=str(exc),
            runtime_health=runtime_health.as_payload(),
        )
    registry.register(run_id, process)
    summary = LengrvisCodeStreamSummary(command=_redacted_command(command), runtime_health=runtime_health.as_payload())

    async def read_stdout() -> None:
        if process.stdout is None:
            return
        while True:
            line = await process.stdout.readline()
            if not line:
                return
            raw_line = line.decode("utf-8", errors="replace")
            event = _parse_ndjson_line(raw_line)
            if event is None:
                if raw_line.strip():
                    summary.invalid_lines.append(raw_line.rstrip("\r\n"))
                continue
            _record_event(summary, event)

    async def read_stderr() -> str:
        if process.stderr is None:
            return ""
        data = await process.stderr.read()
        return data.decode("utf-8", errors="replace")

    stdout_task = asyncio.create_task(read_stdout())
    stderr_task = asyncio.create_task(read_stderr())
    wait_task = asyncio.create_task(process.wait())
    cancel_task: asyncio.Task[bool] | None = None
    if cancel_event is not None:
        cancel_task = asyncio.create_task(cancel_event.wait())

    try:
        pending: set[asyncio.Task[Any]] = {wait_task}
        if cancel_task is not None:
            pending.add(cancel_task)
        done, _pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task is not None and cancel_task in done and cancel_event and cancel_event.is_set():
            summary.cancelled = True
            terminated = await registry.cancel(run_id)
            if not terminated:
                await _terminate_process(process)
        else:
            await wait_task
    finally:
        registry.unregister(run_id, process)
        if cancel_task is not None:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)
        if not wait_task.done():
            try:
                await asyncio.wait_for(wait_task, timeout=1.0)
            except TimeoutError:
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
        else:
            await asyncio.gather(wait_task, return_exceptions=True)
        try:
            await asyncio.wait_for(asyncio.gather(stdout_task, return_exceptions=True), timeout=1.0)
        except TimeoutError:
            stdout_task.cancel()
            await asyncio.gather(stdout_task, return_exceptions=True)
        try:
            summary.stderr = await asyncio.wait_for(stderr_task, timeout=1.0)
        except TimeoutError:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        else:
            await asyncio.gather(stderr_task, return_exceptions=True)
        summary.returncode = process.returncode
        if registry.consume_cancel_requested(run_id):
            summary.cancelled = True
        await _wait_for_subprocess_transport_closed(process)

    return summary


async def cancel_lengrvis_code_run(run_id: str) -> bool:
    return await lengrvis_code_process_registry.cancel(run_id)


def lengrvis_code_summary_to_turn_result(state: RunState, summary: LengrvisCodeStreamSummary) -> EngineTurnResult:
    next_turn = state.turn_count + 1
    observations = list(state.observations)
    observations.append(
        RunObservation(
            turn=next_turn,
            source=f"{LENGRVIS_CODE_ADAPTER_NAME}.stream_json",
            message=_summary_message(summary),
            payload=_summary_payload(summary),
        )
    )

    if summary.cancelled:
        phase = RunPhase.CANCELLED
        transition_reason = f"{LENGRVIS_CODE_DISPLAY_NAME} process cancelled."
    elif summary.is_error:
        phase = RunPhase.FAILED
        transition_reason = _error_reason(summary)
    else:
        phase = RunPhase.COMPLETED
        transition_reason = f"{LENGRVIS_CODE_DISPLAY_NAME} stream-json run completed."

    updated = state.model_copy(
        update={
            "phase": phase,
            "turn_count": next_turn,
            "transition_reason": transition_reason,
            "observations": observations,
        },
        deep=True,
    )
    payload = _summary_payload(summary)
    result_message = payload.get("assistant_text") if not summary.is_error else ""
    return EngineTurnResult(
        state=updated,
        finished=True,
        message=str(result_message or transition_reason),
        outputs={LENGRVIS_CODE_ADAPTER_NAME: payload},
    )


def _configured_command(config: LengrvisCodeConfig) -> tuple[str, ...]:
    if config.command:
        return tuple(str(part) for part in config.command)
    if config.executable:
        return (str(config.executable), *(str(part) for part in config.executable_args))
    return ()


def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command:
        return ()
    first = command[0]
    if os.path.isabs(first) or any(sep in first for sep in ("/", "\\")):
        return command
    resolved = shutil.which(first)
    return (resolved, *command[1:]) if resolved else command


def _command_safety_error(command: Sequence[str], *, build_env: Mapping[str, Any] | None = None) -> str:
    try:
        _assert_no_forbidden_flags(command)
        for raw_tools in _allowed_tools_args(command):
            allow_write_tools = any(str(tool).split("(", 1)[0] in WRITE_CAPABLE_ALLOWED_TOOLS for tool in raw_tools)
            validate_allowed_tools(raw_tools, allow_write_tools=allow_write_tools)
    except ValueError as exc:
        return str(exc)
    if build_env is not None:
        leaked = [key for key in BLOCKED_ENV_KEYS if build_env.get(key)]
        if leaked:
            return f"{LENGRVIS_CODE_DISPLAY_NAME} env must not include Anthropic credentials: {', '.join(leaked)}"
        # P1-11 fix: Also check for sensitive env keys that slipped through.
        # Keys the adapter intentionally injects for the OpenAI-compatible provider
        # (e.g. OPENAI_API_KEY) are required for authentication and must not be
        # treated as leaked parent credentials.
        sensitive_leaked = [
            key
            for key in build_env
            if _is_sensitive_env_key(key) and key not in BLOCKED_ENV_KEYS and key not in ADAPTER_INJECTED_ENV_KEYS
        ]
        if sensitive_leaked:
            return (
                f"{LENGRVIS_CODE_DISPLAY_NAME} env must not include sensitive keys: {', '.join(sensitive_leaked[:5])}"
            )
    return ""


def _is_allowed_bash_tool(tool: str) -> bool:
    if not (tool.startswith("Bash(") and tool.endswith(")")):
        return False
    command = tool[len("Bash(") : -1]
    if command.endswith(":*"):
        command = command[:-2]
    return command in {
        "git status",
        "git diff",
        "git log",
        "git show",
        "pytest",
        "python -m pytest",
        "npm test",
        "pnpm test",
    }


def _allowed_tools_args(command: Sequence[str]) -> list[list[str]]:
    tool_lists: list[list[str]] = []
    for index, token in enumerate(command):
        if token in {"--allowed-tools", "--allowedTools"} and index + 1 < len(command):
            raw = str(command[index + 1])
            tool_lists.append([item.strip() for item in raw.split(",")])
    return tool_lists


def _assert_no_forbidden_flags(command: Sequence[str]) -> None:
    for token in command:
        text = str(token)
        if any(text == flag or text.startswith(f"{flag}=") for flag in FORBIDDEN_CLI_FLAGS):
            raise ValueError(f"--dangerously-skip-permissions must not be used for {LENGRVIS_CODE_DISPLAY_NAME} runs.")


def _parse_ndjson_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def _wait_for_subprocess_transport_closed(process: asyncio.subprocess.Process) -> None:
    protocol = getattr(process, "_protocol", None)
    for _ in range(10):
        transport = getattr(process, "_transport", None)
        protocol_transport = getattr(protocol, "_transport", None)
        pipe_fds = getattr(protocol, "_pipe_fds", ())
        if (transport is None or transport.is_closing()) and protocol_transport is None and not pipe_fds:
            return
        await asyncio.sleep(0.05 if os.name == "nt" else 0)
    transport = getattr(process, "_transport", None)
    if transport is not None and not transport.is_closing():
        transport.close()
        await asyncio.sleep(0.1 if os.name == "nt" else 0)
