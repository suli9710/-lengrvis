from __future__ import annotations

import pytest

from app.core import db
from app.core.schemas import Task, ToolResult


def test_fetch_helpers_reject_unsupported_table(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    with pytest.raises(ValueError, match="Unsupported table"):
        db.fetch_many("tasks; DROP TABLE tasks")
    with pytest.raises(ValueError, match="Unsupported table"):
        db.fetch_one("not_a_data_table", "record_1")


def test_fetch_many_rejects_unsafe_where_clause(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    with pytest.raises(ValueError, match="Unsafe WHERE clause"):
        db.fetch_many("tasks", "id = ?; DROP TABLE tasks", ("task_1",))
    with pytest.raises(ValueError, match="placeholder count"):
        db.fetch_many("tasks", "id = ?", ())
    with pytest.raises(ValueError, match="Unsupported WHERE column"):
        db.fetch_many("tasks", "json_extract = ?", ("task_1",))
    with pytest.raises(ValueError, match="Unsupported WHERE clause"):
        db.fetch_many("tasks", "id = ? OR 1 = 1", ("task_1",))
    with pytest.raises(ValueError, match="Unsupported WHERE clause"):
        db.fetch_many("tasks", "id = ? OR created_at > ?", ("task_1", "2024-01-01T00:00:00Z"))

    assert db.fetch_many("tool_results", "tool_call_id IN (?, ?)", ("call_1", "call_2")) == []


def test_structured_fetch_helpers_validate_columns(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(id="task_fetch_helper", user_goal="Use structured helpers")
    result = ToolResult(id="result_fetch_helper", tool_call_id="call_fetch_helper", ok=True, output={})
    db.upsert_model("tasks", task)
    db.upsert_model("tool_results", result)

    assert db.fetch_many_by_fields("tasks", {"id": task.id}, limit=1) == [task.model_dump(mode="json")]
    assert db.fetch_many_in("tool_results", "tool_call_id", [result.tool_call_id], limit=1) == [
        result.model_dump(mode="json")
    ]
    assert db.fetch_many_in("tool_results", "tool_call_id", []) == []

    with pytest.raises(ValueError, match="Unsupported WHERE column"):
        db.fetch_many_by_fields("tasks", {"data": "{}"})
    with pytest.raises(ValueError, match="Unsupported WHERE column"):
        db.fetch_many_in("tool_results", "data", ["{}"])
