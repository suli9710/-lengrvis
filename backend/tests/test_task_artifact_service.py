"""Task artifact workspace aggregation (plan: phase1-artifact-workspace).

collect_task_artifacts must turn stored tool results into a deduplicated,
existence-annotated artifact list the desktop workspace view can render.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Task, ToolCall, ToolResult
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel
from app.services.task_artifact_service import collect_task_artifacts


def _setup_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()


def _seed_task_with_results(tmp_path) -> str:
    task = Task(user_goal="organize downloads", status=TaskPhase.COMPLETED, phase=TaskPhase.COMPLETED)
    db.upsert_model("tasks", task)

    moved = tmp_path / "downloads" / "moved.zip"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_bytes(b"zip-bytes")
    report = tmp_path / "reports" / "cleanup-report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# report", encoding="utf-8")

    move_call = ToolCall(
        task_id=task.id, step_id="step-1", tool_name="file.move", risk_level=RiskLevel.R2_REVERSIBLE_MODIFY
    )
    report_call = ToolCall(
        task_id=task.id, step_id="step-2", tool_name="file.generate_report", risk_level=RiskLevel.R0_READ_ONLY
    )
    failed_call = ToolCall(
        task_id=task.id, step_id="step-3", tool_name="file.delete", risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    )
    for call in (move_call, report_call, failed_call):
        db.upsert_model("tool_calls", call)

    db.upsert_model(
        "tool_results",
        ToolResult(
            tool_call_id=move_call.id,
            ok=True,
            output={"destination": str(moved)},
            changed_paths=[str(moved), str(tmp_path / "downloads" / "deleted-later.tmp")],
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            tool_call_id=report_call.id,
            ok=True,
            output={"report_path": str(report), "url": "https://example.com/not-a-file"},
        ),
    )
    # Failed results must not contribute artifacts.
    db.upsert_model(
        "tool_results",
        ToolResult(
            tool_call_id=failed_call.id,
            ok=False,
            error="denied",
            changed_paths=[str(tmp_path / "downloads" / "never-happened.txt")],
        ),
    )
    return task.id


def test_collect_task_artifacts_aggregates_changed_and_generated(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    task_id = _seed_task_with_results(tmp_path)

    payload = collect_task_artifacts(task_id)

    paths = {item["path"] for item in payload["artifacts"]}
    assert str(tmp_path / "downloads" / "moved.zip") in paths
    assert str(tmp_path / "reports" / "cleanup-report.md") in paths
    assert str(tmp_path / "downloads" / "never-happened.txt") not in paths
    assert all("example.com" not in path for path in paths)

    by_path = {item["path"]: item for item in payload["artifacts"]}
    moved = by_path[str(tmp_path / "downloads" / "moved.zip")]
    # Same path appears in changed_paths and output destination -> deduped, "output" wins.
    assert moved["kind"] == "output"
    assert moved["exists"] is True
    assert moved["size_bytes"] > 0
    assert moved["tool_name"] == "file.move"

    missing = by_path[str(tmp_path / "downloads" / "deleted-later.tmp")]
    assert missing["exists"] is False

    counts = payload["counts"]
    assert counts["total"] == 3
    assert counts["existing"] == 2
    assert counts["missing"] == 1


def test_artifacts_endpoint_returns_payload_and_404(monkeypatch, tmp_path):
    from app.main import create_app

    _setup_env(monkeypatch, tmp_path)
    task_id = _seed_task_with_results(tmp_path)
    client = TestClient(create_app())

    response = client.get(f"/api/tasks/{task_id}/artifacts")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["counts"]["total"] == 3

    missing = client.get("/api/tasks/task_does_not_exist/artifacts")
    assert missing.status_code == 404


def test_artifacts_endpoint_resolves_run_id_to_linked_task(monkeypatch, tmp_path):
    from app.core.schemas import Run
    from app.main import create_app

    _setup_env(monkeypatch, tmp_path)
    task_id = _seed_task_with_results(tmp_path)
    run = Run(message="organize downloads", task_id=task_id)
    db.upsert_model("runs", run)
    client = TestClient(create_app())

    response = client.get(f"/api/tasks/{run.id}/artifacts")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["counts"]["total"] == 3
