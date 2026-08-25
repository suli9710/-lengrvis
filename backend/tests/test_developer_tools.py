from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app.core.subprocess_output import decode_process_output
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import SafetyVerdict
from app.tools import developer_tools
from app.tools.registry import ToolRegistry
from app.tools.tool_abort import ToolAbortedError


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git diff -- backend/tests",
        "git log --oneline",
        "git show --stat",
        "dir",
        "select-string ToolDefinition backend/app/tools/schemas.py",
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
        "rg ToolDefinition backend/app/tools/schemas.py",
        "rg ToolDefinition --pre python",
        "rg ToolDefinition --pre=python",
        "rg ToolDefinition --pre-glob *.py",
        "rg ToolDefinition --hostname-bin hostname",
        "rg ToolDefinition --search-zip",
        "rg -f patterns.txt backend",
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


def test_shell_readonly_rejects_ripgrep_pre_without_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []

    def fake_run_command(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": 'rg needle --pre "python -c print(1)"'},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "not in the read-only allowlist" in result["error"]
    assert calls == []


def test_shell_readonly_rejects_git_branch_mutation_and_redirection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[Any] = []

    def fake_run_command(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)
    context = {"allowed_directories": [str(tmp_path)]}

    branch_result = developer_tools.shell_readonly({"cwd": str(tmp_path), "command": "git branch codex/test"}, context)
    redirect_result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "echo hi > generated.txt"}, context
    )

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


def test_shell_readonly_reads_absolute_paths_inside_authorized_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "inside.txt"
    inside.write_text("authorized content", encoding="utf-8")

    result = developer_tools.shell_readonly(
        {"cwd": str(workspace), "command": f"type {inside}"},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["stdout"] == "authorized content"


def test_shell_readonly_executes_allowed_commands_as_readonly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    trusted_git = _trusted_test_executable(tmp_path, "git.exe")

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False, **kwargs: Any) -> dict[str, Any]:
        calls.append({"command": command, "cwd": cwd, "shell": shell})
        return {
            "returncode": 0,
            "stdout": "## main\n",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)
    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: str(trusted_git))

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["stdout"] == "## main\n"
    assert result["summary"].startswith("Read-only shell command succeeded")
    assert calls == [{"command": calls[0]["command"], "cwd": tmp_path.resolve(), "shell": False}]
    command = calls[0]["command"]
    assert Path(command[0]).name.casefold().startswith("git")
    assert Path(command[0]).is_absolute()
    assert "core.fsmonitor=false" in command
    assert "core.hooksPath=" in command
    assert "diff.external=" in command
    assert calls[0]["command"][-2:] == ["status", "--short"]


def test_shell_readonly_executes_builtins_without_process_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False) -> dict[str, Any]:
        calls.append({"command": command, "cwd": cwd, "shell": shell})
        return {
            "returncode": 0,
            "stdout": "hello\r\n",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "echo hello"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["stdout"] == "hello\n"
    assert calls == []


def test_shell_readonly_rejects_workspace_hijacked_external_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_git = tmp_path / "git.exe"
    fake_git.write_text("not really git", encoding="utf-8")
    calls: list[Any] = []

    monkeypatch.setattr(developer_tools, "_safe_command_env", lambda: {"PATH": str(tmp_path)})
    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: str(fake_git))
    monkeypatch.setattr(developer_tools, "_run_command", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "authorized workspace" in result["error"].lower()
    assert calls == []


def test_shell_readonly_fails_closed_when_trusted_executable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Any] = []

    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: None)
    monkeypatch.setattr(developer_tools, "_run_command", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["readonly"] is False
    assert "trusted executable for 'git' was not found" in result["error"].lower()
    assert calls == []


def test_shell_readonly_rejects_external_executable_inside_any_allowed_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed_tools = tmp_path / "allowed-tools"
    allowed_tools.mkdir()
    fake_git = allowed_tools / "git.exe"
    fake_git.write_text("not really git", encoding="utf-8")
    calls: list[Any] = []

    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: str(fake_git))
    monkeypatch.setattr(developer_tools, "_run_command", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = developer_tools.diff_preview(
        {"cwd": str(workspace), "pathspec": "."},
        {"allowed_directories": [str(workspace), str(allowed_tools)]},
    )

    assert result["ok"] is False
    assert "authorized workspace" in result["error"].lower()
    assert calls == []


def test_shell_readonly_normalizes_executable_tokens_before_trusted_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    trusted_git = _trusted_test_executable(tmp_path, "GIT.EXE")

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False, **kwargs: Any) -> dict[str, Any]:
        calls.append({"command": command, "cwd": cwd, "shell": shell})
        return {
            "returncode": 0,
            "stdout": "## main\n",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(developer_tools.shutil, "which", lambda command, **kwargs: str(trusted_git))
    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "Git.EXE status --short"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert calls[0]["command"][0] == str(trusted_git.resolve(strict=False))
    assert calls[0]["command"][-2:] == ["status", "--short"]


def _trusted_test_executable(workspace: Path, name: str) -> Path:
    trusted_dir = workspace.parent / f"{workspace.name}-trusted-bin"
    trusted_dir.mkdir(exist_ok=True)
    executable = trusted_dir / name
    executable.write_text("trusted test executable", encoding="utf-8")
    return executable


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("dir", "readonly-sentinel.txt"),
        ("echo hello", "hello"),
        ("type readonly-sentinel.txt", "readonly shell sentinel"),
    ],
)
def test_shell_readonly_executes_readonly_builtins(tmp_path: Path, command: str, expected: str) -> None:
    sentinel = tmp_path / "readonly-sentinel.txt"
    sentinel.write_text("readonly shell sentinel", encoding="utf-8")

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": command},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["readonly"] is True
    assert result["returncode"] == 0
    assert expected in result["stdout"]


