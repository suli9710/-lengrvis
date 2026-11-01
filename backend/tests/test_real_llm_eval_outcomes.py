"""Outcome evidence must check the sandbox state without changing safety gates."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi import FastAPI
from scripts import real_llm_eval_outcomes as outcomes
from scripts import run_real_llm_eval as harness

from app.core import db
from app.core.schemas import Task, ToolCall, ToolResult
from app.policy.risk import RiskLevel


def test_evaluation_checks_reviewed_output_and_file_state_before_cleanup(monkeypatch):
    reviewed = True

    def run_entry(client, task, message, expect, timeout_seconds, *, mode=None):
        stored = Task(user_goal="fixture search", mode="efficiency")
        db.upsert_model("tasks", stored)
        call = ToolCall(
            task_id=stored.id,
            step_id="step",
            tool_name="file.search_by_name",
            risk_level=RiskLevel.R0_READ_ONLY,
            status="committed",
        )
        db.upsert_model("tool_calls", call)
        db.upsert_model(
            "tool_results",
            ToolResult(
                tool_call_id=call.id,
                ok=True,
                output={"name": "private-fixture.txt"},
                runtime_review_completed=reviewed,
                runtime_review_verdict="allow" if reviewed else "",
            ),
        )
        return {"phase": "completed", "phase_ok": True, "_outcome_task_id": stored.id}

    monkeypatch.setattr(harness, "_golden_app", FastAPI)
    monkeypatch.setattr(harness, "_run_runs_entry", run_entry)
    task = {
        "id": "postconditions",
        "title": "fixture",
        "category": "read",
        "entry": "runs",
        "message": "find fixture",
        "fixtures": {"private-fixture.txt": "private body"},
        "expect": {
            "phase": ["completed"],
            "files_exist": ["private-fixture.txt"],
            "tool_output_contains": [{"tool": "file.search_by_name", "text": "private-fixture.txt"}],
        },
    }
    record = harness._evaluate_task(task, 0.1)
    assert record["outcome_evidence"]["verified"] is True
    assert record["outcome_evidence"]["checks_passed"] == 2
    assert record["evaluation_passed"] is True
    assert record["usage_evidence"]["total_cost_usd"] is None
    assert "_outcome_task_id" not in record
    assert "private-fixture" not in json.dumps(record)
    assert "private body" not in json.dumps(record)

    reviewed = False
    unreviewed = harness._evaluate_task(task, 0.1)
    assert unreviewed["outcome_evidence"]["verified"] is False
    assert unreviewed["error_code"] == "RESULT_POSTCONDITION_UNVERIFIED"
    reviewed = True
    task["expect"]["files_exist"] = ["missing.txt"]
    failed = harness._evaluate_task(task, 0.1)
    assert failed["phase_ok"] is True
    assert failed["evaluation_passed"] is False
    assert failed["error_code"] == "RESULT_POSTCONDITION_UNVERIFIED"


def test_incomplete_or_unreviewed_results_cannot_be_verified(monkeypatch, tmp_path):
    task = {
        "expect": {"phase": ["completed"], "tool_output_has_keys": [{"tool": "system.diagnostics", "keys": ["info"]}]}
    }
    monkeypatch.setattr(outcomes, "_reviewed_outputs", lambda task_id: {})
    assert outcomes.measure_outcome_evidence(task, {"phase": "completed"}, tmp_path, "task")["verified"] is False
    monkeypatch.setattr(outcomes, "_reviewed_outputs", lambda task_id: {"system.diagnostics": [{"info": {}}]})
    assert (
        outcomes.measure_outcome_evidence(task, {"phase": "awaiting_approval"}, tmp_path, "task")["verified"] is False
    )
    waiting = {"expect": {"phase": ["awaiting_approval"], "files_exist": ["keep.txt"]}}
    assert outcomes.empty_outcome_evidence(waiting)["eligible"] is False
    # No postcondition declared is unknown rather than successful.
    assert outcomes.empty_outcome_evidence({"expect": {"phase": ["completed"]}})["verified"] is None

    # A provider failure before the result measurement still emits valid failed
    # evidence; an unattempted placeholder must not contribute a zero latency.
    def fail_before_result(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    task.update(id="early-failure", title="fixture", category="read", entry="runs", message="diagnose")
    monkeypatch.setattr(harness, "_golden_app", FastAPI)
    monkeypatch.setattr(harness, "_run_runs_entry", fail_before_result)
    failed = harness._evaluate_task(task, 0.1)
    assert failed["ran"] is False and failed["error"]
    assert failed["outcome_evidence"]["verified"] is False
    outcomes.validate_product_evidence(failed, task)
    skipped = {**failed, "id": "skipped", "error": "", "duration_seconds": 0.0}
    metrics = harness._aggregate([failed, skipped])["product_metrics"]
    assert metrics["verified_outcome_count"] == 0
    assert metrics["duration_sample_count"] == 1
    assert metrics["duration_p50_seconds"] == failed["duration_seconds"]


def test_usage_collection_does_not_report_unknown_prices_or_reservations_as_free(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    assert outcomes.collect_usage_evidence() == outcomes.empty_usage_evidence()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO llm_usage_events
               (id, provider, model, mode, task, purpose, prompt_tokens, completion_tokens,
                total_tokens, total_cost_usd, estimated, data, created_at)
               VALUES ('usage', 'test', 'test', 'efficiency', 'test', 'test', 8, 2, 10, NULL, 1, '{}', 'now')"""
        )
    usage = outcomes.collect_usage_evidence()
    assert usage["total_tokens"] == 10 and usage["total_cost_usd"] is None
    assert usage["estimated"] is True
    with db.connect() as conn:
        conn.execute(
            "UPDATE llm_usage_events SET total_cost_usd = 0.1, data = ?",
            (json.dumps({"reservation": {"state": "reserved"}}),),
        )
    pending = outcomes.collect_usage_evidence()
    assert pending["total_tokens"] is None and pending["total_cost_usd"] is None
    with db.connect() as conn:
        conn.execute("UPDATE llm_usage_events SET data = ?", (json.dumps({"reservation": {"state": "settled"}}),))
    assert outcomes.collect_usage_evidence()["total_cost_usd"] == 0.1


