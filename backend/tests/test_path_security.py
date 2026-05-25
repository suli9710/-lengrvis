from __future__ import annotations

from pathlib import Path

import pytest

from conftest import call_with_supported_kwargs, import_first, require_attr


PATH_MODULES = (
    "backend.security.paths",
    "backend.core.path_security",
    "backend.tools.path_security",
    "mavris.security.paths",
    "mavris.core.path_security",
)


@pytest.fixture
def path_api():
    module = import_first(PATH_MODULES)
    validator = require_attr(
        module,
        (
            "resolve_workspace_path",
            "validate_workspace_path",
            "safe_join",
            "ensure_safe_path",
        ),
    )
    return validator


def _resolve(validator, root: Path, candidate: str):
    return call_with_supported_kwargs(
        validator,
        root=root,
        workspace_root=root,
        base_dir=root,
        base=root,
        path=candidate,
        candidate=candidate,
        relative_path=candidate,
    )


@pytest.mark.parametrize(
    "candidate",
    [
        "../outside.txt",
        "..\\outside.txt",
        "notes/../../outside.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "notes/%2e%2e/outside.txt",
    ],
)
def test_rejects_paths_that_escape_workspace(path_api, workspace: Path, candidate: str):
    with pytest.raises((PermissionError, ValueError, OSError)):
        _resolve(path_api, workspace, candidate)


def test_allows_normalized_child_path(path_api, workspace: Path):
    resolved = Path(_resolve(path_api, workspace, "notes/./safe.txt")).resolve()

    assert resolved == (workspace / "notes" / "safe.txt").resolve()
    assert resolved.is_relative_to(workspace.resolve())


def test_rejects_symlink_escape(path_api, workspace: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read\n", encoding="utf-8")

    link = workspace / "linked-outside"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")

    with pytest.raises((PermissionError, ValueError, OSError)):
        _resolve(path_api, workspace, "linked-outside/secret.txt")
