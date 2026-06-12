from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import Task, TaskStatus
from app.orchestration.state_machine import transition
from app.orchestration.task_phase import TaskPhase


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_STRICT_STATE_MACHINE", raising=False)
    db.init_db()
    yield


def _make_task(status=TaskStatus.CREATED) -> Task:
    task = Task(user_goal="nonstrict test", mode="privacy", status=status)
    db.upsert_model("tasks", task)
    return task


def test_invalid_transition_nonstrict_does_not_persist_bad_status():
    # created -> completed remains invalid (created -> failed became legal so
    # pre-planning crashes can terminate instead of leaving zombie tasks).
    task = _make_task(TaskStatus.CREATED)

    result = transition(task, TaskStatus.COMPLETED, actor="UnitTest", strict=False)

    assert result.status == TaskPhase.CREATED
    assert result.phase == TaskPhase.CREATED

    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.CREATED
    assert persisted.phase == TaskPhase.CREATED

    events = db.fetch_many("audit_events", "task_id = ?", (task.id,), limit=10)
    audited = [event for event in events if event.get("event_type") == "task.invalid_transition_audited"]
    assert len(audited) == 1
    assert audited[0]["payload"]["from"] == TaskPhase.CREATED.value
    assert audited[0]["payload"]["to"] == TaskPhase.COMPLETED.value
    assert audited[0]["payload"]["mode"] == "non_strict"

    status_changed = [event for event in events if event.get("event_type") == "task.status_changed"]
    assert status_changed == []
