from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import AgentMessage, MessageType, SafetyReview, Task, ToolCall, ToolResult
from app.main import app
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.task_recording_service import persist_recording_frame


def test_task_replay_fetches_results_for_current_task_beyond_global_recent_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
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
    assert payload["tool_calls"][0]["args"] == {"redacted": True, "field_count": 1}
    assert payload["tool_results"][0]["id"] == tool_result.id
    assert payload["tool_results"][0]["output"] == {"redacted": True, "field_count": 1}
    assert payload["raw_redacted"] is True


def test_task_replay_summarizes_tool_result_errors_without_raw_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    raw_error = (
        "private customer/revenue prose: Acme renewal revenue will miss forecast; "
        "local file C:/Users/example/customer-revenue.txt token=secret-revenue-token-1234567890"
    )
    task = Task(id="task_replay_private_error", user_goal="Replay failed task")
    tool_call = ToolCall(
        id="tool_call_private_error",
        task_id=task.id,
        step_id="step-1",
        tool_name="file.read_text",
        args={"path": "C:/Users/example/customer-revenue.txt"},
        risk_level=RiskLevel.R0_READ_ONLY,
        created_at="2024-01-01T00:00:00Z",
    )
    tool_result = ToolResult(
        id="result_private_error",
        tool_call_id=tool_call.id,
        ok=False,
        error=raw_error,
        created_at="2024-01-01T00:00:01Z",
    )
    db.upsert_model("tasks", task)
    db.upsert_model("tool_calls", tool_call)
    db.upsert_model("tool_results", tool_result)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="step-1",
            from_agent="ToolRuntime",
            message_type=MessageType.NOTIFICATION,
            content=raw_error,
            structured_payload={
                "kind": "tool_progress",
                "tool_name": "file.read_text",
                "status": raw_error,
                "error": raw_error,
            },
            metadata={"event_type": "tool.progress", "tool_name": "file.read_text", "error": raw_error},
        ),
    )

    client = TestClient(app)
    replay = client.get(f"/api/tasks/{task.id}/replay")
    timeline = client.get(f"/api/tasks/{task.id}/timeline")

    assert replay.status_code == 200
    assert timeline.status_code == 200
    public_dump = json.dumps([replay.json(), timeline.json()], ensure_ascii=False)
    assert raw_error not in public_dump
    assert "Acme renewal revenue" not in public_dump
    assert "customer-revenue.txt" not in public_dump
    assert "secret-revenue-token-1234567890" not in public_dump
    public_result = replay.json()["tool_results"][0]
    assert public_result["error"] == "The tool reported an error. Private diagnostic details are hidden from replay."
    assert public_result["error_metadata"]["status"] == "failed"
    assert public_result["error_metadata"]["category"] == "execution_error"
    assert public_result["error_metadata"]["private_detail_redacted"] is True


