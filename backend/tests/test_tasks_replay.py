from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import AgentMessage, MessageType, SafetyReview, Task, ToolCall, ToolResult
from app.main import app
from app.policy.risk import RiskLevel, SafetyVerdict


def test_task_replay_fetches_results_for_current_task_beyond_global_recent_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(id="task_replay_old", user_goal="Replay old task")
    tool_call = ToolCall(
        id="tool_call_old",
        task_id=task.id,
        step_id="step-1",
        tool_name="file.read_text",
        args={"path": "notes.txt"},
        risk_level=RiskLevel.R0_READ_ONLY,
        created_at="2024-01-01T00:00:00Z",
    )
    tool_result = ToolResult(
        id="result_old",
        tool_call_id=tool_call.id,
        ok=True,
        output={"text": "target result"},
        created_at="2024-01-01T00:00:01Z",
    )
    db.upsert_model("tasks", task)
    db.upsert_model("tool_calls", tool_call)
    db.upsert_model("tool_results", tool_result)
    for index in range(1001):
        db.upsert_model(
            "tool_results",
            ToolResult(
                id=f"result_noise_{index}",
                tool_call_id=f"noise_call_{index}",
                ok=True,
                output={"index": index},
                created_at=f"2026-01-01T00:00:00.{index:04d}Z",
            ),
        )

    response = TestClient(app).get(f"/api/tasks/{task.id}/replay")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_calls"][0]["id"] == tool_call.id
    assert payload["tool_results"] == [tool_result.model_dump(mode="json")]


def test_task_timeline_exposes_boundary_events(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(id="task_boundary_events", user_goal="Expose boundary events")
    db.upsert_model("tasks", task)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="step_1",
            from_agent="ToolRuntime",
            message_type=MessageType.NOTIFICATION,
            content="Starting file.read_text.",
            structured_payload={"kind": "tool_progress", "tool_name": "file.read_text", "status": "started"},
            metadata={"event_type": "tool.progress", "tool_name": "file.read_text"},
        ),
    )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            step_id="step_1",
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Post-tool output remained within boundary."],
        ),
    )
    record("context.projected", "ContextAwareProvider", {"strategy": "auto_compact", "tokens_saved": 42}, task_id=task.id)

    client = TestClient(app)
    timeline = client.get(f"/api/tasks/{task.id}/timeline")
    tasks = client.get("/api/tasks")

    assert timeline.status_code == 200
    kinds = {event["kind"] for event in timeline.json()["boundary_events"]}
    assert {"tool_progress", "post_tool_review", "context_projection"}.issubset(kinds)
    assert tasks.status_code == 200
    listed = next(item for item in tasks.json() if item["id"] == task.id)
    assert listed["boundary_events"]
