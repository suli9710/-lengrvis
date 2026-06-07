from __future__ import annotations

from pathlib import Path

from app.services import file_service
from app.tools.file_tools import find_duplicates, search_by_name, search_full_text


def test_search_by_name_stops_after_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"needle-{index}.txt").write_text("match\n", encoding="utf-8")

    result = search_by_name(
        {"query": "needle", "limit": 3, "max_scanned": 100},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 3
    assert len(result["results"]) == 3
    assert result["truncated"] is True
    assert result["scanned"] <= 3


def test_search_by_name_stops_after_scan_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"file-{index}.txt").write_text("match\n", encoding="utf-8")

    result = search_by_name(
        {"query": "missing", "limit": 100, "max_scanned": 5},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 0
    assert result["results"] == []
    assert result["truncated"] is True
    assert result["scanned"] == 5


def test_search_full_text_stops_after_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"doc-{index}.txt").write_text("needle in text\n", encoding="utf-8")

    result = search_full_text(
        {"query": "needle", "limit": 3, "max_scanned": 100},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 3
    assert len(result["results"]) == 3
    assert result["truncated"] is True
    assert result["scanned"] <= 3


def test_search_full_text_stops_after_scan_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"doc-{index}.txt").write_text("haystack\n", encoding="utf-8")

    result = search_full_text(
        {"query": "missing", "limit": 100, "max_scanned": 5},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 0
    assert result["results"] == []
    assert result["truncated"] is True
    assert result["scanned"] == 5


def test_search_full_text_respects_file_read_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "early.txt").write_text("needle before the budget\n", encoding="utf-8")
    (workspace / "late.txt").write_text(("x" * 64) + "needle after the budget\n", encoding="utf-8")

    result = search_full_text(
        {
            "query": "needle",
            "limit": 10,
            "max_scanned": 10,
            "max_file_bytes": 32,
            "max_chars_per_file": 32,
        },
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 1
    assert result["truncated"] is True
    assert result["scanned"] == 2
    assert result["results"][0]["path"].endswith("early.txt")


def test_find_duplicates_stops_after_scan_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"file-{index}.bin").write_bytes(f"unique-{index}".encode("utf-8"))

    result = find_duplicates(
        {"limit": 100, "max_scanned": 5},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["duplicates"] == []
    assert result["count"] == 0
    assert result["scanned"] == 5
    assert result["truncated"] is True


def test_find_duplicates_respects_file_size_budget(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "small-a.bin").write_bytes(b"same")
    (workspace / "small-b.bin").write_bytes(b"same")
    (workspace / "large-a.bin").write_bytes(b"x" * 8)
    (workspace / "large-b.bin").write_bytes(b"x" * 8)

    result = find_duplicates(
        {"limit": 10, "max_scanned": 10, "max_file_bytes": 4},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["count"] == 1
    assert result["skipped_large"] == 2
    assert result["truncated"] is True
    paths = result["duplicates"][0]["paths"]
    assert any(path.endswith("small-a.bin") for path in paths)
    assert any(path.endswith("small-b.bin") for path in paths)


def test_file_service_duplicates_returns_live_scan_meta(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("same", encoding="utf-8")
    (workspace / "b.txt").write_text("same", encoding="utf-8")

    monkeypatch.setattr(
        file_service,
        "get_effective_settings",
        lambda: type("Settings", (), {"allowed_directories": [str(workspace)]})(),
    )

    class FakeFTSIndex:
        def duplicates(self) -> list[dict]:
            return []

    monkeypatch.setattr(file_service, "FTSIndex", FakeFTSIndex)

    result = file_service.duplicates()

    assert result["live_duplicates"]
    assert result["live_duplicates_meta"]["scanned"] == 2
    assert result["live_duplicates_meta"]["truncated"] is False


def test_search_files_without_scope_returns_missing_scope(monkeypatch) -> None:
    monkeypatch.setattr(file_service, "get_effective_settings", lambda: type("Settings", (), {"allowed_directories": []})())

    result = file_service.search_files("contract")

    assert result["index_results"] == []
    assert result["name_results"] == []
    assert result["name_search"]["status"] == "missing_scope"


def test_search_files_empty_query_does_not_scan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        file_service,
        "get_effective_settings",
        lambda: type("Settings", (), {"allowed_directories": [str(tmp_path)]})(),
    )

    result = file_service.search_files("   ")

    assert result["index_results"] == []
    assert result["name_results"] == []
    assert result["name_search"]["status"] == "empty_query"
    assert result["name_search"]["scanned"] == 0
