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
        return {"returncode": 0, "stdout": "## main\n", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["stdout"] == "## main\n"
    assert calls == [{"command": ["git", "status", "--short"], "cwd": tmp_path.resolve(), "shell": False}]


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
    assert worktree["requires_authorized_path"] is True
