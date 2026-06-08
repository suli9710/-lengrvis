from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import import_first, require_attr

from app.tools.file_tools import read_text as app_read_text


FILE_TOOL_MODULES = (
    "app.tools.file_tools",
)

INDEX_MODULES = (
    "app.indexer.fts_index",
)

SYSTEM_MODULES = (
    "app.services.system_service",
)

API_MODULES = (
    "app.main",
)


def test_file_tool_reads_inside_workspace(workspace: Path):
    assert app_read_text.__module__ == "app.tools.file_tools"

    result = app_read_text(
        {"path": str(workspace / "notes" / "safe.txt")},
        {"allowed_directories": [str(workspace)]},
    )

    assert result["ok"] is True
    assert "project notes" in result["text"]


def test_index_can_ingest_and_search_text(workspace: Path):
    module = import_first(INDEX_MODULES)
    index_cls_or_func = require_attr(module, ("SearchIndex", "WorkspaceIndex", "create_index", "index_workspace"))

    if isinstance(index_cls_or_func, type):
        index = index_cls_or_func()
        add = getattr(index, "add_document", None) or getattr(index, "index_file", None)
        search = getattr(index, "search", None) or getattr(index, "query", None)
        if add is None or search is None:
            pytest.skip(f"{index_cls_or_func.__name__} lacks add/search APIs")
        add("notes/safe.txt", "project notes about lengrvis")
        results = search("lengrvis")
    else:
        results = index_cls_or_func(workspace)

    assert results


def test_system_health_shape():
    module = import_first(SYSTEM_MODULES)
    health = require_attr(module, ("health", "health_check", "get_health", "status"))

    result = health()

    if isinstance(result, dict):
        assert result.get("status") in {"ok", "healthy", "ready"}
    else:
        assert str(result).lower() in {"ok", "healthy", "ready"}


def test_api_app_exposes_health_route_when_available():
    module = import_first(API_MODULES)
    app_or_factory = require_attr(module, ("app", "create_app", "build_app"))
    app = app_or_factory() if callable(app_or_factory) and not hasattr(app_or_factory, "routes") else app_or_factory

    if hasattr(app, "test_client"):
        client = app.test_client()
        response = client.get("/health")
        assert response.status_code == 200
        return

    if hasattr(app, "routes"):
        routes = {getattr(route, "path", None) for route in app.routes}
        assert "/health" in routes or "/api/health" in routes
        return

    pytest.skip("API object is present but no supported smoke-test interface was found")


def test_index_status_reports_last_update_and_retry_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))

    from app.core import db
    from app.core.audit import record
    from app.core.schemas import IndexedFile
    from app.indexer.fts_index import FTSIndex

    db.init_db()
    indexed = IndexedFile(
        path=str(tmp_path / "workspace" / "notes.txt"),
        normalized_path=str(tmp_path / "workspace" / "notes.txt"),
        name="notes.txt",
        extension=".txt",
        size=42,
        sha256="abc123",
        modified_at="1700000000",
        indexed_at="2026-01-02T03:04:05+00:00",
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO indexed_files
            (id, normalized_path, data, sha256, name, extension, size, modified_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                indexed.id,
                indexed.normalized_path,
                indexed.model_dump_json(),
                indexed.sha256,
                indexed.name,
                indexed.extension,
                indexed.size,
                indexed.modified_at,
                indexed.indexed_at,
            ),
        )
    record(
        "index.embedding_failed",
        "FTSIndex",
        {"path": indexed.normalized_path, "error": "embedding service offline"},
    )

    status = FTSIndex().status([str(tmp_path / "workspace")])

    assert status["status"] == "degraded"
    assert status["files_indexed"] == 1
    assert status["last_indexed_at"] == "2026-01-02T03:04:05+00:00"
    assert status["latest_failure"]["message"] == "embedding service offline"
    assert status["latest_failure"]["path_label"] == "notes.txt"
    assert "Retry rebuild" in status["retry_hint"]


def test_index_status_is_scoped_to_authorized_directories_and_redacts_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))

    from app.core import db
    from app.core.audit import record
    from app.core.schemas import IndexedFile
    from app.indexer.fts_index import FTSIndex

    db.init_db()
    workspace = tmp_path / "workspace"
    outside = tmp_path / "private"
    scoped = IndexedFile(
        path=str(workspace / "notes.txt"),
        normalized_path=str(workspace / "notes.txt"),
        name="notes.txt",
        extension=".txt",
        size=42,
        sha256="scoped",
        modified_at="1700000000",
        indexed_at="2026-01-02T03:04:05+00:00",
    )
    unscoped = IndexedFile(
        path=str(outside / "secret.txt"),
        normalized_path=str(outside / "secret.txt"),
        name="secret.txt",
        extension=".txt",
        size=99,
        sha256="unscoped",
        modified_at="1700000001",
        indexed_at="2026-01-02T04:04:05+00:00",
    )
    with db.connect() as conn:
        for indexed in (scoped, unscoped):
            conn.execute(
                """
                INSERT INTO indexed_files
                (id, normalized_path, data, sha256, name, extension, size, modified_at, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indexed.id,
                    indexed.normalized_path,
                    indexed.model_dump_json(),
                    indexed.sha256,
                    indexed.name,
                    indexed.extension,
                    indexed.size,
                    indexed.modified_at,
                    indexed.indexed_at,
                ),
            )
    record(
        "index.embedding_failed",
        "FTSIndex",
        {"path": unscoped.normalized_path, "error": f"token failure at {unscoped.normalized_path}"},
    )

    status = FTSIndex().status([str(workspace)])
    rendered = json.dumps(status)

    assert status["status"] == "ready"
    assert status["files_indexed"] == 1
    assert status["bytes_indexed"] == 42
    assert status["latest_failure"] is None
    assert "private" not in rendered
    assert "secret.txt" not in rendered


def test_index_status_uses_latest_authorized_failure_when_newer_failure_is_outside_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))

    from app.core import db
    from app.core.audit import record
    from app.core.schemas import IndexedFile
    from app.indexer.fts_index import FTSIndex

    db.init_db()
    workspace = tmp_path / "workspace"
    outside = tmp_path / "private"
    indexed = IndexedFile(
        path=str(workspace / "notes.txt"),
        normalized_path=str(workspace / "notes.txt"),
        name="notes.txt",
        extension=".txt",
        size=42,
        sha256="scoped",
        modified_at="1700000000",
        indexed_at="2026-01-02T03:04:05+00:00",
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO indexed_files
            (id, normalized_path, data, sha256, name, extension, size, modified_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                indexed.id,
                indexed.normalized_path,
                indexed.model_dump_json(),
                indexed.sha256,
                indexed.name,
                indexed.extension,
                indexed.size,
                indexed.modified_at,
                indexed.indexed_at,
            ),
        )
    record("index.embedding_failed", "FTSIndex", {"path": indexed.normalized_path, "error": "embedding service offline"})
    for index in range(30):
        record("index.embedding_failed", "FTSIndex", {"path": str(outside / f"secret-{index}.txt"), "error": "outside failure"})

    status = FTSIndex().status([str(workspace)])

    assert status["status"] == "degraded"
    assert status["latest_failure"]["path_label"] == "notes.txt"
    assert status["latest_failure"]["message"] == "embedding service offline"


def test_file_search_response_includes_index_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))

    from app.config import AppSettings
    from app.services import file_service

    monkeypatch.setattr(file_service, "get_effective_settings", lambda: AppSettings(allowed_directories=[]))

    result = file_service.search_files("needle")

    assert result["index_status"]["status"] == "missing_scope"
    assert result["index_status"]["files_indexed"] == 0
