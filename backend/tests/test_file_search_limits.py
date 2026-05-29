from __future__ import annotations

from pathlib import Path

from app.services import file_service
from app.tools.file_tools import search_by_name


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
