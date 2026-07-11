"""Opt-in local metrics aggregation (plan: phase4-perf-observability).

The metrics service must aggregate counts only (no goals/prompts/paths), and the
API endpoint must stay fail-closed until the user opts in.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Run, RunEvent, RunPhase, Task
from app.orchestration.task_phase import TaskPhase
from app.services.local_metrics_service import _task_metrics, collect_local_metrics


def _setup_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()


def _seed_dataset() -> None:
    for status in (TaskPhase.COMPLETED, TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.EXECUTION):
        db.upsert_model("tasks", Task(user_goal="metrics seed", status=status, phase=status))

    run_ok = Run(message="m1", phase=RunPhase.COMPLETED)
    run_reflected = Run(message="m2", phase=RunPhase.COMPLETED)
    run_failed = Run(message="m3", phase=RunPhase.FAILED)
    for run in (run_ok, run_reflected, run_failed):
        db.upsert_model("runs", run)

    db.insert_run_event(
        RunEvent(run_id=run_reflected.id, name="os.reflection.started", payload={"reason": "step failed"})
    )
    db.insert_run_event(
        RunEvent(run_id=run_reflected.id, name="os.reflection.decided", payload={"action": "add_steps"})
    )
    db.insert_run_event(RunEvent(run_id=run_failed.id, name="os.reflection.started", payload={"reason": "step failed"}))
    db.insert_run_event(RunEvent(run_id=run_failed.id, name="os.reflection.decided", payload={"action": "ask_user"}))

    with db.connect() as conn:
        for index, finish_reason in enumerate(("stop", "stop", "content_filter")):
            conn.execute(
                """
                INSERT INTO llm_usage_events (
                    id, provider, model, mode, task, purpose,
                    prompt_tokens, completion_tokens, total_tokens,
                    total_cost_usd, estimated, data, created_at
                )
                VALUES (?, 'openai_compatible', 'gpt-test', 'efficiency', 'plan', 'planning',
                        10, 5, 15, NULL, ?, ?, ?)
                """,
                (
                    f"llm_usage_metrics_{index}",
                    1 if index == 2 else 0,
                    f'{{"finish_reason": "{finish_reason}"}}',
                    "2999-01-01T00:00:00+00:00",
                ),
            )


def test_collect_local_metrics_counts_only(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_dataset()

    payload = collect_local_metrics(days=7)

    assert payload["tasks"]["terminal"] == 3
    assert payload["tasks"]["succeeded"] == 2
    assert payload["tasks"]["success_rate"] == round(2 / 3, 4)

    assert payload["runs"]["total"] == 3
    assert payload["recovery"]["reflections_started"] == 2
    assert payload["recovery"]["runs_with_reflection"] == 2
    assert payload["recovery"]["recovery_trigger_rate"] == round(2 / 3, 4)
    assert payload["recovery"]["decided_actions"] == {"add_steps": 1, "ask_user": 1}
    assert payload["recovery"]["ask_user_share"] == 0.5

    assert payload["llm"]["calls"] == 3
    assert payload["llm"]["anomalies"] == 1
    assert payload["llm"]["anomaly_rate"] == round(1 / 3, 4)

    # Privacy: the payload must not leak任何自由文本（goal/prompt/path）。
    flat = str(payload)
    assert "metrics seed" not in flat
    assert "m1" not in payload.get("runs", {}).get("by_phase", {})


def test_task_metrics_normalize_legacy_failed_rollback_records() -> None:
    rows = [
        {"data": json.dumps({"status": "failed", "metadata": {"rollback": {"state": "succeeded"}}})},
        {"data": json.dumps({"status": "failed", "metadata": {"rollback": {"state": "partial"}}})},
    ]

    metrics = _task_metrics(rows)

    assert metrics["terminal"] == 2
    assert metrics["succeeded"] == 1
    assert metrics["by_status"] == {"repair_required": 1, "rolled_back": 1}


def test_collect_local_metrics_empty_db(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)

    payload = collect_local_metrics(days=7)

    assert payload["tasks"]["total"] == 0
    assert payload["tasks"]["success_rate"] is None
    assert payload["recovery"]["recovery_trigger_rate"] is None
    assert payload["llm"]["anomaly_rate"] is None


def test_metrics_endpoint_requires_opt_in(monkeypatch, tmp_path):
    from app.main import create_app

    _setup_env(monkeypatch, tmp_path)
    monkeypatch.delenv("LENGRVIS_LOCAL_METRICS_ENABLED", raising=False)
    client = TestClient(create_app())

    response = client.get("/api/metrics/local")
    assert response.status_code == 403


def test_metrics_endpoint_returns_data_when_enabled(monkeypatch, tmp_path):
    from app.main import create_app

    _setup_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_LOCAL_METRICS_ENABLED", "1")
    _seed_dataset()
    client = TestClient(create_app())

    response = client.get("/api/metrics/local?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["tasks"]["terminal"] == 3
    assert body["recovery"]["decided_actions"]["ask_user"] == 1