def test_task_timeline_exposes_boundary_events(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
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
    timeline_payload = timeline.json()
    kinds = {event["kind"] for event in timeline_payload["boundary_events"]}
    assert {"tool_progress", "post_tool_review", "context_projection"}.issubset(kinds)
    summary = timeline_payload["evidence_summary"]
    assert summary["counts"]["review_checkpoints"] == 1
    assert summary["counts"]["capability_boundaries"] == 1
    assert summary["counts"]["tool_updates"] == 1
    assert "next_step" in summary
    assert "file contents" in summary["privacy_note"]
    assert tasks.status_code == 200
    listed = next(item for item in tasks.json() if item["id"] == task.id)
    assert listed["boundary_events"]
    assert listed["evidence_summary"]["counts"] == summary["counts"]


def test_task_evidence_summary_and_boundary_payloads_are_public_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "super-secret-token-1234567890"
    hidden_prompt = "hidden system prompt should stay private"
    file_body = "quarterly revenue file body should not be displayed"
    task = Task(
        id="task_public_safe_evidence",
        user_goal=f"Audit task with {secret_token}",
        status="completed",
        final_summary="Completed without exposing file contents.",
        metadata={
            "message": f"task metadata repeats {file_body}",
            "local_path": "C:/Users/example/private.txt",
            "token": secret_token,
            "hidden_prompt": hidden_prompt,
        },
    )
    db.upsert_model("tasks", task)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="step_1",
            from_agent="ModelBoundary",
            message_type=MessageType.NOTIFICATION,
            content=f"Model boundary denied token={secret_token}; {hidden_prompt}",
            structured_payload={
                "event_type": "model_boundary.denied",
                "reason": hidden_prompt,
                "token": secret_token,
            },
            metadata={"event_type": "model_boundary.denied"},
        ),
    )
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="step_1",
            from_agent="ToolRuntime",
            message_type=MessageType.NOTIFICATION,
            content=f"Reading C:/Users/example/private.txt with token={secret_token}",
            structured_payload={
                "kind": "tool_progress",
                "tool_name": "file.read_text",
                "status": "started",
                "path": "C:/Users/example/private.txt",
                "token": secret_token,
            },
            metadata={"event_type": "tool.progress", "tool_name": "file.read_text"},
        ),
    )
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="step_1",
            from_agent="ComputerAgent",
            message_type=MessageType.PROPOSAL,
            content=f"Plain agent note includes {file_body} and should not be public.",
            tool_calls=[
                {
                    "id": "call_sensitive_arguments",
                    "type": "function",
                    "function": {
                        "name": "file.read_text",
                        "arguments": json.dumps(
                            {
                                "path": "C:/Users/example/private.txt",
                                "content": file_body,
                                "note": "ordinary sensitive business prose",
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
            structured_payload={
                "kind": "subagent_note",
                "detail": file_body,
                "message": f"Do not expose {file_body}",
                "parameters": {"body": file_body, "path": "C:/Users/example/private.txt"},
            },
            metadata={
                "event_type": "subagent.note",
                "message": f"metadata repeats {file_body}",
            },
        ),
    )
    db.upsert_model(
        "tool_calls",
        ToolCall(
            id="tool_call_public_safe",
            task_id=task.id,
            step_id="step_1",
            tool_name="file.read_text",
            args={
                "path": "C:/Users/example/private.txt",
                "content": file_body,
                "token": secret_token,
                "hidden_prompt": hidden_prompt,
            },
            risk_level=RiskLevel.R0_READ_ONLY,
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="tool_result_public_safe",
            tool_call_id="tool_call_public_safe",
            ok=True,
            output={
                "path": "C:/Users/example/private.txt",
                "text": file_body,
                "token": secret_token,
            },
            observation=f"Read {file_body} from C:/Users/example/private.txt",
        ),
    )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            step_id="step_1",
            target_type="tool_result",
            verdict=SafetyVerdict.DENY,
            risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            reasons=[f"Review saw {file_body} and token={secret_token}."],
        ),
    )
    record(
        "context.projected",
        "ContextAwareProvider",
        {
            "strategy": "auto_compact",
            "tokens_saved": 13,
            "secret": secret_token,
            "content": file_body,
        },
        task_id=task.id,
    )
    persist_recording_frame(
        {
            "task_id": task.id,
            "step_id": "step_1",
            "phase": "before",
            "ok": True,
            "enabled": True,
            "captured_at": "2026-01-01T00:00:00Z",
            "file_name": f"screen-{secret_token}.png",
            "url": f"/api/tasks/{task.id}/recordings/screen-{secret_token}.png",
            "mime_type": "image/png",
            "width": 640,
            "height": 480,
        },
        b"private screenshot bytes",
    )

    client = TestClient(app)
    timeline = client.get(f"/api/tasks/{task.id}/timeline")
    task_payload = client.get(f"/api/tasks/{task.id}")
    safety_reviews = client.get(f"/api/tasks/{task.id}/safety-reviews")
    agent_messages = client.get(f"/api/tasks/{task.id}/agent-messages")
    progress = client.get(f"/api/tasks/{task.id}/progress")
    explain = client.get(f"/api/tasks/{task.id}/explain")
    replay = client.get(f"/api/tasks/{task.id}/replay")
    tasks = client.get("/api/tasks")

    assert timeline.status_code == 200
    assert task_payload.status_code == 200
    assert safety_reviews.status_code == 200
    assert agent_messages.status_code == 200
    assert progress.status_code == 200
    assert explain.status_code == 200
    assert replay.status_code == 200
    assert tasks.status_code == 200
    listed = next(item for item in tasks.json() if item["id"] == task.id)
    public_dump = json.dumps(
        [
            task_payload.json(),
            listed,
            replay.json(),
            timeline.json()["messages"],
            timeline.json()["reviews"],
            safety_reviews.json(),
            agent_messages.json(),
            progress.json(),
            explain.json(),
            timeline.json()["boundary_events"],
            timeline.json()["evidence_summary"],
            timeline.json()["recordings"],
            task_payload.json()["user_goal"],
            task_payload.json()["final_summary"],
            task_payload.json()["evidence_summary"],
            listed["user_goal"],
            listed["final_summary"],
            listed["evidence_summary"],
        ],
        ensure_ascii=False,
    )
    assert secret_token not in public_dump
    assert hidden_prompt not in public_dump
    assert file_body not in public_dump
    assert "ordinary sensitive business prose" not in public_dump
    assert "C:/Users/example/private.txt" not in public_dump
    assert "[REDACTED_LOCAL_PATH]" in public_dump
    assert "Agent message recorded." in public_dump
    assert "[REDACTED_TEXT]" in public_dump
    assert "recordings/screen-" not in public_dump
    assert all(item["redacted"] is True for item in timeline.json()["recordings"])
    assert all("url" not in frame and "file_name" not in frame for item in timeline.json()["recordings"] for frame in item["frames"])
    assert all(event["payload"].get("redacted") is True for event in timeline.json()["boundary_events"])
    assert timeline.json()["reviews"][0]["reason_count"] == 1
    assert timeline.json()["reviews"][0]["reasons"] == []
    assert safety_reviews.json()[0]["reason_count"] == 1
    assert safety_reviews.json()[0]["reasons"] == []
    assert agent_messages.json()[0]["redacted"] is True
    assert task_payload.json()["metadata"] == {"redacted": True, "field_count": 4}
    assert listed["metadata"] == {"redacted": True, "field_count": 4}
    assert replay.json()["raw_redacted"] is True
    assert replay.json()["tool_calls"][0]["args"]["redacted"] is True
    assert all(result["redacted"] is True for result in replay.json()["tool_results"])
    assert timeline.json()["evidence_summary"]["counts"]["items_needing_attention"] >= 1


def test_tasks_list_batches_boundary_events(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task_ids = [f"task_boundary_batch_{index}" for index in range(3)]
    for task_id in task_ids:
        task = Task(id=task_id, user_goal=f"Batch boundary events {task_id}")
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
        record("context.projected", "ContextAwareProvider", {"strategy": "auto_compact"}, task_id=task.id)

    event_fetches: dict[str, int] = {"agent_messages": 0, "safety_reviews": 0, "audit_events": 0}
    original_fetch_many = db.fetch_many

    def spy_fetch_many(table, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        if table in event_fetches:
            event_fetches[table] += 1
        return original_fetch_many(table, *args, **kwargs)

    monkeypatch.setattr(db, "fetch_many", spy_fetch_many)

    response = TestClient(app).get("/api/tasks")

    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json() if item["id"] in task_ids}
    assert set(listed) == set(task_ids)
    for task_id in task_ids:
        kinds = {event["kind"] for event in listed[task_id]["boundary_events"]}
        assert {"tool_progress", "post_tool_review", "context_projection"}.issubset(kinds)
    assert event_fetches == {"agent_messages": 0, "safety_reviews": 0, "audit_events": 0}


def test_tasks_list_boundary_events_use_table_task_id_when_json_task_id_is_stale(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    target = Task(id="task_boundary_table_source", user_goal="Use table task id")
    stale_json_target = Task(id="task_boundary_json_stale", user_goal="Stale JSON task id")
    db.upsert_model("tasks", target)
    db.upsert_model("tasks", stale_json_target)

    message = AgentMessage(
        id="msg_stale_json_task_id",
        task_id=stale_json_target.id,
        step_id="step_1",
        from_agent="ToolRuntime",
        message_type=MessageType.NOTIFICATION,
        content="Starting file.read_text.",
        structured_payload={"kind": "tool_progress", "tool_name": "file.read_text", "status": "started"},
        metadata={"event_type": "tool.progress", "tool_name": "file.read_text"},
        created_at="2024-01-01T00:00:00Z",
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_messages (id, task_id, step_id, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                message.id,
                target.id,
                message.step_id,
                json.dumps(message.model_dump(mode="json"), ensure_ascii=False),
                message.created_at,
            ),
        )

    response = TestClient(app).get("/api/tasks")

    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json() if item["id"] in {target.id, stale_json_target.id}}
    target_events = listed[target.id]["boundary_events"]
    stale_target_events = listed[stale_json_target.id]["boundary_events"]
    assert any(event["id"] == message.id and event["kind"] == "tool_progress" for event in target_events)
    assert all(event["id"] != message.id for event in stale_target_events)
