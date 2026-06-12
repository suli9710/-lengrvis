from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def _plan_row_created_at(plan_id: str) -> str:
    with db.connect() as conn:
        row = conn.execute("SELECT created_at FROM plans WHERE id = ?", (plan_id,)).fetchone()
    assert row is not None
    return str(row["created_at"])


def test_upsert_plan_preserves_created_at_on_conflict():
    plan = Plan(
        task_id="task_plan_persist",
        goal="first version",
        steps=[
            PlanStep(
                task_id="task_plan_persist",
                agent_name="FileAgent",
                tool_name="file.search_by_name",
                description="search",
                args={"query": "x"},
            )
        ],
    )

    db.upsert_model("plans", plan)
    first_created_at = _plan_row_created_at(plan.id)

    plan.goal = "second version"
    db.upsert_model("plans", plan)

    second_created_at = _plan_row_created_at(plan.id)
    assert second_created_at == first_created_at

    persisted = Plan.model_validate(db.fetch_one("plans", plan.id))
    assert persisted.goal == "second version"