@pytest.mark.parametrize(
    "command",
    [
        "pytest backend/tests/test_developer_tools.py",
        "python -m pytest backend/tests/test_developer_tools.py",
        "npm test",
        "pnpm run test",
        "pytest -c backend/pytest.ini backend/tests",
        "pytest -q --maxfail=1 -k expr -m marker",
    ],
)
def test_validate_test_command_allows_controlled_test_commands(command: str) -> None:
    allowed, reason = developer_tools.validate_test_command(command)

    assert allowed is True
    assert reason == ""


@pytest.mark.parametrize(
    "command",
    [
        "python script.py",
        "pytest --junitxml report.xml",
        "pytest --override-ini=cache_dir=..\\outside-cache",
        "pytest -ocache_dir=..\\outside-cache",
        "pytest --watch",
        "npm run build",
        "pytest tests > out.txt",
        "pytest -o addopts=--rootdir=C:\\outside",
        "pytest -oaddopts=--rootdir=C:\\outside",
        "pytest --override-ini=addopts=--rootdir=C:\\outside",
    ],
)
def test_validate_test_command_rejects_uncontrolled_commands(command: str) -> None:
    allowed, reason = developer_tools.validate_test_command(command)

    assert allowed is False
    assert reason


@pytest.mark.parametrize(
    "command",
    [
        "pytest --rootdir=..\\evil",
        "pytest --rootdir=../evil",
        "pytest -c..\\evil\\pytest.ini",
        "pytest --confcutdir=..\\evil",
    ],
)
def test_validate_test_command_rejects_flag_value_path_traversal(command: str) -> None:
    allowed, reason = developer_tools.validate_test_command(command)

    assert allowed is False
    assert reason


def test_validate_test_command_rejects_rootdir_outside_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "evil"
    outside.mkdir()

    allowed, reason = developer_tools.validate_test_command(
        f"pytest --rootdir={outside}", allowed_directories=[str(workspace)]
    )

    assert allowed is False
    assert "outside authorized directories" in reason.lower()


def test_validate_test_command_allows_rootdir_inside_allowed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    sub = workspace / "pkg"
    sub.mkdir(parents=True)

    allowed, reason = developer_tools.validate_test_command(
        f"pytest --rootdir={sub}", allowed_directories=[str(workspace)]
    )

    assert allowed is True
    assert reason == ""


def test_diff_preview_rejects_pathspec_traversal(tmp_path: Path) -> None:
    result = developer_tools.diff_preview(
        {"cwd": str(tmp_path), "pathspec": "../../secret"},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["error"]


def test_test_run_allows_relative_path_with_sensitive_filename(tmp_path: Path) -> None:
    # containment-only symlink check must NOT reject legitimate files whose name
    # merely contains words like "password" (resolve_authorized would).
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_password.py").write_text("def test_x():\n    pass\n", encoding="utf-8")

    result = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "pytest tests/test_password.py", "dry_run": True},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True


