from __future__ import annotations

import asyncio
import sys

import pytest

from app.config import AppSettings
from app.orchestration.claude_code_config import ClaudeCodeConfig
from app.orchestration.claude_code_runner import claude_code_summary_to_turn_result, run_claude_code
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
parser.add_argument("--mode", choices=["stream", "sleep"], default="stream")
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
