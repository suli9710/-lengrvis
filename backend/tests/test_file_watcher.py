"""Tests for the file watcher and incremental indexing."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from app.core import db
from app.indexer import file_watcher as file_watcher_module
from app.indexer.fts_index import FTSIndex


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    # Reset singleton
    file_watcher_module._instance = None
    db.init_db()
    yield
    file_watcher_module._instance = None


@pytest.fixture
def allowed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "watched"
    d.mkdir()
    return d


def test_index_file_appears_in_search(allowed_dir: Path):
    """Index a single text file and verify it appears in search results."""
    test_file = allowed_dir / "hello.txt"
    test_file.write_text("The quick brown fox jumps over the lazy dog", encoding="utf-8")

    fts = FTSIndex()
    result = fts.index_file(str(test_file), [str(allowed_dir)])
    assert result is True

    hits = fts.search("quick brown fox")
    assert len(hits) > 0
    found_paths = [h.get("path", "") for h in hits]
    assert any(str(allowed_dir) in p for p in found_paths)


def test_index_file_skips_unchanged(allowed_dir: Path):
    """Indexing the same file twice (unchanged) should skip on second call."""
    test_file = allowed_dir / "stable.txt"
    test_file.write_text("stable content that does not change", encoding="utf-8")

    fts = FTSIndex()
    first = fts.index_file(str(test_file), [str(allowed_dir)])
    assert first is True

    second = fts.index_file(str(test_file), [str(allowed_dir)])
    assert second is False


def test_remove_file_clears_from_index(allowed_dir: Path):
    """Index a file, then remove it, verify it no longer appears."""
    test_file = allowed_dir / "removeme.txt"
    test_file.write_text("unique removable content zxywvu", encoding="utf-8")

    fts = FTSIndex()
    fts.index_file(str(test_file), [str(allowed_dir)])

    # Verify it is present
    hits = fts.search("unique removable content zxywvu")
    assert len(hits) > 0

    # Remove
    normalized = str(test_file.resolve())
    removed = fts.remove_file(normalized)
    assert removed is True

    # Verify it is gone
    hits_after = fts.search("unique removable content zxywvu")
    assert len(hits_after) == 0


def test_remove_file_returns_false_for_missing():
    """Removing a non-existent file returns False."""
    fts = FTSIndex()
    assert fts.remove_file("C:\\nonexistent\\fake.txt") is False


def test_file_watcher_debounce(allowed_dir: Path):
    """Create a watcher with short debounce, create a file, verify it gets indexed."""

    async def _run():
        from app.indexer.file_watcher import FileWatcher

        watcher = FileWatcher(debounce_seconds=0.1)
        await watcher.start([str(allowed_dir)])

        try:
            # Create a test file
            test_file = allowed_dir / "debounce_test.txt"
            test_file.write_text(
                "debounce watcher test content abcxyz123", encoding="utf-8"
            )

            # Wait for debounce + processing
            await asyncio.sleep(1.5)

            # Verify the file got indexed
            fts = FTSIndex()
            hits = fts.search("debounce watcher test content abcxyz123")
            assert len(hits) > 0
        finally:
            await watcher.stop()

    asyncio.run(_run())


def test_file_watcher_singleton():
    """get_file_watcher returns the same instance."""
    from app.indexer.file_watcher import get_file_watcher

    w1 = get_file_watcher()
    w2 = get_file_watcher()
    assert w1 is w2


def test_file_change_handler_filters_by_suffix():
    """The reusable watchdog handler only emits matching suffixes."""
    events: list[tuple[str, str]] = []
    handler = file_watcher_module._FileChangeHandler(
        lambda path, action: events.append((path, action)),
        suffixes={".md"},
    )

    handler.on_modified(FileModifiedEvent("prompt.md"))
    handler.on_modified(FileModifiedEvent("notes.txt"))

    assert events == [("prompt.md", "upsert")]


def test_watch_directories_backward_compat():
    """The old watch_directories function still works."""
    from app.indexer.file_watcher import watch_directories

    result = watch_directories(["/some/path"])
    assert "watching" in result
    assert result["watching"] == ["/some/path"]
