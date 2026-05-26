from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.tools import developer_tools
from app.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git diff -- backend/tests",
        "git log --oneline",
        "git show --stat",
        "dir",
        "rg ToolDefinition backend/app/tools/schemas.py",
        "where python",
        "whoami",
    ],
)
def test_validate_readonly_shell_allows_inspection_commands(command: str) -> None:
    allowed, reason = developer_tools.validate_readonly_shell(command)

    assert allowed is True
    assert reason == ""


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m test",
        "git branch codex/test",
        "git diff --output=out.patch",
        "git show --output out.txt HEAD",
        "git log --output=log.txt",
        "git checkout -b codex/test",
        "git worktree add ../tmp -b codex/tmp",
        "Remove-Item file.txt",
        "mkdir generated",
        "echo hi > generated.txt",
        "rg query | Out-File result.txt",
        "curl https://example.com",
    ],
)
def test_validate_readonly_shell_rejects_write_or_network_commands(command: str) -> None:
    allowed, reason = developer_tools.validate_readonly_shell(command)

    assert allowed is False
    assert reason


def test_shell_readonly_does_not_execute_rejected_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []

    def fake_run_command(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git commit -m test"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "not read-only" in result["error"].lower()
    assert calls == []


def test_shell_readonly_rejects_git_branch_mutation_and_redirection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []

    def fake_run_command(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)
    context = {"allowed_directories": [str(tmp_path)]}

    branch_result = developer_tools.shell_readonly({"cwd": str(tmp_path), "command": "git branch codex/test"}, context)
    redirect_result = developer_tools.shell_readonly({"cwd": str(tmp_path), "command": "echo hi > generated.txt"}, context)

    assert branch_result["ok"] is False
    assert branch_result["readonly"] is False
    assert "git branch" in branch_result["error"].lower()
    assert redirect_result["ok"] is False
    assert redirect_result["readonly"] is False
    assert "write-like shell token" in redirect_result["error"].lower()
    assert calls == []


def test_shell_readonly_rejects_absolute_paths_outside_authorized_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = developer_tools.shell_readonly(
        {"cwd": str(workspace), "command": f"type {outside}"},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "outside authorized directories" in result["error"].lower()


def test_shell_readonly_executes_allowed_commands_as_readonly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False) -> dict[str, Any]:
        calls.append({"command": command, "cwd": cwd, "shell": shell})
        return {"returncode": 0, "stdout": "## main\n", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["stdout"] == "## main\n"
    assert result["summary"].startswith("Read-only shell command succeeded")
    assert calls == [{"command": calls[0]["command"], "cwd": tmp_path.resolve(), "shell": False}]
    assert calls[0]["command"][0] == "git"
    assert "core.fsmonitor=false" in calls[0]["command"]
    assert "core.hooksPath=" in calls[0]["command"]
    assert "diff.external=" in calls[0]["command"]
    assert calls[0]["command"][-2:] == ["status", "--short"]


def test_shell_readonly_rejects_without_allowed_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_run_command(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly({"command": "git status --short"}, {"allowed_directories": []})

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "no authorized directories" in result["error"].lower()
    assert calls == []


def test_run_command_marks_truncated_stdout_and_stderr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Completed:
        returncode = 0
        stdout = "o" * (developer_tools.COMMAND_STDOUT_LIMIT + 1)
        stderr = "e" * (developer_tools.COMMAND_STDERR_LIMIT + 1)

    monkeypatch.setattr(developer_tools.subprocess, "run", lambda *args, **kwargs: Completed())

    result = developer_tools._run_command(["git", "status"], cwd=tmp_path)

    assert result["stdout"] == "o" * developer_tools.COMMAND_STDOUT_LIMIT
    assert result["stderr"] == "e" * developer_tools.COMMAND_STDERR_LIMIT
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True


def test_diff_preview_returns_summary_and_truncation_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    diff = "diff --git a/a.py b/a.py\n@@\n" + ("+" * developer_tools.DIFF_PREVIEW_LIMIT)
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False) -> dict[str, Any]:  # noqa: ARG001
        calls.append(command)
        return {"returncode": 0, "stdout": diff, "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.diff_preview({"cwd": str(tmp_path)}, {"allowed_directories": [str(tmp_path)]})

    assert result["ok"] is True
    assert result["diff_truncated"] is True
    assert result["summary"].endswith("Truncated.")
    assert len(result["diff"]) == developer_tools.DIFF_PREVIEW_LIMIT
    assert "--no-ext-diff" in calls[0]
    assert "--no-textconv" in calls[0]
    assert "diff.external=" in calls[0]


def test_shell_readonly_wraps_git_diff_with_external_execution_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False) -> dict[str, Any]:  # noqa: ARG001
        calls.append(command)
        return {"returncode": 0, "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly({"cwd": str(tmp_path), "command": "git diff -- backend/tests"}, {"allowed_directories": [str(tmp_path)]})

    assert result["ok"] is True
    assert calls
    command = calls[0]
    assert command[:2] == ["git", "-c"]
    assert "--no-ext-diff" in command
    assert "--no-textconv" in command
    assert command[-2:] == ["--", "backend/tests"]


def test_pytest_inventory_collects_static_test_definitions(tmp_path: Path) -> None:
    test_file = tmp_path / "test_sample.py"
    test_file.write_text(
        "\n".join(
            [
                "def helper():",
                "    pass",
                "",
                "def test_function():",
                "    pass",
                "",
                "class TestFeature:",
                "    def test_method(self):",
                "        pass",
            ]
        ),
        encoding="utf-8",
    )

    result = developer_tools.pytest_inventory({"path": str(tmp_path)}, {"allowed_directories": [str(tmp_path)]})

    assert result["ok"] is True
    assert result["test_count"] == 2
    assert result["file_count"] == 1
    assert result["summary"] == "Static pytest inventory found 2 test(s) in 1 file(s)."
    assert [item["name"] for item in result["test_files"][0]["tests"]] == ["test_function", "TestFeature.test_method"]


def test_worktree_preview_rejects_out_of_workspace_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside-worktree"
    workspace.mkdir()

    with pytest.raises(Exception, match="outside authorized directories"):
        developer_tools.worktree_preview(
            {"cwd": str(workspace), "target_path": str(outside)},
            {"allowed_directories": [str(workspace)]},
        )


def test_registered_developer_tools_are_public_readonly_fast_path_tools() -> None:
    registry = ToolRegistry()

    developer_tools.register(registry)

    shell = registry.get("dev.shell_readonly")
    public = shell.to_public_dict(include_schema=True)
    inventory = registry.get("dev.pytest_inventory").to_public_dict(include_schema=True)
    worktree = registry.get("dev.worktree_preview").to_public_dict(include_schema=True)

    assert public["permission_mode"] == "auto_readonly"
    assert public["read_only"] is True
    assert public["concurrency_safe"] is True
    assert public["trust_tier"] == "builtin"
    assert public["origin"] == "builtin"
    assert public["fast_path_eligible"] is True
    assert "shell" in public["capabilities"]
    assert public["effects"] == ["read", "inspect"]
    assert public["input_schema"]["required"] == ["command"]
    assert "tests" in inventory["capabilities"]
    assert inventory["read_only"] is True
    assert inventory["effects"] == ["read", "inspect"]
    assert inventory["requires_authorized_path"] is True
    assert worktree["requires_authorized_path"] is True
