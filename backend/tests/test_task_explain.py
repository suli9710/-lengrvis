from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole, Plan, PlanStep, SafetyReview, Task, ToolCall, ToolResult
from app.main import create_app
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.task_explain_service import build_task_explain


def _assert_completion_evidence_shape(completion_evidence):
    assert set(completion_evidence) == {"level", "result_verified", "result_artifacts", "missing", "signoff"}
    assert completion_evidence["level"] in {
        "submission",
        "task_created",
        "visible_progress",
        "completed_result",
        "safe_failure",
    }
    assert isinstance(completion_evidence["result_verified"], bool)
    assert isinstance(completion_evidence["result_artifacts"], list)
    assert isinstance(completion_evidence["missing"], list)
    assert completion_evidence["signoff"] is False
    for item in completion_evidence["result_artifacts"]:
        assert {"kind", "label", "redacted"}.issubset(item)
        assert isinstance(item["kind"], str)
        assert isinstance(item["label"], str)
        if "count" in item:
            assert isinstance(item["count"], int)
        assert item["redacted"] is True


def _completion_counts(completion_evidence):
    return {item["kind"]: item.get("count", 1) for item in completion_evidence["result_artifacts"]}


def test_build_task_explain_returns_full_decision_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    task = _seed_complete_task()

    explain = build_task_explain(task.id)

    assert explain["task_id"] == task.id
    assert explain["complete"] is True
    assert explain["missing_sections"] == []
    assert explain["user_goal_record"]["text"] == task.user_goal
    assert explain["supervisor_judgment"]["delegate"] is True
    assert explain["supervisor_judgment"]["agent_hint"] == "ComputerAgent"
    assert explain["planner_reasoning"]["step_count"] == 1
    assert explain["steps"][0]["safety_reviews"]
    assert explain["steps"][0]["subagent_suggestions"][0]["action"]["rationale"] == "System info answers the user's goal."
    _assert_completion_evidence_shape(explain["completion_evidence"])
    assert explain["completion_evidence"]["level"] == "completed_result"
    assert explain["completion_evidence"]["result_verified"] is True
    assert explain["completion_evidence"]["missing"] == []
    completion_counts = _completion_counts(explain["completion_evidence"])
    assert completion_counts["tool_result"] == 1
    assert completion_counts["final_summary"] == 1
    assert completion_counts["final_review"] == 1
    assert explain["final_result"]["summary"] == "System info checked."
    assert {item["stage"] for item in explain["chain"]} == {
        "user_goal",
        "supervisor_judgment",
        "planner_reasoning",
        "step_safety_reviews",
        "subagent_suggestions",
        "final_result",
    }
    assert explain["data_sources"]["agent_messages"] >= 4
    assert explain["data_sources"]["safety_reviews"] >= 4
    assert explain["data_sources"]["audit_events"] >= 2


def test_explain_route_returns_full_chain_after_task_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = _seed_complete_task()

    response = TestClient(create_app()).get(f"/api/tasks/{task.id}/explain")

    assert response.status_code == 200
    payload = response.json()
    assert payload["complete"] is True
    _assert_completion_evidence_shape(payload["completion_evidence"])
    assert payload["completion_evidence"]["level"] == "completed_result"
    assert payload["completion_evidence"]["result_verified"] is True
    assert _completion_counts(payload["completion_evidence"])["tool_result"] == 1
    assert payload["final_result"]["status"] == "completed"
    assert payload["steps"][0]["safety_reviews"][0]["reasons"]


def test_explain_route_returns_404_for_unknown_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    response = TestClient(create_app()).get("/api/tasks/missing/explain")

    assert response.status_code == 404


def test_completion_evidence_does_not_verify_submission_only_completed_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        user_goal="check this computer",
        mode="efficiency",
        status="completed",
        final_summary="Task was submitted and routed.",
    )
    db.upsert_model("tasks", task)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            role=OpenAIMessageRole.USER,
            from_agent="User",
            to_agent="OrchestratorAgent",
            message_type=MessageType.PROPOSAL,
            content=task.user_goal,
        ),
    )
    record("task.finished_or_waiting", "OrchestratorAgent", {"status": "completed"}, task_id=task.id)

    explain = build_task_explain(task.id)

    _assert_completion_evidence_shape(explain["completion_evidence"])
    assert explain["completion_evidence"]["level"] == "visible_progress"
    assert explain["completion_evidence"]["result_verified"] is False
    assert "completed result evidence" in explain["completion_evidence"]["missing"]
    completion_counts = _completion_counts(explain["completion_evidence"])
    assert completion_counts["final_summary"] == 1
    assert "tool_result" not in completion_counts
    assert "final_review" not in completion_counts


