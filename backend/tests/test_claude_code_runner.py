from __future__ import annotations

import asyncio
import sys

import pytest

from app.config import AppSettings
from app.orchestration.claude_code_config import ClaudeCodeConfig
from app.orchestration.claude_code_runner import (
    classify_claude_code_error,
    claude_code_summary_to_turn_result,
    parse_claude_code_ndjson_lines,
    run_claude_code,
)
from app.orchestration.execution_models import RunPhase, RunState


@pytest.fixture
def fake_claude_cli(tmp_path):
    script = tmp_path / "fake_claude.py"
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

record_path = os.environ.get("FAKE_CLAUDE_RECORD")
if record_path:
    Path(record_path).write_text(
        json.dumps(
            {
                "argv": sys.argv[1:],
                "env": {
                    key: os.environ.get(key)
                    for key in [
                        "CLAUDE_CODE_USE_OPENAI",
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
async def test_fake_claude_code_stream_json_becomes_mavris_result(tmp_path, fake_claude_cli) -> None:
    record_path = tmp_path / "record.json"
    settings = AppSettings(
        base_url="https://openai-compatible.example/v1",
        api_key="test-api-key",
        model="openai/gpt-5",
    )
    config = ClaudeCodeConfig(
        command=(sys.executable, "-u", str(fake_claude_cli)),
        env={
            "FAKE_CLAUDE_RECORD": str(record_path),
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
        },
    )

    summary = await run_claude_code("make a safe edit", cwd=tmp_path, settings=settings, config=config)
    state = RunState(run_id="devrun_fake", engine="developer", phase=RunPhase.RUNNING, goal="make a safe edit")
    result = claude_code_summary_to_turn_result(state, summary)

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert result.message == "Fake done"
    assert result.outputs["claude_code"]["ok"] is True
    assert result.outputs["claude_code"]["tool_events"][0]["name"] == "Read"
    assert result.state.observations[0].source == "claude_code.stream_json"
    assert result.outputs["claude_code"]["error_classification"] is None
    assert {"agent.message", "tool.proposed", "tool.progress", "tool.result", "run.completed"}.issubset(
        {event["name"] for event in result.outputs["claude_code"]["mavris_events"]}
    )
    tool_proposed = next(event for event in result.outputs["claude_code"]["mavris_events"] if event["name"] == "tool.proposed")
    assert tool_proposed["payload"]["claude_event_index"] == 2
    assert tool_proposed["payload"]["source_event_type"] == "assistant"
    assert tool_proposed["payload"]["tool_name"] == "Read"
    assert "README.md" in tool_proposed["payload"]["tool_input_summary"]

    record = record_path.read_text(encoding="utf-8")
    assert "--output-format" in record
    assert "stream-json" in record
    assert "--dangerously-skip-permissions" not in record
    assert "Bash(*)" not in record
    assert '"CLAUDE_CODE_USE_OPENAI": "1"' in record
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
async def test_cancel_terminates_fake_claude_code_process(tmp_path, fake_claude_cli) -> None:
    record_path = tmp_path / "cancel-record.json"
    cancel_event = asyncio.Event()
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")
    config = ClaudeCodeConfig(
        command=(sys.executable, "-u", str(fake_claude_cli)),
        extra_args=("--mode", "sleep"),
        env={"FAKE_CLAUDE_RECORD": str(record_path)},
    )

    task = asyncio.create_task(
        run_claude_code("wait until cancelled", cwd=tmp_path, settings=settings, config=config, cancel_event=cancel_event)
    )
    while not record_path.exists():
        await asyncio.sleep(0.01)
    cancel_event.set()
    summary = await asyncio.wait_for(task, timeout=5)
    state = RunState(run_id="devrun_cancel", engine="developer", phase=RunPhase.RUNNING, goal="cancel")
    result = claude_code_summary_to_turn_result(state, summary)

    assert summary.cancelled is True
    assert summary.returncode is not None
    assert result.state.phase == RunPhase.CANCELLED
    assert result.outputs["claude_code"]["cancelled"] is True
    assert result.outputs["claude_code"]["error_classification"] == "cancelled"
    assert any(event["name"] == "run.cancelled" for event in result.outputs["claude_code"]["mavris_events"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected", "phase"),
    [
        ("badjson", "bad_ndjson", RunPhase.FAILED),
        ("nonzero", "non_zero_exit", RunPhase.FAILED),
        ("result-error", "claude_result_error", RunPhase.FAILED),
        ("permission", "permission_denial", RunPhase.FAILED),
    ],
)
async def test_claude_code_error_classification_modes(tmp_path, fake_claude_cli, mode, expected, phase) -> None:
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")
    config = ClaudeCodeConfig(command=(sys.executable, "-u", str(fake_claude_cli)), extra_args=("--mode", mode))

    summary = await run_claude_code("classify failure", cwd=tmp_path, settings=settings, config=config)
    state = RunState(run_id=f"devrun_{mode}", engine="developer", phase=RunPhase.RUNNING, goal="classify")
    result = claude_code_summary_to_turn_result(state, summary)

    assert classify_claude_code_error(summary) == expected
    assert result.state.phase == phase
    assert result.outputs["claude_code"]["ok"] is False
    assert result.outputs["claude_code"]["error_classification"] == expected
    assert any(event["name"] == "run.failed" for event in result.outputs["claude_code"]["mavris_events"])
    if expected == "permission_denial":
        assert result.outputs["claude_code"]["permission_denials"][0]["tool_name"] == "Write"
        assert result.outputs["claude_code"]["usage"] == {"input_tokens": 1}
    if expected == "non_zero_exit":
        assert "fatal fake stderr" in result.outputs["claude_code"]["stderr"]
        assert result.outputs["claude_code"]["stderr_diagnostics"] == ["fatal fake stderr"]


def test_bad_ndjson_summary_is_classified_as_error() -> None:
    summary = parse_claude_code_ndjson_lines(["not-json\n"])

    assert classify_claude_code_error(summary) == "bad_ndjson"
    assert summary.is_error is True


@pytest.mark.asyncio
async def test_launch_failure_returns_health_diagnostic(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MARVIS_CLAUDE_CODE_COMMAND", raising=False)
    monkeypatch.setenv("MARVIS_CLAUDE_CODE_VENDOR_ROOT", str(tmp_path / "missing-vendor"))
    settings = AppSettings(api_key="test-api-key", model="openai/gpt-5")

    summary = await run_claude_code("launch", cwd=tmp_path, settings=settings, config=ClaudeCodeConfig())
    state = RunState(run_id="devrun_launch_failure", engine="developer", phase=RunPhase.RUNNING, goal="launch")
    result = claude_code_summary_to_turn_result(state, summary)

    assert result.state.phase == RunPhase.FAILED
    assert result.outputs["claude_code"]["error_classification"] == "launch_failure"
    assert result.outputs["claude_code"]["runtime_health"]["build_required"] is True
    assert "MARVIS_CLAUDE_CODE_COMMAND" in result.outputs["claude_code"]["diagnostics"][1]
