from __future__ import annotations

import asyncio
import json
import sys

import pytest

from app.config import AppSettings
from app.orchestration.execution_models import RunPhase, RunState
from app.orchestration.lengrvis_code_config import LengrvisCodeConfig
from app.orchestration.lengrvis_code_runner import (
    LengrvisCodeProcessRegistry,
    LengrvisCodeStreamSummary,
    classify_lengrvis_code_error,
    lengrvis_code_summary_to_turn_result,
    parse_lengrvis_code_ndjson_lines,
    run_lengrvis_code,
)


@pytest.fixture
def fake_lengrvis_cli(tmp_path):
    script = tmp_path / "fake_lengrvis.py"
    script.write_text(
        """
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--print", action="store_true")
parser.add_argument("--output-format")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--bare", action="store_true")
parser.add_argument("--model")
parser.add_argument("--add-dir")
parser.add_argument("--permission-mode")
parser.add_argument("--allowedTools", "--allowed-tools", dest="allowed_tools")
parser.add_argument("--max-turns")
parser.add_argument("--mode", choices=["stream", "sleep", "badjson", "nonzero", "result-error", "permission"], default="stream")
parser.add_argument("prompt")
args = parser.parse_args()

record_path = os.environ.get("FAKE_LENGRVIS_RECORD")
if record_path:
    Path(record_path).write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "env": {
                    key: os.environ.get(key)
                    for key in [
                        "LENGRVIS_CODE_USE_OPENAI",
                        "OPENAI_API_KEY",
                        "OPENAI_BASE_URL",
                        "OPENAI_MODEL",
                        "OPENAI_DEFAULT_SONNET_MODEL",
                        "OPENAI_DEFAULT_OPUS_MODEL",
                        "OPENAI_DEFAULT_HAIKU_MODEL",
                        "OPENAI_SMALL_FAST_MODEL",
                        "ANTHROPIC_API_KEY",
                        "ANTHROPIC_AUTH_TOKEN",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

if args.mode == "sleep":
    def handle_signal(signum, frame):
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["terminated"]}), flush=True)
        sys.exit(23)

    signal.signal(signal.SIGTERM, handle_signal)
    time.sleep(30)
    sys.exit(0)

if args.mode == "badjson":
    print("not-json", flush=True)
    sys.exit(0)

if args.mode == "nonzero":
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "About to fail"}]}}), flush=True)
    print("fatal fake stderr", file=sys.stderr, flush=True)
    sys.exit(7)

if args.mode == "result-error":
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["tool result missing"]}), flush=True)
    sys.exit(0)

if args.mode == "permission":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Denied", "permission_denials": [{"tool_name": "Write", "reason": "policy"}], "usage": {"input_tokens": 1}}), flush=True)
    sys.exit(0)

print(json.dumps({"type": "system", "subtype": "init", "tools": args.allowed_tools.split(",")}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Fake work"}, {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "duration_ms": 1, "duration_api_ms": 1, "is_error": False, "num_turns": 1, "result": "Fake done", "stop_reason": "end_turn", "total_cost_usd": 0, "usage": {}, "modelUsage": {}, "permission_denials": []}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    return script


@pytest.mark.asyncio
async def test_fake_lengrvis_code_stream_json_becomes_lengrvis_result(tmp_path, fake_lengrvis_cli) -> None:
    record_path = tmp_path / "record.json"
    settings = AppSettings(
        base_url="https://openai-compatible.example/v1",
        api_key="test-api-key",
        model="openai/gpt-5",
    )
    config = LengrvisCodeConfig(
        command=(sys.executable, "-u", str(fake_lengrvis_cli)),
        env={
            "FAKE_LENGRVIS_RECORD": str(record_path),
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
        },
    )

    summary = await run_lengrvis_code("make a safe edit", cwd=tmp_path, settings=settings, config=config)
    state = RunState(run_id="devrun_fake", engine="developer", phase=RunPhase.RUNNING, goal="make a safe edit")
    result = lengrvis_code_summary_to_turn_result(state, summary)

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert result.message == "[REDACTED_LENGRVIS_CODE_FINAL_TEXT]"
    payload = result.outputs["lengrvis_code"]
    assert payload["ok"] is True
    assert payload["display_name"] == "Lengrvis Code"
    assert payload["tool_events"][0]["name"] == "Read"
    assert result.state.observations[0].source == "lengrvis_code.stream_json"
    assert payload["error_classification"] is None
    assert {"agent.message", "tool.proposed", "tool.progress", "tool.result", "run.completed"}.issubset(
        {event["name"] for event in payload["lengrvis_events"]}
    )
    tool_proposed = next(event for event in payload["lengrvis_events"] if event["name"] == "tool.proposed")
    assert tool_proposed["payload"]["source_event_index"] == 2
    assert tool_proposed["payload"]["source_event_type"] == "assistant"
    assert tool_proposed["payload"]["tool_name"] == "Read"
    assert tool_proposed["payload"]["adapter_tool_name"] == "lengrvis_code"
    assert "README.md" not in tool_proposed["payload"]["tool_input_summary"]
    assert "file_path=[REDACTED_PATH]" in tool_proposed["payload"]["tool_input_summary"]

    record = record_path.read_text(encoding="utf-8")
    assert "--output-format" in record
    assert "stream-json" in record
    assert "--dangerously-skip-permissions" not in record
    assert "Bash(*)" not in record
    assert '"LENGRVIS_CODE_USE_OPENAI": "1"' in record
    assert '"OPENAI_API_KEY": "test-api-key"' in record
    assert '"OPENAI_BASE_URL": "https://openai-compatible.example/v1"' in record
    assert '"OPENAI_MODEL": "openai/gpt-5"' in record
    assert '"OPENAI_DEFAULT_SONNET_MODEL": "openai/gpt-5"' in record
    assert '"OPENAI_DEFAULT_OPUS_MODEL": "openai/gpt-5"' in record
    assert '"OPENAI_DEFAULT_HAIKU_MODEL": "openai/gpt-5"' in record
    assert '"OPENAI_SMALL_FAST_MODEL": "openai/gpt-5"' in record
    assert '"ANTHROPIC_API_KEY": null' in record
    assert '"ANTHROPIC_AUTH_TOKEN": null' in record


@pytest.mark.asyncio
async def test_cancel_terminates_fake_lengrvis_code_process(tmp_path, fake_lengrvis_cli) -> None:
    record_path = tmp_path / "cancel-record.json"
    cancel_event = asyncio.Event()
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")
    config = LengrvisCodeConfig(
        command=(sys.executable, "-u", str(fake_lengrvis_cli)),
        extra_args=("--mode", "sleep"),
        env={"FAKE_LENGRVIS_RECORD": str(record_path)},
    )

    task = asyncio.create_task(
        run_lengrvis_code(
            "wait until cancelled", cwd=tmp_path, settings=settings, config=config, cancel_event=cancel_event
        )
    )
    while not record_path.exists():
        await asyncio.sleep(0.01)
    cancel_event.set()
    summary = await asyncio.wait_for(task, timeout=5)
    state = RunState(run_id="devrun_cancel", engine="developer", phase=RunPhase.RUNNING, goal="cancel")
    result = lengrvis_code_summary_to_turn_result(state, summary)

    assert summary.cancelled is True
    assert summary.returncode is not None
    assert result.state.phase == RunPhase.CANCELLED
    payload = result.outputs["lengrvis_code"]
    assert payload["cancelled"] is True
    assert payload["error_classification"] == "cancelled"
    assert any(event["name"] == "run.cancelled" for event in payload["lengrvis_events"])


@pytest.mark.asyncio
async def test_cancel_falls_back_when_registered_owner_loop_is_closed() -> None:
    registry = LengrvisCodeProcessRegistry()
    loop = asyncio.new_event_loop()

    class Process:
        returncode = None
        terminated = False
        killed = False

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.killed = True

    process = Process()
    with registry._lock:  # noqa: SLF001 - regression test for cross-loop cancellation fallback.
        registry._processes["run_closed_loop"] = (process, loop)  # noqa: SLF001
    loop.close()

    cancelled = await registry.cancel("run_closed_loop")

    assert cancelled is True
    assert process.terminated is True
    assert process.killed is False
    assert registry.active_run_ids() == []


@pytest.mark.asyncio
async def test_cancel_fallback_kills_and_only_unregisters_after_confirmed_exit() -> None:
    registry = LengrvisCodeProcessRegistry()
    loop = asyncio.new_event_loop()

    class Process:
        returncode = None
        terminated = False
        killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    process = Process()
    with registry._lock:  # noqa: SLF001 - regression test for cross-loop cancellation fallback.
        registry._processes["run_closed_loop_kill"] = (process, loop)  # noqa: SLF001
    loop.close()

    cancelled = await registry.cancel("run_closed_loop_kill", timeout_seconds=0.01)

    assert cancelled is True
    assert process.terminated is True
    assert process.killed is True
    assert registry.active_run_ids() == []


@pytest.mark.asyncio
async def test_cancel_fallback_checks_underlying_popen_when_asyncio_returncode_stays_stale() -> None:
    registry = LengrvisCodeProcessRegistry()
    loop = asyncio.new_event_loop()

    class Popen:
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

    class Transport:
        def __init__(self, popen: Popen) -> None:
            self._proc = popen

        def get_returncode(self) -> None:
            return None

    class Process:
        returncode = None
        terminated = False
        killed = False

        def __init__(self) -> None:
            self._popen = Popen()
            self._transport = Transport(self._popen)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self._popen.returncode = -9

    process = Process()
    with registry._lock:  # noqa: SLF001 - regression test for cross-loop cancellation fallback.
        registry._processes["run_closed_loop_stale_returncode"] = (process, loop)  # noqa: SLF001
    loop.close()

    cancelled = await registry.cancel("run_closed_loop_stale_returncode", timeout_seconds=0.01)

    assert cancelled is True
    assert process.terminated is True
    assert process.killed is True
    assert process.returncode is None
    assert registry.active_run_ids() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected", "phase"),
    [
        ("badjson", "bad_ndjson", RunPhase.FAILED),
        ("nonzero", "non_zero_exit", RunPhase.FAILED),
        ("result-error", "lengrvis_result_error", RunPhase.FAILED),
        ("permission", "permission_denial", RunPhase.FAILED),
    ],
)
async def test_lengrvis_code_error_classification_modes(tmp_path, fake_lengrvis_cli, mode, expected, phase) -> None:
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")
    config = LengrvisCodeConfig(command=(sys.executable, "-u", str(fake_lengrvis_cli)), extra_args=("--mode", mode))

    summary = await run_lengrvis_code("classify failure", cwd=tmp_path, settings=settings, config=config)
    state = RunState(run_id=f"devrun_{mode}", engine="developer", phase=RunPhase.RUNNING, goal="classify")
    result = lengrvis_code_summary_to_turn_result(state, summary)

    assert classify_lengrvis_code_error(summary) == expected
    assert result.state.phase == phase
    payload = result.outputs["lengrvis_code"]
    assert payload["ok"] is False
    assert payload["error_classification"] == expected
    assert any(event["name"] == "run.failed" for event in payload["lengrvis_events"])
    if expected == "permission_denial":
        assert payload["permission_denials"][0]["tool_name"] == "Write"
        assert payload["usage"] == {"input_tokens": 1}
    if expected == "non_zero_exit":
        assert "fatal fake stderr" in payload["stderr"]
        assert payload["stderr_diagnostics"] == ["fatal fake stderr"]


def test_bad_ndjson_summary_is_classified_as_error() -> None:
    summary = parse_lengrvis_code_ndjson_lines(["not-json\n"])

    assert classify_lengrvis_code_error(summary) == "bad_ndjson"
    assert summary.is_error is True


def test_lengrvis_code_public_failure_payload_redacts_diagnostics() -> None:
    raw_path = "C:/Users/Suli/private/project/.env"
    raw_file = "leaky-output.log"
    secret_token = "lengrvis-code-secret-1234567890"
    api_key = "sk-lengrvis-code-secret"
    summary = LengrvisCodeStreamSummary(
        launch_error=f"failed to spawn {raw_path} token={secret_token}",
        stderr=f"stderr references {raw_file} api_key={api_key}",
        invalid_lines=[f"not-json from {raw_path} token={secret_token}"],
        result={
            "is_error": True,
            "subtype": "error_during_execution",
            "result": f"failed near {raw_path}",
            "errors": [f"tool failed at {raw_file} token={secret_token}"],
            "permission_denials": [{"tool_name": "Write", "reason": f"policy blocked {raw_path}"}],
        },
    )
    state = RunState(run_id="devrun_private_failure", engine="developer", phase=RunPhase.RUNNING, goal="classify")

    result = lengrvis_code_summary_to_turn_result(state, summary)
    payload = result.outputs["lengrvis_code"]
    serialized = json.dumps(
        {
            "message": result.message,
            "transition_reason": result.state.transition_reason,
            "payload": payload,
        },
        sort_keys=True,
    )

    assert result.state.phase == RunPhase.FAILED
    assert "failed to spawn" in result.state.transition_reason
    assert "[REDACTED_LOCAL_PATH]" in serialized
    assert raw_path not in serialized
    assert raw_file not in serialized
    assert secret_token not in serialized
    assert api_key not in serialized
    assert payload["launch_error"] == result.state.transition_reason.removeprefix("Lengrvis Code launch failure: ")
    assert payload["stderr_diagnostics"] == ["stderr references [REDACTED_FILE_NAME] api_key=[REDACTED]"]
    assert payload["invalid_lines"] == ["not-json from [REDACTED_LOCAL_PATH] token=[REDACTED]"]


def test_lengrvis_code_public_payload_redacts_system_events_and_runtime_health() -> None:
    raw_path = "C:/Users/Suli/private/project"
    raw_script = f"{raw_path}/fake_lengrvis.py"
    plain_secret = "plain-runtime-secret"
    summary = LengrvisCodeStreamSummary(
        events=[{"type": "system", "subtype": "init", "cwd": raw_path, "api_key": plain_secret}],
        system_events=[
            {
                "type": "system",
                "subtype": "init",
                "cwd": raw_path,
                "api_key": plain_secret,
                "tools": [f"Bash(python {raw_script}:*)"],
            }
        ],
        result={"type": "result", "subtype": "success", "is_error": False, "result": "done"},
        runtime_health={
            "ok": True,
            "source_root": raw_path,
            "command": ["python", raw_script],
            "api_key": plain_secret,
        },
    )
    state = RunState(run_id="devrun_private_system", engine="developer", phase=RunPhase.RUNNING, goal="redact")

    result = lengrvis_code_summary_to_turn_result(state, summary)
    payload = result.outputs["lengrvis_code"]
    serialized = json.dumps(
        {"payload": payload, "observation_payload": result.state.observations[0].payload},
        sort_keys=True,
    )

    assert payload["system_events"][0]["cwd"] == "[REDACTED_LOCAL_PATH]"
    assert payload["system_events"][0]["api_key"] == "***"
    assert payload["runtime_health"]["source_root"] == "[REDACTED_LOCAL_PATH]"
    assert payload["runtime_health"]["api_key"] == "***"
    assert raw_path not in serialized
    assert raw_script not in serialized
    assert plain_secret not in serialized


def test_lengrvis_code_public_payload_redacts_semantic_tool_input_fields() -> None:
    secret_text = "plain confidential notes"
    raw_file = "C:/Users/Suli/private/project/notes.txt"
    raw_notebook = "/home/suli/private/notebook.ipynb"
    raw_command = f"type {raw_file} && echo {secret_text}"
    raw_source = f"print({secret_text!r})"
    semicolon_summary = (
        f"content={secret_text};command={raw_command};input={secret_text} through generic input"
    )
    summary = parse_lengrvis_code_ndjson_lines(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Working"},
                            {
                                "type": "tool_use",
                                "id": "toolu_private",
                                "name": "NotebookEdit",
                                "input": {
                                    "content": secret_text,
                                    "new_source": raw_source,
                                    "command": raw_command,
                                    "file_path": raw_file,
                                    "notebook_path": raw_notebook,
                                    "input": f"{secret_text} through generic input",
                                    "edits": [{"old_string": secret_text, "new_string": raw_source}],
                                },
                            },
                        ]
                    },
                }
            )
            + "\n",
            json.dumps(
                {
                    "type": "streamlined_tool_use_summary",
                    "summary": {
                        "tool_name": "NotebookEdit",
                        "summary": (
                            f"content={secret_text}, new_source={raw_source}, command={raw_command}, "
                            f"file_path={raw_file}, notebook_path={raw_notebook}, "
                            f"input={secret_text} through generic input;{semicolon_summary}"
                        ),
                    },
                }
            )
            + "\n",
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"}) + "\n",
        ]
    )
    summary.command = ["lengrvis-code", "--print", raw_command, secret_text]
    state = RunState(run_id="devrun_private_inputs", engine="developer", phase=RunPhase.RUNNING, goal="redact")

    result = lengrvis_code_summary_to_turn_result(state, summary)
    payload = result.outputs["lengrvis_code"]
    serialized = json.dumps(
        {"payload": payload, "observation_payload": result.state.observations[0].payload},
        sort_keys=True,
    )

    assert payload["tool_events"][0]["input"]["content"] == "[REDACTED_CONTENT]"
    assert payload["tool_events"][0]["input"]["new_source"] == "[REDACTED_CONTENT]"
    assert payload["tool_events"][0]["input"]["command"] == "[REDACTED_COMMAND]"
    assert payload["tool_events"][0]["input"]["file_path"] == "[REDACTED_PATH]"
    assert payload["tool_events"][0]["input"]["notebook_path"] == "[REDACTED_PATH]"
    assert payload["tool_events"][0]["input"]["input"] == "[REDACTED_INPUT]"
    assert payload["tool_events"][0]["input"]["edits"][0]["old_string"] == "[REDACTED_CONTENT]"
    assert payload["command"] == ["[REDACTED_COMMAND]"]
    assert "content=[REDACTED_CONTENT]" in serialized
    assert "new_source=[REDACTED_CONTENT]" in serialized
    assert "command=[REDACTED_COMMAND]" in serialized
    assert "file_path=[REDACTED_PATH]" in serialized
    assert "notebook_path=[REDACTED_PATH]" in serialized
    assert "input=[REDACTED_INPUT]" in serialized
    assert "content=[REDACTED_CONTENT];command=[REDACTED_COMMAND];input=[REDACTED_INPUT]" in serialized
    for raw in (secret_text, raw_file, raw_notebook, raw_command, raw_source):
        assert raw not in serialized


def test_lengrvis_code_public_payload_summarizes_tool_result_content() -> None:
    raw_file = "C:/Users/Suli/private/project/notes.txt"
    secret_text = "private file contents and customer notes"
    api_key = "sk-tool-result-secret"
    summary = parse_lengrvis_code_ndjson_lines(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": {"file_path": raw_file},
                            }
                        ]
                    },
                }
            )
            + "\n",
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "tool_name": "Read",
                                "content": f"{raw_file}\n{secret_text}\napi_key={api_key}",
                            }
                        ]
                    },
                }
            )
            + "\n",
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": f"{raw_file}\n{secret_text}\napi_key={api_key}",
                }
            )
            + "\n",
        ]
    )
    state = RunState(
        run_id="devrun_private_tool_result",
        engine="developer",
        phase=RunPhase.RUNNING,
        goal="redact tool result",
    )

    result = lengrvis_code_summary_to_turn_result(state, summary)
    payload = result.outputs["lengrvis_code"]
    tool_result_event = next(event for event in payload["lengrvis_events"] if event["name"] == "tool.result")
    serialized = json.dumps(
        {"payload": payload, "observation_payload": result.state.observations[0].payload},
        sort_keys=True,
    )

    assert tool_result_event["payload"]["message"] == "Lengrvis Code tool result completed with redacted text output."
    assert tool_result_event["payload"]["output"] == {
        "redacted": True,
        "is_error": False,
        "content_type": "str",
        "char_count": len(f"{raw_file}\n{secret_text}\napi_key={api_key}"),
    }
    assert payload["assistant_text"] == "[REDACTED_LENGRVIS_CODE_FINAL_TEXT]"
    assert payload["result"]["result"] == "[REDACTED_LENGRVIS_CODE_FINAL_TEXT]"
    for raw in (raw_file, secret_text, api_key):
        assert raw not in serialized


def test_lengrvis_code_split_helpers_remain_available_from_legacy_module() -> None:
    from app.integrations import lengrvis_code as legacy_module
    from app.integrations.lengrvis_code_events import _summary_payload
    from app.integrations.lengrvis_code_redaction import _public_lengrvis_code_text

    summary = LengrvisCodeStreamSummary(
        events=[{"type": "streamlined_text", "text": "hello"}],
        assistant_text=["hello"],
        result={"type": "result", "subtype": "success", "is_error": False, "result": "done"},
    )

    assert legacy_module._summary_payload is _summary_payload
    assert legacy_module._public_lengrvis_code_text is _public_lengrvis_code_text
    assert legacy_module._summary_payload(summary)["adapter_name"] == "lengrvis_code"


@pytest.mark.asyncio
async def test_launch_failure_returns_health_diagnostic(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LENGRVIS_CODE_COMMAND", raising=False)
    monkeypatch.delenv("LENGRVIS_CODE_COMMAND", raising=False)
    monkeypatch.setenv("LENGRVIS_CODE_VENDOR_ROOT", str(tmp_path / "missing-vendor"))
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")

    summary = await run_lengrvis_code("launch", cwd=tmp_path, settings=settings, config=LengrvisCodeConfig())
    state = RunState(run_id="devrun_launch_failure", engine="developer", phase=RunPhase.RUNNING, goal="launch")
    result = lengrvis_code_summary_to_turn_result(state, summary)

    assert result.state.phase == RunPhase.FAILED
    payload = result.outputs["lengrvis_code"]
    assert payload["error_classification"] == "launch_failure"
    assert payload["runtime_health"]["build_required"] is True
    assert "LENGRVIS_CODE_COMMAND" in payload["diagnostics"][1]