def test_diff_preview_rejects_symlinked_pathspec_escaping_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform/session")

    result = developer_tools.diff_preview(
        {"cwd": str(workspace), "pathspec": "link/secret.txt"},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["ok"] is False
    assert "outside the authorized workspace" in result["error"].lower()


def _create_directory_escape_link(link: Path, target: Path) -> None:
    import os
    import subprocess

    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation failed: {completed.stderr or completed.stdout}")


def _remove_escape_link(link: Path) -> None:
    if link.is_symlink():
        link.unlink()
    elif hasattr(link, "is_junction") and link.is_junction():
        link.rmdir()


@pytest.mark.parametrize("tool_fn", [developer_tools.glob_files, developer_tools.grep_files])
def test_rglob_tools_ignore_directory_escape_links(tmp_path: Path, tool_fn) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret-token", encoding="utf-8")
    link = workspace / "linked-outside"
    _create_directory_escape_link(link, outside)

    try:
        if tool_fn is developer_tools.grep_files:
            result = tool_fn(
                {"path": str(workspace), "query": "outside-secret-token", "pattern": "*.txt"},
                {"allowed_directories": [str(workspace)]},
            )
            assert result["ok"] is True
            assert result["count"] == 0
            assert result["results"] == []
        else:
            result = tool_fn(
                {"path": str(workspace), "pattern": "**/secret.txt"},
                {"allowed_directories": [str(workspace)]},
            )
            assert result["ok"] is True
            assert result["count"] == 0
            assert result["matches"] == []
    finally:
        _remove_escape_link(link)


def test_pytest_inventory_ignores_directory_escape_links(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "test_escape.py").write_text(
        "def test_outside():\n    assert False\n",
        encoding="utf-8",
    )
    link = workspace / "linked-outside"
    _create_directory_escape_link(link, outside)

    try:
        result = developer_tools.pytest_inventory(
            {"path": str(workspace), "pattern": "test_*.py"},
            {"allowed_directories": [str(workspace)]},
        )
        assert result["ok"] is True
        assert result["file_count"] == 0
        assert result["test_files"] == []
    finally:
        _remove_escape_link(link)


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

    monkeypatch.setattr(developer_tools, "run_process_tree", lambda *args, **kwargs: Completed())

    result = developer_tools._run_command(["git", "status"], cwd=tmp_path)

    assert result["stdout"] == "o" * developer_tools.COMMAND_STDOUT_LIMIT
    assert result["stderr"] == "e" * developer_tools.COMMAND_STDERR_LIMIT
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True


def test_run_command_decodes_non_utf8_windows_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Completed:
        returncode = 0
        stdout = "你好".encode("gbk")
        stderr = "错误".encode("gbk")

    monkeypatch.setattr(developer_tools, "run_process_tree", lambda *args, **kwargs: Completed())

    result = developer_tools._run_command(["where", "python"], cwd=tmp_path)

    assert result["stdout"] == "你好"
    assert result["stderr"] == "错误"


def test_decode_process_output_detects_utf16_without_bom() -> None:
    text = '{"Name":"App"}'

    assert decode_process_output(text.encode("utf-16le")) == text
    assert decode_process_output(text.encode("utf-16be")) == text


def test_dev_test_run_dry_run_does_not_execute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(developer_tools, "run_process_tree", lambda *args, **kwargs: calls.append((args, kwargs)))

    result = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "pytest backend/tests", "timeout_seconds": 7, "dry_run": True},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["would_execute"] is True
    assert result["timeout_seconds"] == 7
    assert calls == []


def test_dev_test_run_aborts_before_foreground_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Any] = []
    abort = threading.Event()
    abort.set()
    monkeypatch.setattr(developer_tools, "run_process_tree", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ToolAbortedError):
        developer_tools.test_run(
            {"cwd": str(tmp_path), "command": "pytest backend/tests", "timeout_seconds": 7},
            {"allowed_directories": [str(tmp_path)], "_tool_abort_event": abort},
        )

    assert calls == []


def test_dev_test_run_persists_output_and_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Timeout:
        stdout = "partial stdout"
        stderr = "partial stderr"

    def fake_run(*args: Any, **kwargs: Any):  # noqa: ANN202
        raise developer_tools.subprocess.TimeoutExpired(
            cmd=args[0], timeout=kwargs["timeout"], output=Timeout.stdout, stderr=Timeout.stderr
        )

    monkeypatch.setattr(developer_tools, "run_process_tree", fake_run)

    result = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "pytest backend/tests", "timeout_seconds": 1},
        {"allowed_directories": [str(tmp_path)]},
    )

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert Path(result["stdout_path"]).read_text(encoding="utf-8") == "partial stdout"
    assert Path(result["stderr_path"]).read_text(encoding="utf-8") == "partial stderr"


