from __future__ import annotations

from pathlib import Path

import pytest

from conftest import import_first, require_attr


FILE_TOOL_MODULES = (
    "backend.tools.files",
    "backend.file_tools",
    "backend.core.file_tools",
    "mavris.tools.files",
)

INDEX_MODULES = (
    "backend.index",
    "backend.search.index",
    "backend.core.index",
    "mavris.index",
)

SYSTEM_MODULES = (
    "backend.system",
    "backend.core.system",
    "mavris.system",
)

API_MODULES = (
    "backend.api",
    "backend.main",
    "mavris.api",
    "mavris.main",
)


def test_file_tool_reads_inside_workspace(workspace: Path):
    module = import_first(FILE_TOOL_MODULES)
    read_file = require_attr(module, ("read_file", "read_workspace_file", "read_text"))

    try:
        result = read_file(root=workspace, workspace_root=workspace, path="notes/safe.txt")
    except TypeError:
        result = read_file(workspace, "notes/safe.txt")

    assert "project notes" in str(result)


def test_index_can_ingest_and_search_text(workspace: Path):
    module = import_first(INDEX_MODULES)
    index_cls_or_func = require_attr(module, ("SearchIndex", "WorkspaceIndex", "create_index", "index_workspace"))

    if isinstance(index_cls_or_func, type):
        index = index_cls_or_func()
        add = getattr(index, "add_document", None) or getattr(index, "index_file", None)
        search = getattr(index, "search", None) or getattr(index, "query", None)
        if add is None or search is None:
            pytest.skip(f"{index_cls_or_func.__name__} lacks add/search APIs")
        add("notes/safe.txt", "project notes about mavris")
        results = search("mavris")
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
