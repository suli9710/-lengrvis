from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import Plan, PlanStep
from app.services.plan_quality_service import risk_annotation_consistency


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def _plan_with_metadata(task_id: str, supplied: str, derived: str) -> Plan:
    step = PlanStep(
        task_id=task_id,
        agent_name="FileAgent",
        tool_name="file.search_by_name",
        description="search",
        args={"query": "x"},
        model_action={
            "action_type": "plan_step",
            "runtime_metadata": {
                "model_supplied_risk_level": supplied,
                "derived_risk_level": derived,
            },
        },
    )
    return Plan(task_id=task_id, goal="g", steps=[step])


def test_risk_consistency_aggregates_match_and_mismatch():
    db.upsert_model("plans", _plan_with_metadata("task_a", "R0_READ_ONLY", "R0_READ_ONLY"))
    db.upsert_model("plans", _plan_with_metadata("task_b", "R0_READ_ONLY", "R2_REVERSIBLE_MODIFY"))

    summary = risk_annotation_consistency()

    assert summary["steps_annotated"] == 2
    assert summary["steps_consistent"] == 1
    assert summary["consistency_rate"] == 0.5
    assert summary["mismatches_by_tool"][0]["tool"] == "file.search_by_name"
    assert summary["mismatches_by_tool"][0]["count"] == 1


def test_risk_consistency_skips_unannotated_steps():
    plan = _plan_with_metadata("task_c", "", "")
    db.upsert_model("plans", plan)

    summary = risk_annotation_consistency()

    assert summary["steps_total"] >= 1
    assert summary["steps_annotated"] == 0
    assert summary["consistency_rate"] is None