def test_dev_test_run_aborts_before_persisting_output(tmp_path: Path) -> None:
    abort = threading.Event()
    abort.set()

    with pytest.raises(ToolAbortedError):
        developer_tools._persist_test_output(
            tmp_path / "runs",
            "stdout",
            "stderr",
            abort_context={"_tool_abort_event": abort},
        )

    assert not (tmp_path / "runs").exists()


def test_dev_test_run_background_returns_task_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeTask:
        id = "bgtask_test"

        def snapshot(self, *, preview_chars: int = 4000):  # noqa: ARG002, ANN202
            return {
                "task_id": self.id,
                "status": "running",
                "command": ["pytest"],
                "cwd": str(tmp_path),
                "returncode": None,
                "stdout_path": str(tmp_path / "out.log"),
                "stderr_path": str(tmp_path / "err.log"),
                "stdout_preview": "",
                "stderr_preview": "",
            }

    monkeypatch.setattr(developer_tools, "start_background_process", lambda *args, **kwargs: FakeTask())
    monkeypatch.setattr(
        developer_tools,
        "background_task_status",
        lambda task_id: {"ok": True, "task_id": task_id, "status": "succeeded"},
    )

    started = developer_tools.test_run(
        {"cwd": str(tmp_path), "command": "pytest backend/tests", "background": True},
        {"allowed_directories": [str(tmp_path)]},
    )
    status = developer_tools.test_status({"task_id": "bgtask_test"}, {"allowed_directories": [str(tmp_path)]})

    assert started["ok"] is True
    assert started["background"] is True
    assert started["task_id"] == "bgtask_test"
    assert status["ok"] is True
    assert status["summary"] == "Background test run bgtask_test is succeeded."


def test_diff_preview_returns_summary_and_truncation_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    diff = "diff --git a/a.py b/a.py\n@@\n" + ("+" * developer_tools.DIFF_PREVIEW_LIMIT)
    calls: list[list[str]] = []
    trusted_git = _trusted_test_executable(tmp_path, "git.exe")

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        calls.append(command)
        return {"returncode": 0, "stdout": diff, "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)
    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: str(trusted_git))

    result = developer_tools.diff_preview({"cwd": str(tmp_path)}, {"allowed_directories": [str(tmp_path)]})

    assert result["ok"] is True
    assert result["diff_truncated"] is True
    assert result["summary"].endswith("Truncated.")
    assert len(result["diff"]) == developer_tools.DIFF_PREVIEW_LIMIT
    assert "--no-ext-diff" in calls[0]
    assert "--no-textconv" in calls[0]
    assert "diff.external=" in calls[0]


def test_shell_readonly_wraps_git_diff_with_external_execution_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    trusted_git = _trusted_test_executable(tmp_path, "git.exe")

    def fake_run_command(command: list[str], *, cwd: Path, shell: bool = False, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        calls.append(command)
        return {"returncode": 0, "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}

    monkeypatch.setattr(developer_tools, "_run_command", fake_run_command)
    monkeypatch.setattr(developer_tools.shutil, "which", lambda *args, **kwargs: str(trusted_git))

    result = developer_tools.shell_readonly(
        {"cwd": str(tmp_path), "command": "git diff -- backend/tests"}, {"allowed_directories": [str(tmp_path)]}
    )

    assert result["ok"] is True
    assert calls
    command = calls[0]
    assert Path(command[0]).name.casefold().startswith("git")
    assert Path(command[0]).is_absolute()
    assert command[1] == "-c"
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
    test_run = registry.get("dev.test_run").to_public_dict(include_schema=True)

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
    assert test_run["risk_level"] == "R2_REVERSIBLE_MODIFY"
    assert test_run["permission_mode"] == "ask_on_write"
    assert test_run["read_only"] is False
    assert test_run["supports_dry_run"] is True
    assert test_run["fast_path_eligible"] is False
    assert "execute_test" in test_run["effects"]


def test_dev_test_run_requires_approval_as_local_code_execution() -> None:
    registry = ToolRegistry()
    developer_tools.register(registry)
    tool = registry.get("dev.test_run")

    review = PolicyEngine().review_tool_call(
        "task_tests",
        "step_tests",
        tool.name,
        {"command": "pytest backend/tests"},
        tool.risk_level,
        tool_definition=tool,
    )

    assert review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL
    assert review.risk_level == tool.risk_level
