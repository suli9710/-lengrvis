from __future__ import annotations

import pytest

from app.core import db


def test_fetch_helpers_reject_unsupported_table(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    with pytest.raises(ValueError, match="Unsupported table"):
        db.fetch_many("tasks; DROP TABLE tasks")
    with pytest.raises(ValueError, match="Unsupported table"):
        db.fetch_one("not_a_data_table", "record_1")


def test_fetch_many_rejects_unsafe_where_clause(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    with pytest.raises(ValueError, match="Unsafe WHERE clause"):
        db.fetch_many("tasks", "id = ?; DROP TABLE tasks", ("task_1",))
    with pytest.raises(ValueError, match="placeholder count"):
        db.fetch_many("tasks", "id = ?", ())
