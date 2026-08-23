from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HYGIENE_SCRIPT = REPO_ROOT / "scripts" / "check_repo_hygiene.ps1"


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is not available")
    return executable


def test_hygiene_rejects_tracked_cursor_runtime_log(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    runtime_log = repo / ".cursor" / "session.log"
    runtime_log.parent.mkdir()
    runtime_log.write_text("C:\\Users\\Alice\\private-path\n", encoding="utf-8")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "fixture"], cwd=repo)

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HYGIENE_SCRIPT),
            "-Root",
            str(repo),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert ".cursor/session.log" in result.stdout


def test_mobile_tool_runtime_caches_are_ignored_and_blocked() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    hygiene = HYGIENE_SCRIPT.read_text(encoding="utf-8")

    for cache_path in ("mobile/.expo/", "mobile/android/.kotlin/"):
        assert cache_path in gitignore
        assert cache_path in hygiene


def test_readme_installs_hashed_development_lock() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "python -m pip install --require-hashes -r requirements-dev-lock.txt" in readme
    assert "python3 -m pip install --require-hashes -r requirements-dev-lock.txt" in readme
