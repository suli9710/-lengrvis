from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, AsyncIterator, Iterable, Mapping, Sequence

from app.config import AppSettings, PROJECT_ROOT
from app.orchestration.execution_models import EngineTurnResult, RunObservation, RunPhase, RunState


VENDORED_CLAUDE_CODE_ROOT = PROJECT_ROOT / "vendor" / "claude-code"
VENDOR_ROOT_ENV = "MARVIS_CLAUDE_CODE_VENDOR_ROOT"
COMMAND_ENV = "MARVIS_CLAUDE_CODE_COMMAND"
DEFAULT_PERMISSION_MODE = "acceptEdits"
DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
    "Bash(npm test:*)",
    "Bash(pnpm test:*)",
    "Agent",
)
MAX_ADAPTER_EVENTS = 500
FORBIDDEN_ALLOWED_TOOLS: tuple[str, ...] = ("Bash", "Bash(*)")
BLOCKED_ENV_KEYS: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
FORBIDDEN_CLI_FLAGS: tuple[str, ...] = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")
OPENAI_MODEL_ENV_KEYS: tuple[str, ...] = (
    "OPENAI_DEFAULT_SONNET_MODEL",
    "OPENAI_DEFAULT_OPUS_MODEL",
    "OPENAI_DEFAULT_HAIKU_MODEL",
    "OPENAI_SMALL_FAST_MODEL",
)


@dataclass(slots=True)
class ClaudeCodeRuntime:
    """Resolved Claude Code runtime command and source root."""

    source_root: Path = VENDORED_CLAUDE_CODE_ROOT
    command: tuple[str, ...] = ()
    reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.command)


@dataclass(slots=True)
class ClaudeCodeConfig:
    """Configuration for one Claude Code headless run."""

    command: tuple[str, ...] = ()
    executable: str = ""
    executable_args: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    max_turns: int = 1
    permission_mode: str = DEFAULT_PERMISSION_MODE
    extra_args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ClaudeCodeStreamSummary:
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

    @property
    def final_text(self) -> str:
        if self.result and isinstance(self.result.get("result"), str):
            return str(self.result["result"]).strip()
        return "\n".join(text for text in self.assistant_text if text).strip()

    @property
    def is_error(self) -> bool:
        if self.cancelled:
            return True
        if self.returncode not in {None, 0}:
            return True
        return bool(self.result and self.result.get("is_error"))


class ClaudeCodeProcessRegistry:
    """Tracks Claude Code subprocesses by Mavris run_id for cancellation."""

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
        future = asyncio.run_coroutine_threadsafe(
            self._cancel_on_owner_loop(run_id, process, timeout_seconds=timeout_seconds),
            loop,
        )
        try:
            return await asyncio.wrap_future(future)
        except (RuntimeError, concurrent.futures.CancelledError):
            try:
                process.terminate()
            except ProcessLookupError:
                self.unregister(run_id, process)
            return True

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
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        finally:
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


claude_code_process_registry = ClaudeCodeProcessRegistry()


def resolve_claude_code_runtime(
    command: Sequence[str] | None = None,
    *,
    source_root: str | Path | None = None,
) -> ClaudeCodeRuntime:
    """Resolve a stable command without coupling callers to Claude Code internals."""

    explicit = tuple(str(part) for part in (command or ())) or claude_code_command_from_env()
    if explicit:
        return ClaudeCodeRuntime(command=_resolve_command(explicit), reason="explicit command")

    root = _vendor_source_root(source_root)
    node_cli = root / "dist" / "cli-node.js"
    if node_cli.exists():
        node = shutil.which("node")
        if node:
            return ClaudeCodeRuntime(source_root=root, command=(node, str(node_cli)), reason="vendored dist/cli-node.js")
        return ClaudeCodeRuntime(reason="vendored dist/cli-node.js exists but node was not found")

    bun_cli = root / "dist" / "cli-bun.js"
    if bun_cli.exists():
        bun = shutil.which("bun")
        if bun:
            return ClaudeCodeRuntime(source_root=root, command=(bun, str(bun_cli)), reason="vendored dist/cli-bun.js")
        return ClaudeCodeRuntime(reason="vendored dist/cli-bun.js exists but bun was not found")

    return ClaudeCodeRuntime(
        source_root=root,
        reason=(
            f"Claude Code CLI is unavailable at {root}. Build the vendored snapshot to create dist/cli-node.js "
            f"or set {COMMAND_ENV} explicitly."
        )
    )