def test_product_metrics_keep_failures_in_denominator_and_costs_and_validate_evidence():
    task = {"expect": {"phase": ["completed"], "files_absent": ["removed.txt"]}}
    evidence = {**outcomes.empty_outcome_evidence(task), "verified": True, "checks_passed": 1}
    passed = {
        "ran": True,
        "phase": "completed",
        "evaluation_passed": True,
        "duration_seconds": 2.0,
        "outcome_evidence": evidence,
        "usage_evidence": {
            **outcomes.empty_usage_evidence(),
            "recorded_events": 1,
            "total_tokens": 10,
            "total_cost_usd": 0.1,
            "estimated": False,
        },
    }
    failed = copy.deepcopy(passed)
    failed.update(phase="failed", evaluation_passed=False, duration_seconds=4.0)
    failed["outcome_evidence"].update(verified=False, checks_passed=0)
    failed["usage_evidence"]["total_cost_usd"] = 0.2
    metrics = outcomes.product_metrics([passed, failed], 2, 2)
    assert metrics["phase_acceptance_rate"] == 1.0
    assert metrics["verified_outcome_rate"] == 0.5
    assert metrics["cost_per_verified_outcome_usd"] == 0.3
    assert metrics["duration_p50_seconds"] == 3 and metrics["duration_p95_seconds"] is None
    outcomes.validate_product_evidence(passed, task)
    outcomes.validate_product_evidence(failed, task)
    failed["usage_evidence"] = outcomes.empty_usage_evidence()
    metrics = outcomes.product_metrics([passed, failed], 2, 2)
    assert metrics["total_tokens"] is None and metrics["total_cost_usd"] is None
    assert metrics["cost_per_verified_outcome_usd"] is None
    failed["outcome_evidence"]["verified"] = True
    with pytest.raises(ValueError, match="contradicts"):
        outcomes.validate_product_evidence(failed, task)
    failed["outcome_evidence"]["verified"] = False
    failed["usage_evidence"]["total_cost_usd"] = 0.0
    with pytest.raises(ValueError, match="missing usage"):
        outcomes.validate_product_evidence(failed, task)