def test_completion_evidence_does_not_verify_successful_tool_result_without_final_summary(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "secret-tool-result-token-1234567890"
    private_path = "C:/Users/example/private-result.txt"
    task = Task(user_goal="read local status", mode="efficiency", status="completed")
    db.upsert_model("tasks", task)
    db.upsert_model(
        "tool_calls",
        ToolCall(
            id="tool_success_only",
            task_id=task.id,
            step_id="step_1",
            tool_name="file.read_text",
            args={"path": private_path, "token": secret_token},
            risk_level=RiskLevel.R0_READ_ONLY,
            dry_run=False,
            status="succeeded",
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result_success_only",
            tool_call_id="tool_success_only",
            ok=True,
            output={"path": private_path, "text": f"private body {secret_token}"},
            observation=f"Read {private_path} with {secret_token}.",
        ),
    )

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "visible_progress"
    assert completion_evidence["result_verified"] is False
    assert "completed result evidence" in completion_evidence["missing"]
    assert _completion_counts(completion_evidence) == {"tool_result": 1}
    public_dump = json.dumps(completion_evidence, ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_verifies_successful_tool_result_with_final_summary_without_public_result_leak(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "secret-tool-result-token-1234567890"
    private_path = "C:/Users/example/private-result.txt"
    task = Task(
        user_goal="read local status",
        mode="efficiency",
        status="completed",
        final_summary="Local status was checked.",
    )
    db.upsert_model("tasks", task)
    db.upsert_model(
        "tool_calls",
        ToolCall(
            id="tool_success_with_summary",
            task_id=task.id,
            step_id="step_1",
            tool_name="file.read_text",
            args={"path": private_path, "token": secret_token},
            risk_level=RiskLevel.R0_READ_ONLY,
            dry_run=False,
            status="succeeded",
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result_success_with_summary",
            tool_call_id="tool_success_with_summary",
            ok=True,
            output={"path": private_path, "text": f"private body {secret_token}"},
            observation=f"Read {private_path} with {secret_token}.",
        ),
    )

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "completed_result"
    assert completion_evidence["result_verified"] is True
    assert completion_evidence["missing"] == []
    assert _completion_counts(completion_evidence) == {"tool_result": 1, "final_summary": 1}
    public_dump = json.dumps(completion_evidence, ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_does_not_verify_review_without_result_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(user_goal="review only should not verify", mode="efficiency", status="completed")
    db.upsert_model("tasks", task)
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final review alone is not a completed result artifact."],
        ),
    )

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "visible_progress"
    assert completion_evidence["result_verified"] is False
    assert "completed result evidence" in completion_evidence["missing"]
    assert _completion_counts(completion_evidence) == {"final_review": 1}


def _seed_complete_task() -> Task:
    task = Task(user_goal="check system information", mode="efficiency", status="completed", final_summary="System info checked.")
    db.upsert_model("tasks", task)

    step = PlanStep(
        id="step_1",
        task_id=task.id,
        order=1,
        agent_name="ComputerAgent",
        tool_name="system.get_info",
        description="Read local system information.",
        args={},
        expected_observation="System information is available.",
        risk_level=RiskLevel.R0_READ_ONLY,
        status="succeeded",
    )
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        assumptions=["The request is read-only system inspection."],
        steps=[step],
        global_risk_level=RiskLevel.R0_READ_ONLY,
    )
    db.upsert_model("plans", plan)

    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            role=OpenAIMessageRole.USER,
            from_agent="User",
            to_agent="OrchestratorAgent",
            message_type=MessageType.PROPOSAL,
            content=task.user_goal,
        ),
    )
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            from_agent="PlannerAgent",
            message_type=MessageType.PROPOSAL,
            content="Generated plan with 1 step(s).",
            structured_payload=plan.model_dump(),
        ),
    )
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id=step.id,
            from_agent="ComputerAgent",
            message_type=MessageType.PROPOSAL,
            content="propose_tool system.get_info | System info answers the user's goal.",
            structured_payload={
                "subagent_action": {
                    "kind": "propose_tool",
                    "tool_name": "system.get_info",
                    "args": {},
                    "rationale": "System info answers the user's goal.",
                    "follow_up_question": "",
                }
            },
        ),
    )
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id=step.id,
            role=OpenAIMessageRole.TOOL,
            from_agent="ComputerAgent",
            message_type=MessageType.OBSERVATION,
            content="system.get_info completed.",
            tool_call_id="tool_1",
        ),
    )
    tool_call = ToolCall(
        id="tool_1",
        task_id=task.id,
        step_id=step.id,
        tool_name="system.get_info",
        args={},
        risk_level=RiskLevel.R0_READ_ONLY,
        dry_run=False,
        status="succeeded",
    )
    db.upsert_model("tool_calls", tool_call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result_1",
            tool_call_id=tool_call.id,
            ok=True,
            output={"platform": "Windows", "hostname": "redacted"},
            observation="System information was collected.",
        ),
    )

    for review in [
        SafetyReview(
            task_id=task.id,
            target_type="goal",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["No forbidden intent detected."],
        ),
        SafetyReview(
            task_id=task.id,
            target_type="plan",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Plan is within read/open-only risk bounds."],
        ),
        SafetyReview(
            task_id=task.id,
            step_id=step.id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Read-only or open-only tool call allowed."],
        ),
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final runtime review cleared the task state and summary."],
        ),
    ]:
        db.upsert_model("safety_reviews", review)

    record(
        "supervisor.decision",
        "SupervisorAgent",
        {
            "delegate": True,
            "reply": "Delegating to ComputerAgent.",
            "agent_hint": "ComputerAgent",
            "mode": task.mode,
            "goal": task.user_goal,
        },
        task_id=task.id,
    )
    record("task.finished_or_waiting", "OrchestratorAgent", {"status": "completed"}, task_id=task.id)
    return task