def claude_code_command_from_env() -> tuple[str, ...]:
    raw = os.environ.get(COMMAND_ENV, "").strip()
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw, posix=True))
    except ValueError:
        return (raw,)


def _vendor_source_root(source_root: str | Path | None = None) -> Path:
    raw = source_root or os.environ.get(VENDOR_ROOT_ENV) or VENDORED_CLAUDE_CODE_ROOT
    return Path(raw).expanduser().resolve(strict=False)


def build_claude_code_env(settings: AppSettings, *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Map Mavris OpenAI-compatible settings into Claude Code's OpenAI provider env."""

    env = dict(os.environ if base_env is None else base_env)
    for key in BLOCKED_ENV_KEYS:
        env.pop(key, None)
    model = str(settings.model or "").strip()
    env.update(
        {
            "CLAUDE_CODE_USE_OPENAI": "1",
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


def default_allowed_tools() -> tuple[str, ...]:
    return DEFAULT_ALLOWED_TOOLS


def validate_allowed_tools(allowed_tools: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(tool).strip() for tool in allowed_tools if str(tool).strip())
    for tool in normalized:
        if tool in FORBIDDEN_ALLOWED_TOOLS:
            raise ValueError(f"Unsafe Claude Code allowedTools entry is not permitted: {tool}")
        if tool.startswith("Bash(") and not _is_allowed_bash_tool(tool):
            raise ValueError(f"Unsafe Claude Code Bash allowedTools entry is not permitted: {tool}")
    return normalized


def build_claude_code_command(
    prompt: str,
    *,
    cwd: str | Path,
    settings: AppSettings | None = None,
    config: ClaudeCodeConfig | None = None,
) -> list[str]:
    active = config or ClaudeCodeConfig()
    workspace = Path(cwd).expanduser().resolve(strict=False)
    extra_args = tuple(str(arg) for arg in active.extra_args)
    _assert_no_forbidden_flags(extra_args)
    runtime = resolve_claude_code_runtime(_configured_command(active))
    if not runtime.command:
        raise RuntimeError(runtime.reason)

    model = str((settings.model if settings is not None else "") or "").strip()
    allowed_tools = validate_allowed_tools(active.allowed_tools)

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
    if active.env:
        assert_safe_claude_code_invocation(command, build_env=active.env)
    return command


def assert_safe_claude_code_invocation(command: Sequence[str], *, build_env: Mapping[str, Any]) -> None:
    _assert_no_forbidden_flags(command)
    for raw_tools in _allowed_tools_args(command):
        validate_allowed_tools(raw_tools)
    leaked = [key for key in BLOCKED_ENV_KEYS if build_env.get(key)]
    if leaked:
        raise ValueError(f"Claude Code env must not include Anthropic credentials: {', '.join(leaked)}")


def parse_claude_code_ndjson_lines(lines: Iterable[str]) -> ClaudeCodeStreamSummary:
    summary = ClaudeCodeStreamSummary()
    for raw_line in lines:
        event = _parse_ndjson_line(raw_line)
        if event is None:
            if raw_line.strip():
                summary.invalid_lines.append(raw_line.rstrip("\r\n"))
            continue
        _record_event(summary, event)
    return summary


async def iter_claude_code_ndjson(stream: asyncio.StreamReader) -> AsyncIterator[dict[str, Any]]:
    while True:
        line = await stream.readline()
        if not line:
            break
        event = _parse_ndjson_line(line.decode("utf-8", errors="replace"))
        if event is not None:
            yield event


async def run_claude_code(
    prompt: str,
    *,
    cwd: str | Path,
    settings: AppSettings,
    config: ClaudeCodeConfig | None = None,
    run_id: str = "",
    cancel_event: asyncio.Event | None = None,
    registry: ClaudeCodeProcessRegistry = claude_code_process_registry,
) -> ClaudeCodeStreamSummary:
    active = config or ClaudeCodeConfig(max_turns=settings.agent_loop_max_turns)
    env = build_claude_code_env(settings, base_env={**os.environ, **active.env})
    launch_config = ClaudeCodeConfig(
        command=active.command,
        executable=active.executable,
        executable_args=active.executable_args,
        allowed_tools=active.allowed_tools,
        max_turns=active.max_turns,
        permission_mode=active.permission_mode,
        extra_args=active.extra_args,
        env=env,
    )
    command = build_claude_code_command(prompt, cwd=cwd, settings=settings, config=launch_config)
    assert_safe_claude_code_invocation(command, build_env=env)

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(Path(cwd).expanduser().resolve(strict=False)),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    registry.register(run_id, process)
    summary = ClaudeCodeStreamSummary(command=_redacted_command(command))

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
        try:
            await asyncio.wait_for(asyncio.gather(stdout_task, return_exceptions=True), timeout=1.0)
        except asyncio.TimeoutError:
            stdout_task.cancel()
            await asyncio.gather(stdout_task, return_exceptions=True)
        try:
            summary.stderr = await asyncio.wait_for(stderr_task, timeout=1.0)
        except asyncio.TimeoutError:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        summary.returncode = process.returncode
        if registry.consume_cancel_requested(run_id):
            summary.cancelled = True

    return summary


async def cancel_claude_code_run(run_id: str) -> bool:
    return await claude_code_process_registry.cancel(run_id)


def claude_code_summary_to_turn_result(state: RunState, summary: ClaudeCodeStreamSummary) -> EngineTurnResult:
    next_turn = state.turn_count + 1
    observations = list(state.observations)
    observations.append(
        RunObservation(
            turn=next_turn,
            source="claude_code.stream_json",
            message=_summary_message(summary),
            payload=_summary_payload(summary),
        )
    )

    if summary.cancelled:
        phase = RunPhase.CANCELLED
        transition_reason = "Claude Code process cancelled."
    elif summary.is_error:
        phase = RunPhase.FAILED
        transition_reason = _error_reason(summary)
    else:
        phase = RunPhase.COMPLETED
        transition_reason = "Claude Code stream-json run completed."

    updated = state.model_copy(
        update={
            "phase": phase,
            "turn_count": next_turn,
            "transition_reason": transition_reason,
            "observations": observations,
        },
        deep=True,
    )
    return EngineTurnResult(
        state=updated,
        finished=True,
        message=summary.final_text or transition_reason,
        outputs={"claude_code": _summary_payload(summary)},
    )


def _configured_command(config: ClaudeCodeConfig) -> tuple[str, ...]:
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
            raise ValueError("--dangerously-skip-permissions must not be used for Mavris Claude Code runs.")


def _parse_ndjson_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _record_event(summary: ClaudeCodeStreamSummary, event: dict[str, Any]) -> None:
    summary.events.append(event)
    event_type = str(event.get("type") or "")
    if event_type == "assistant":
        summary.assistant_text.extend(_assistant_text(event))
        summary.tool_events.extend(_assistant_tool_uses(event))
    elif event_type == "streamlined_text":
        text = event.get("text")
        if isinstance(text, str):
            summary.assistant_text.append(text)
    elif event_type == "streamlined_tool_use_summary":
        summary.tool_events.append(event)
    elif event_type == "system":
        summary.system_events.append(event)
    elif event_type == "result":
        summary.result = event


def _assistant_text(event: Mapping[str, Any]) -> list[str]:
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(str(block["text"]))
    return texts


def _assistant_tool_uses(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    tools: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "tool_use":
            tools.append(dict(block))
    return tools


def _summary_message(summary: ClaudeCodeStreamSummary) -> str:
    if summary.cancelled:
        return "Claude Code run was cancelled."
    if summary.is_error:
        return _error_reason(summary)
    if summary.final_text:
        return summary.final_text[:500]
    return f"Claude Code emitted {len(summary.events)} stream-json event(s)."


def _error_reason(summary: ClaudeCodeStreamSummary) -> str:
    if summary.result and isinstance(summary.result.get("errors"), list) and summary.result["errors"]:
        return "; ".join(str(item) for item in summary.result["errors"])
    if summary.result and isinstance(summary.result.get("subtype"), str):
        return f"Claude Code result: {summary.result['subtype']}"
    if summary.stderr.strip():
        return summary.stderr.strip()[:500]
    if summary.returncode not in {None, 0}:
        return f"Claude Code exited with code {summary.returncode}."
    return "Claude Code run failed."


def _summary_payload(summary: ClaudeCodeStreamSummary) -> dict[str, Any]:
    adapter_events = _adapter_events(summary)
    payload: dict[str, Any] = {
        "ok": not summary.is_error,
        "cancelled": summary.cancelled,
        "returncode": summary.returncode,
        "event_count": len(summary.events),
        "assistant_text": summary.final_text,
        "tool_events": summary.tool_events,
        "system_events": summary.system_events,
        "result": summary.result,
        "invalid_line_count": len(summary.invalid_lines),
        "diagnostics": _diagnostics(summary),
        "adapter_events": adapter_events,
        "mavris_events": [event["mavris_event"] for event in adapter_events if event.get("mavris_event")],
        "command": summary.command,
    }
    if summary.stderr:
        payload["stderr"] = summary.stderr[-4000:]
    if summary.invalid_lines:
        payload["invalid_lines"] = summary.invalid_lines[:10]
    return payload


def _diagnostics(summary: ClaudeCodeStreamSummary) -> list[str]:
    diagnostics: list[str] = []
    if summary.stderr.strip():
        diagnostics.append(f"Claude Code stderr: {summary.stderr.strip()[:500]}")
    if summary.invalid_lines:
        diagnostics.append(f"Malformed Claude Code stream-json lines: {len(summary.invalid_lines)}")
    if summary.returncode not in {None, 0}:
        diagnostics.append(f"Claude Code exited with code {summary.returncode}.")
    if summary.result and isinstance(summary.result.get("errors"), list):
        diagnostics.extend(str(item) for item in summary.result["errors"])
    return diagnostics


def _adapter_events(summary: ClaudeCodeStreamSummary) -> list[dict[str, Any]]:
    start = max(0, len(summary.events) - MAX_ADAPTER_EVENTS)
    events: list[dict[str, Any]] = []
    for offset, event in enumerate(summary.events[start:], start=start + 1):
        event_type = str(event.get("type") or "unknown")
        message = _event_summary(event)
        events.append(
            {
                "sequence": offset,
                "event_type": event_type,
                "summary": message,
                "mavris_event": _mavris_event_for(event, message),
            }
        )
    return events


def _event_summary(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("type") or "unknown")
    if event_type == "system":
        subtype = event.get("subtype")
        if subtype == "init":
            tools = event.get("tools") if isinstance(event.get("tools"), list) else []
            return f"Claude Code initialized with {len(tools)} tools."
        return f"Claude Code system event: {subtype or 'unknown'}."
    if event_type == "assistant":
        texts = _assistant_text(event)
        if texts:
            return "\n".join(texts).strip()[:500]
        tools = _assistant_tool_names(event)
        if tools:
            return f"Claude Code requested tool(s): {', '.join(tools)}."
        return "Claude Code assistant message."
    if event_type == "result":
        if isinstance(event.get("result"), str) and event.get("result"):
            return str(event["result"])[:500]
        if isinstance(event.get("errors"), list) and event["errors"]:
            return "; ".join(str(item) for item in event["errors"])[:500]
        return f"Claude Code result: {event.get('subtype') or 'unknown'}."
    return f"Claude Code event: {event_type}."


def _mavris_event_for(event: Mapping[str, Any], message: str) -> dict[str, Any]:
    event_type = str(event.get("type") or "unknown")
    if event_type == "assistant":
        tools = _assistant_tool_names(event)
        if tools:
            return {
                "name": "tool.progress",
                "payload": {
                    "tool_name": "claude_code",
                    "status": "running",
                    "message": message,
                    "claude_tools": tools,
                },
            }
        return {
            "name": "agent.message",
            "payload": {"agent": "ClaudeCode", "message": message, "source": "claude_code"},
        }
    if event_type == "result":
        return {
            "name": "tool.result",
            "payload": {
                "tool_name": "claude_code",
                "status": "completed" if event.get("subtype") == "success" else "failed",
                "message": message,
            },
        }
    return {
        "name": "tool.progress",
        "payload": {"tool_name": "claude_code", "status": "running", "message": message, "event_type": event_type},
    }


def _assistant_tool_names(event: Mapping[str, Any]) -> list[str]:
    return [str(tool.get("name")) for tool in _assistant_tool_uses(event) if tool.get("name")]


def _redacted_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    for token in command:
        text = str(token)
        if text.startswith("sk-"):
            redacted.append("[REDACTED]")
        else:
            redacted.append(text)
    return redacted


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()
