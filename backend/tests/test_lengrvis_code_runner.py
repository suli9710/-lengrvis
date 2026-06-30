from __future__ import annotations

import asyncio
import sys

import pytest

from app.config import AppSettings
from app.orchestration.execution_models import RunPhase, RunState
from app.orchestration.lengrvis_code_config import LengrvisCodeConfig
from app.orchestration.lengrvis_code_runner import (
    LengrvisCodeProcessRegistry,
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
    assert result.message == "Fake done"
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
    assert "README.md" in tool_proposed["payload"]["tool_input_summary"]

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

        def terminate(self) -> None:
            self.terminated = True

    process = Process()
    with registry._lock:  # noqa: SLF001 - regression test for cross-loop cancellation fallback.
        registry._processes["run_closed_loop"] = (process, loop)  # noqa: SLF001
    loop.close()

    cancelled = await registry.cancel("run_closed_loop")

    assert cancelled is True
    assert process.terminated is True
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
