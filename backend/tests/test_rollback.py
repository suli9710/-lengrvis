"""Tests for P1-3 rollback executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import ToolResult
from app.tools import rollback_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def test_rollback_move_back_returns_file(tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-1",
        ok=True,
        rollback_info={"move_back": {"from": str(moved), "to": str(original)}},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert original.exists() and not moved.exists()


def test_rollback_trash_created_file_sends_to_recycle_bin(tmp_path: Path):
    created = tmp_path / "report.md"
    created.write_text("# report", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-2",
        ok=True,
        rollback_info={"trash_created_file": str(created)},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert not created.exists() or outcome.get("detail") == "already absent"


def test_rollback_delete_folder_if_empty(tmp_path: Path):
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    result = ToolResult(
        tool_call_id="call-3",
        ok=True,
        rollback_info={"delete_folder_if_empty": str(folder)},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert not folder.exists()


def test_rollback_delete_folder_if_empty_skipped_when_not_empty(tmp_path: Path):
    folder = tmp_path / "with-stuff"
    folder.mkdir()
    (folder / "child.txt").write_text("x", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-4",
        ok=True,
        rollback_info={"delete_folder_if_empty": str(folder)},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is False
    assert folder.exists()


def test_rollback_restore_backup(tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-5",
        ok=True,
        rollback_info={"backup": str(backup)},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert original.read_text(encoding="utf-8") == "original-content"
    assert not backup.exists()


def test_rollback_restore_from_recycle_bin_requires_user_action(tmp_path: Path):
    result = ToolResult(
        tool_call_id="call-6",
        ok=True,
        rollback_info={"restore_from_recycle_bin": str(tmp_path / "trashed.txt")},
    )
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is False
    assert outcome.get("requires_user_action") is True


def test_rollback_noop_when_no_info():
    result = ToolResult(tool_call_id="call-x", ok=True, rollback_info={})
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert outcome["action"] == "noop"
