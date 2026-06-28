from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.core.errors import SecurityError
from app.tools import file_tools
from app.tools.tool_abort import ToolAbortedError


def _context(workspace: Path) -> dict[str, list[str]]:
    return {"allowed_directories": [str(workspace)]}


def _create_directory_escape_link(link: Path, target: Path) -> None:
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


def test_write_text_rejects_existing_linked_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / "linked-outside"
    _create_directory_escape_link(link, outside)

    try:
        with pytest.raises(SecurityError):
            file_tools.write_text(
                {"path": str(link / "escaped.txt"), "text": "owned", "dry_run": False},
                _context(workspace),
            )

        assert not (outside / "escaped.txt").exists()
    finally:
        _remove_escape_link(link)


def test_move_file_rejects_existing_linked_destination_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    source = workspace / "source.txt"
    source.write_text("keep inside", encoding="utf-8")
    link = workspace / "linked-outside"
    _create_directory_escape_link(link, outside)

    try:
        with pytest.raises(SecurityError):
            file_tools.move_file(
                {"source": str(source), "destination": str(link / "moved.txt"), "dry_run": False},
                _context(workspace),
            )

        assert source.exists()
        assert not (outside / "moved.txt").exists()
    finally:
        _remove_escape_link(link)


def test_trash_file_rejects_existing_linked_target_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_victim = outside / "victim.txt"
    outside_victim.write_text("do not trash", encoding="utf-8")
    link = workspace / "linked-outside"
    _create_directory_escape_link(link, outside)
    calls: list[str] = []
    monkeypatch.setattr(file_tools, "send2trash", lambda path: calls.append(str(path)))

    try:
        with pytest.raises(SecurityError):
            file_tools.trash_file({"path": str(link / "victim.txt"), "dry_run": False}, _context(workspace))

        assert calls == []
        assert outside_victim.exists()
    finally:
        _remove_escape_link(link)


def test_write_text_rechecks_parent_after_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    parent = workspace / "swap"
    workspace.mkdir()
    outside.mkdir()
    parent.mkdir()
    target = parent / "escaped.txt"
    original_resolve = file_tools.resolve_authorized
    swapped = False

    def resolve_then_swap(path: str | Path, allowed_directories: list[str]) -> Path:
        nonlocal swapped
        resolved = original_resolve(path, allowed_directories)
        if Path(path) == target and not swapped:
            swapped = True
            parent.rmdir()
            _create_directory_escape_link(parent, outside)
        return resolved

    monkeypatch.setattr(file_tools, "resolve_authorized", resolve_then_swap)

    try:
        with pytest.raises(SecurityError):
            file_tools.write_text({"path": str(target), "text": "owned", "dry_run": False}, _context(workspace))

        assert not (outside / "escaped.txt").exists()
    finally:
        _remove_escape_link(parent)


def test_write_text_uses_managed_backup_without_overwriting_legacy_bak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("original", encoding="utf-8")
    legacy_backup = workspace / "note.txt.bak"
    legacy_backup.write_text("keep me", encoding="utf-8")

    result = file_tools.write_text(
        {"path": str(target), "text": "changed", "dry_run": False},
        _context(workspace),
    )

    backup = result["rollback_info"]["backup"]
    backup_path = Path(str(backup["path"]))
    assert backup["managed"] is True
    assert backup_path.parent == (tmp_path / "data" / "file-tool-backups").resolve()
    assert backup_path.read_text(encoding="utf-8") == "original"
    assert legacy_backup.read_text(encoding="utf-8") == "keep me"
    assert target.read_text(encoding="utf-8") == "changed"


def test_edit_text_uses_managed_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("alpha beta", encoding="utf-8")

    result = file_tools.edit_text(
        {"path": str(target), "old_string": "alpha", "new_string": "omega", "dry_run": False},
        _context(workspace),
    )

    backup = result["rollback_info"]["backup"]
    backup_path = Path(str(backup["path"]))
    assert result["ok"] is True
    assert backup["managed"] is True
    assert backup_path.parent == (tmp_path / "data" / "file-tool-backups").resolve()
    assert backup_path.read_text(encoding="utf-8") == "alpha beta"
    assert target.read_text(encoding="utf-8") == "omega beta"


def test_move_file_rechecks_destination_parent_after_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    parent = workspace / "swap"
    workspace.mkdir()
    outside.mkdir()
    parent.mkdir()
    source = workspace / "source.txt"
    source.write_text("keep inside", encoding="utf-8")
    target = parent / "moved.txt"
    original_resolve = file_tools.resolve_authorized
    swapped = False

    def resolve_then_swap(path: str | Path, allowed_directories: list[str]) -> Path:
        nonlocal swapped
        resolved = original_resolve(path, allowed_directories)
        if Path(path) == target and not swapped:
            swapped = True
            parent.rmdir()
            _create_directory_escape_link(parent, outside)
        return resolved

    monkeypatch.setattr(file_tools, "resolve_authorized", resolve_then_swap)

    try:
        with pytest.raises(SecurityError):
            file_tools.move_file(
                {"source": str(source), "destination": str(target), "dry_run": False},
                _context(workspace),
            )

        assert source.exists()
        assert not (outside / "moved.txt").exists()
    finally:
        _remove_escape_link(parent)


def test_trash_file_rechecks_target_parent_after_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    parent = workspace / "swap"
    workspace.mkdir()
    outside.mkdir()
    parent.mkdir()
    inside_victim = parent / "victim.txt"
    inside_victim.write_text("inside", encoding="utf-8")
    outside_victim = outside / "victim.txt"
    outside_victim.write_text("do not trash", encoding="utf-8")
    original_resolve = file_tools.resolve_authorized
    swapped = False
    calls: list[str] = []

    def resolve_then_swap(path: str | Path, allowed_directories: list[str]) -> Path:
        nonlocal swapped
        resolved = original_resolve(path, allowed_directories)
        if Path(path) == inside_victim and not swapped:
            swapped = True
            inside_victim.unlink()
            parent.rmdir()
            _create_directory_escape_link(parent, outside)
        return resolved

    def fake_send2trash(path: str) -> None:
        calls.append(path)
        Path(path).unlink()

    monkeypatch.setattr(file_tools, "resolve_authorized", resolve_then_swap)
    monkeypatch.setattr(file_tools, "send2trash", fake_send2trash)

    try:
        with pytest.raises(SecurityError):
            file_tools.trash_file({"path": str(inside_victim), "dry_run": False}, _context(workspace))

        assert calls == []
        assert outside_victim.exists()
    finally:
        _remove_escape_link(parent)


def test_write_text_aborts_before_persist(tmp_path: Path):
    import threading

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "abort-me.txt"
    abort = threading.Event()
    abort.set()
    context = {**_context(workspace), "_tool_abort_event": abort}

    with pytest.raises(ToolAbortedError):
        file_tools.write_text({"path": str(target), "text": "blocked", "dry_run": False}, context)

    assert not target.exists()
