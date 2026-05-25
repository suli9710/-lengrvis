"""Tests for P1-5 smart disk cleanup."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools import system_tools


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # Build a tiny workspace with 3 large + 2 small files.
    big_a = tmp_path / "big_video.mp4"
    big_a.write_bytes(b"0" * (3 * 1024 * 1024))  # 3 MiB
    big_b = tmp_path / "big_archive.zip"
    big_b.write_bytes(b"0" * (4 * 1024 * 1024))  # 4 MiB
    big_c = tmp_path / "big_installer.msi"
    big_c.write_bytes(b"0" * (2 * 1024 * 1024))  # 2 MiB
    small_a = tmp_path / "note.txt"
    small_a.write_text("hi", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "report.pdf").write_bytes(b"0" * (1 * 1024 * 1024))
    return tmp_path


def test_find_large_files_returns_sorted_by_size(workspace: Path):
    result = system_tools.find_large_files(
        {"threshold_mb": 1, "roots": [str(workspace)]}, {"allowed_directories": [str(workspace)]}
    )
    assert result["count"] >= 3
    sizes = [item["size"] for item in result["files"]]
    assert sizes == sorted(sizes, reverse=True)
    assert all(item["category"] in {"media", "archive", "installer", "document", "other"} for item in result["files"])


def test_find_large_files_below_threshold_returns_empty(workspace: Path):
    result = system_tools.find_large_files(
        {"threshold_mb": 100, "roots": [str(workspace)]}, {"allowed_directories": [str(workspace)]}
    )
    assert result["count"] == 0


def test_find_large_files_handles_missing_root(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    result = system_tools.find_large_files(
        {"threshold_mb": 1, "roots": [str(missing)]}, {"allowed_directories": [str(missing)]}
    )
    assert result["files"] == []


def test_cleanup_suggestions_three_buckets(workspace: Path):
    result = system_tools.cleanup_suggestions(
        {"threshold_mb": 1}, {"allowed_directories": [str(workspace)]}
    )
    assert result["ok"] is True
    buckets = result["buckets"]
    assert isinstance(buckets["immediate"], list)
    assert isinstance(buckets["approval"], list)
    assert isinstance(buckets["info_only"], list)
    # info_only should mention the large files from the workspace.
    assert any("big_video" in item["path"] for item in buckets["info_only"])
