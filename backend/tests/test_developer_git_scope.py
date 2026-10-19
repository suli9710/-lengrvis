from __future__ import annotations

import json
import subprocess

import pytest

from app.tools import developer_tools


@pytest.fixture
def scoped_repository(tmp_path):
    repo = tmp_path / "repo"
    scope = repo / "scope"
    sibling = repo / "sibling"
    scope.mkdir(parents=True)
    sibling.mkdir()
    local = scope / "local.txt"
    private = sibling / "private.txt"
    local.write_text("initial local\n", encoding="utf-8")
    private.write_text("OUTSIDE_INITIAL_SENTINEL\n", encoding="utf-8")
    for args in (
        ["init"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    local.write_text("LOCAL_CHANGE\n", encoding="utf-8")
    private.write_text("OUTSIDE_PRIVATE_SENTINEL\n", encoding="utf-8")
    return repo, scope


@pytest.mark.parametrize(
    "pathspec", [":(top)sibling", ":/sibling", ":(exclude)unrelated", ":!unrelated", ":^unrelated", ":"]
)
@pytest.mark.parametrize("entrypoint", ["status", "diff", "preview"])
def test_git_magic_pathspec_cannot_escape_authorized_subdirectory(scoped_repository, pathspec, entrypoint):
    _, scope = scoped_repository
    context = {"allowed_directories": [str(scope)]}
    if entrypoint == "preview":
        result = developer_tools.diff_preview({"cwd": str(scope), "pathspec": pathspec}, context)
    else:
        result = developer_tools.shell_readonly(
            {"cwd": str(scope), "command": f"git {entrypoint} -- {pathspec}"}, context
        )
    assert result["ok"] is False
    assert "pathspec" in result["error"].lower()
    assert "OUTSIDE_" not in json.dumps(result)


@pytest.mark.parametrize("command", ["git status --short", "git diff", "git diff -- local.txt"])
def test_git_worktree_inspection_only_returns_authorized_changes(scoped_repository, command):
    _, scope = scoped_repository
    result = developer_tools.shell_readonly(
        {"cwd": str(scope), "command": command}, {"allowed_directories": [str(scope)]}
    )
    assert result["ok"] is True
    assert "local.txt" in result["stdout"]
    assert "OUTSIDE_" not in result["stdout"]
    assert "sibling" not in result["stdout"]


@pytest.mark.parametrize("command", ["git show HEAD:sibling/private.txt", "git show", "git log -p"])
def test_git_object_and_history_queries_require_authorization_for_repository(scoped_repository, command):
    repo, scope = scoped_repository
    result = developer_tools.shell_readonly(
        {"cwd": str(scope), "command": command}, {"allowed_directories": [str(scope)]}
    )
    assert result["ok"] is False
    assert "OUTSIDE_" not in json.dumps(result)
    allowed = developer_tools.shell_readonly(
        {"cwd": str(scope), "command": command}, {"allowed_directories": [str(repo)]}
    )
    assert allowed["ok"] is True


def test_git_blob_diff_requires_repository_authorization(scoped_repository):
    repo, scope = scoped_repository
    private_blob = subprocess.run(
        ["git", "rev-parse", "HEAD:sibling/private.txt"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    local_blob = subprocess.run(
        ["git", "rev-parse", "HEAD:scope/local.txt"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    result = developer_tools.shell_readonly(
        {"cwd": str(scope), "command": f"git diff {private_blob} {local_blob}"},
        {"allowed_directories": [str(scope)]},
    )
    assert result["ok"] is False
    assert "OUTSIDE_" not in json.dumps(result)
