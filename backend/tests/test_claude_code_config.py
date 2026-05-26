from __future__ import annotations

import pytest

from app.config import AppSettings
from app.orchestration.claude_code_config import (
    BLOCKED_ENV_KEYS,
    ClaudeCodeConfig,
    build_claude_code_command,
    build_claude_code_env,
    default_allowed_tools,
    resolve_claude_code_runtime,
    validate_allowed_tools,
)
from app.orchestration.claude_code_runner import parse_claude_code_ndjson_lines


def test_openai_compatible_settings_map_to_claude_code_env() -> None:
    settings = AppSettings(
        base_url="https://llm.example.test/v1",
        api_key="mavris-key",
        model="openai/gpt-5-mini",
    )
    env = build_claude_code_env(
        settings,
        base_env={
            "PATH": "bin",
            "ANTHROPIC_API_KEY": "must-not-leak",
            "ANTHROPIC_AUTH_TOKEN": "must-not-leak",
        },
    )

    assert env["CLAUDE_CODE_USE_OPENAI"] == "1"
    assert env["OPENAI_API_KEY"] == "mavris-key"
    assert env["OPENAI_BASE_URL"] == "https://llm.example.test/v1"
    assert env["OPENAI_MODEL"] == "openai/gpt-5-mini"
    assert env["OPENAI_DEFAULT_SONNET_MODEL"] == "openai/gpt-5-mini"
    assert env["OPENAI_DEFAULT_OPUS_MODEL"] == "openai/gpt-5-mini"
    assert env["OPENAI_DEFAULT_HAIKU_MODEL"] == "openai/gpt-5-mini"
    assert env["OPENAI_SMALL_FAST_MODEL"] == "openai/gpt-5-mini"
    assert all(key not in env for key in BLOCKED_ENV_KEYS)


def test_default_allowed_tools_are_controlled_and_do_not_include_unrestricted_bash() -> None:
    tools = default_allowed_tools()

    assert tools == (
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
    assert validate_allowed_tools(tools) == tools


@pytest.mark.parametrize(
    "tool",
    [
        "Bash(*)",
        "Bash",
        "Bash(git commit:*)",
        "Bash(rm:*)",
        "Bash(npm install:*)",
    ],
)
def test_allowed_tools_reject_unsafe_bash(tool: str) -> None:
    with pytest.raises(ValueError):
        validate_allowed_tools(["Read", tool])


def test_command_uses_stream_json_allowed_tools_and_never_skip_permissions(tmp_path) -> None:
    settings = AppSettings(model="openai/gpt-5")
    config = ClaudeCodeConfig(command=("claude-test",), max_turns=3, env={"OPENAI_API_KEY": "key"})

    command = build_claude_code_command("fix tests", cwd=tmp_path, settings=settings, config=config)

    assert command[:4] == ["claude-test", "--print", "--output-format", "stream-json"]
    assert "--output-format" in command
    assert command[command.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in command
    assert "--bare" in command
    assert "--add-dir" in command
    assert command[command.index("--add-dir") + 1] == str(tmp_path.resolve())
    assert "--permission-mode" in command
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert "--allowedTools" in command
    allowed_tools = command[command.index("--allowedTools") + 1]
    assert "Read,Grep,Glob,Edit,Write" in allowed_tools
    assert "Bash(*)" not in allowed_tools
    assert "--dangerously-skip-permissions" not in command
    assert command[command.index("--max-turns") + 1] == "3"
    assert command[command.index("--model") + 1] == "openai/gpt-5"
    assert command[-1] == "fix tests"


@pytest.mark.parametrize(
    "flag",
    [
        "--dangerously-skip-permissions",
        "--dangerously-skip-permissions=true",
        "--allow-dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions=true",
    ],
)
def test_command_rejects_dangerously_skip_permissions(tmp_path, flag: str) -> None:
    config = ClaudeCodeConfig(extra_args=(flag,), env={"OPENAI_API_KEY": "key"})

    with pytest.raises(ValueError, match="dangerously-skip-permissions"):
        build_claude_code_command("unsafe", cwd=tmp_path, config=config)


def test_unbuilt_vendor_runtime_does_not_fallback_to_path(tmp_path, monkeypatch) -> None:
    fake_path = tmp_path / "bin"
    fake_path.mkdir()
    fake_exe = fake_path / ("claude.cmd")
    fake_exe.write_text("@echo off\necho wrong-runtime\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(fake_path))
    monkeypatch.setenv("MARVIS_CLAUDE_CODE_VENDOR_ROOT", str(tmp_path / "vendor" / "claude-code"))
    monkeypatch.delenv("MARVIS_CLAUDE_CODE_COMMAND", raising=False)

    runtime = resolve_claude_code_runtime()

    assert runtime.command == ()
    assert "MARVIS_CLAUDE_CODE_COMMAND" in runtime.reason


def test_explicit_runtime_command_is_allowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARVIS_CLAUDE_CODE_COMMAND", "python fake_claude.py")

    runtime = resolve_claude_code_runtime()

    assert runtime.command[-2:] == ("python", "fake_claude.py") or runtime.command[-1:] == ("fake_claude.py",)


def test_ndjson_parser_keeps_assistant_tools_result_and_invalid_lines() -> None:
    summary = parse_claude_code_ndjson_lines(
        [
            '{"type":"system","subtype":"init","tools":["Read"]}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Working"},{"type":"tool_use","name":"Read","input":{"file_path":"a.py"}}]}}\n',
            "not-json\n",
            '{"type":"streamlined_text","text":"Done"}\n',
            '{"type":"result","subtype":"success","is_error":false,"result":"All good","num_turns":1}\n',
        ]
    )

    assert summary.final_text == "All good"
    assert summary.result and summary.result["subtype"] == "success"
    assert summary.system_events[0]["subtype"] == "init"
    assert summary.tool_events[0]["name"] == "Read"
    assert summary.invalid_lines == ["not-json"]
    assert summary.is_error is False
