from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import (
    AgentMessage,
    MessageType,
    OpenAIMessageRole,
    Plan,
    PlanStep,
    SafetyReview,
    Task,
    ToolCall,
    ToolResult,
)
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


def _assert_result_quality_shape(result_quality):
    assert set(result_quality) == {
        "state",
        "label",
        "summary",
        "result_verified",
        "can_treat_as_done",
        "needs_review",
        "missing_checks",
        "next_step",
        "signoff",
        "redacted",
        "privacy_note",
    }
    assert result_quality["state"] in {
        "verified_result",
        "visible_progress",
        "safe_failure",
        "task_evidence_only",
    }
    assert isinstance(result_quality["label"], str)
    assert isinstance(result_quality["summary"], str)
    assert isinstance(result_quality["result_verified"], bool)
    assert isinstance(result_quality["can_treat_as_done"], bool)
    assert isinstance(result_quality["needs_review"], bool)
    assert isinstance(result_quality["missing_checks"], list)
    assert isinstance(result_quality["next_step"], str)
    assert result_quality["signoff"] is False
    assert result_quality["redacted"] is True


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
    assert (
        explain["steps"][0]["subagent_suggestions"][0]["action"]["rationale"] == "System info answers the user's goal."
    )
    _assert_completion_evidence_shape(explain["completion_evidence"])
    assert explain["completion_evidence"]["level"] == "completed_result"
    assert explain["completion_evidence"]["result_verified"] is True
    assert explain["completion_evidence"]["missing"] == []
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "verified_result"
    assert explain["result_quality"]["result_verified"] is True
    assert explain["result_quality"]["can_treat_as_done"] is True
    assert explain["result_quality"]["missing_checks"] == []
    completion_counts = _completion_counts(explain["completion_evidence"])
    assert completion_counts["tool_result"] == 1
    assert completion_counts["final_summary"] == 1
    assert completion_counts["post_tool_review"] == 1
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
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "visible_progress"
    assert explain["result_quality"]["result_verified"] is False
    assert "verified result evidence" in explain["result_quality"]["missing_checks"]


def test_completion_evidence_does_not_verify_successful_tool_result_without_final_summary(monkeypatch, tmp_path):
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
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "visible_progress"
    assert "verified result evidence" in explain["result_quality"]["missing_checks"]
    public_dump = json.dumps(completion_evidence, ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_does_not_verify_successful_tool_result_with_final_summary_without_reviews(
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
    assert completion_evidence["level"] == "visible_progress"
    assert completion_evidence["result_verified"] is False
    assert "post-tool result verification" in completion_evidence["missing"]
    assert "final result verification" in completion_evidence["missing"]
    assert _completion_counts(completion_evidence) == {"tool_result": 1, "final_summary": 1}
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "visible_progress"
    assert "action result review" in explain["result_quality"]["missing_checks"]
    assert "final result review" in explain["result_quality"]["missing_checks"]
    quality_dump = json.dumps(explain["result_quality"], ensure_ascii=False)
    assert "post-tool" not in quality_dump
    assert "tool_result" not in quality_dump
    public_dump = json.dumps([completion_evidence, explain["result_quality"]], ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_verifies_successful_tool_result_with_final_summary_and_reviews_without_public_result_leak(
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
            id="tool_success_with_reviewed_summary",
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
            id="result_success_with_reviewed_summary",
            tool_call_id="tool_success_with_reviewed_summary",
            ok=True,
            output={"path": private_path, "text": f"private body {secret_token}"},
            observation=f"Read {private_path} with {secret_token}.",
        ),
    )
    for review in [
        SafetyReview(
            task_id=task.id,
            step_id="step_1",
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Post-tool result was reviewed."],
        ),
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final result was reviewed."],
        ),
    ]:
        db.upsert_model("safety_reviews", review)

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "completed_result"
    assert completion_evidence["result_verified"] is True
    assert completion_evidence["missing"] == []
    assert _completion_counts(completion_evidence) == {
        "tool_result": 1,
        "final_summary": 1,
        "post_tool_review": 1,
        "final_review": 1,
    }
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "verified_result"
    assert explain["result_quality"]["result_verified"] is True
    assert explain["result_quality"]["can_treat_as_done"] is True
    public_dump = json.dumps(completion_evidence, ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_does_not_verify_multiple_results_with_partial_step_reviews(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "secret-multi-result-token-1234567890"
    task = Task(
        user_goal="read two local statuses",
        mode="efficiency",
        status="completed",
        final_summary="Both local statuses were checked.",
    )
    db.upsert_model("tasks", task)
    for index in (1, 2):
        tool_call = ToolCall(
            id=f"tool_multi_result_{index}",
            task_id=task.id,
            step_id=f"step_{index}",
            tool_name="file.read_text",
            args={"path": f"C:/Users/example/private-{index}.txt", "token": secret_token},
            risk_level=RiskLevel.R0_READ_ONLY,
            dry_run=False,
            status="succeeded",
        )
        db.upsert_model("tool_calls", tool_call)
        db.upsert_model(
            "tool_results",
            ToolResult(
                id=f"result_multi_result_{index}",
                tool_call_id=tool_call.id,
                ok=True,
                output={"text": f"private status {index} {secret_token}"},
                observation=f"Read private-{index}.txt with {secret_token}.",
            ),
        )
    for review in [
        SafetyReview(
            task_id=task.id,
            step_id="step_1",
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Only the first post-tool result was reviewed."],
        ),
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final result was reviewed."],
        ),
    ]:
        db.upsert_model("safety_reviews", review)

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "visible_progress"
    assert completion_evidence["result_verified"] is False
    assert "post-tool result verification" in completion_evidence["missing"]
    assert "completed result evidence" in completion_evidence["missing"]
    assert _completion_counts(completion_evidence) == {
        "tool_result": 2,
        "final_summary": 1,
        "post_tool_review": 1,
        "final_review": 1,
    }
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "visible_progress"
    assert "action result review" in explain["result_quality"]["missing_checks"]
    public_dump = json.dumps([completion_evidence, explain["result_quality"]], ensure_ascii=False)
    assert "private-1.txt" not in public_dump
    assert "private-2.txt" not in public_dump
    assert secret_token not in public_dump


def test_completion_evidence_verifies_multiple_results_with_step_bound_reviews(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        user_goal="read two reviewed statuses",
        mode="efficiency",
        status="completed",
        final_summary="Both reviewed statuses were checked.",
    )
    db.upsert_model("tasks", task)
    for index in (1, 2):
        tool_call = ToolCall(
            id=f"tool_reviewed_multi_result_{index}",
            task_id=task.id,
            step_id=f"step_{index}",
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
                id=f"result_reviewed_multi_result_{index}",
                tool_call_id=tool_call.id,
                ok=True,
                output={"status": f"ok-{index}"},
                observation=f"Status {index} was collected.",
            ),
        )
        db.upsert_model(
            "safety_reviews",
            SafetyReview(
                task_id=task.id,
                step_id=f"step_{index}",
                target_type="tool_result",
                verdict=SafetyVerdict.ALLOW,
                risk_level=RiskLevel.R0_READ_ONLY,
                reasons=[f"Post-tool result {index} was reviewed."],
            ),
        )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final result was reviewed."],
        ),
    )

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "completed_result"
    assert completion_evidence["result_verified"] is True
    assert completion_evidence["missing"] == []
    assert _completion_counts(completion_evidence) == {
        "tool_result": 2,
        "final_summary": 1,
        "post_tool_review": 2,
        "final_review": 1,
    }
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "verified_result"
    assert explain["result_quality"]["result_verified"] is True


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
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "visible_progress"


def test_result_quality_treats_result_review_needing_user_approval_as_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "approval-needed-token-1234567890"
    private_path = "C:/Users/example/approval-needed-result.txt"
    task = Task(
        user_goal="read local status requiring approval",
        mode="efficiency",
        status="completed",
        final_summary="Local status was checked, but approval is still needed.",
    )
    tool_call = ToolCall(
        id="tool_result_needs_approval",
        task_id=task.id,
        step_id="step_approval",
        tool_name="file.read_text",
        args={"path": private_path, "token": secret_token},
        risk_level=RiskLevel.R0_READ_ONLY,
        dry_run=False,
        status="succeeded",
    )
    db.upsert_model("tasks", task)
    db.upsert_model("tool_calls", tool_call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result_needs_approval",
            tool_call_id=tool_call.id,
            ok=True,
            output={"path": private_path, "text": f"private body {secret_token}"},
            observation=f"Read {private_path} with {secret_token}.",
        ),
    )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            step_id=tool_call.step_id,
            target_type="tool_result",
            verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            reasons=[f"User approval is still required for {private_path}."],
        ),
    )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            target_type="final",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Final summary was reviewed."],
        ),
    )

    explain = build_task_explain(task.id)

    completion_evidence = explain["completion_evidence"]
    _assert_completion_evidence_shape(completion_evidence)
    assert completion_evidence["level"] == "safe_failure"
    assert completion_evidence["result_verified"] is False
    assert "unblocked result review" in completion_evidence["missing"]
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "safe_failure"
    assert explain["result_quality"]["can_treat_as_done"] is False
    assert "blocking review cleared" in explain["result_quality"]["missing_checks"]
    public_dump = json.dumps([completion_evidence, explain["result_quality"]], ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump


def test_task_explain_result_quality_reports_task_evidence_only_without_private_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "task-quality-token-1234567890"
    private_path = "C:/Users/example/result-quality/private-note.txt"
    task = Task(
        user_goal=f"Inspect {private_path} using {secret_token}",
        mode="efficiency",
        status="created",
    )
    db.upsert_model("tasks", task)

    explain = build_task_explain(task.id)

    _assert_completion_evidence_shape(explain["completion_evidence"])
    assert explain["completion_evidence"]["level"] == "task_created"
    _assert_result_quality_shape(explain["result_quality"])
    assert explain["result_quality"]["state"] == "task_evidence_only"
    assert explain["result_quality"]["result_verified"] is False
    assert explain["result_quality"]["can_treat_as_done"] is False
    assert "completed task status" in explain["result_quality"]["missing_checks"]
    public_dump = json.dumps(explain["result_quality"], ensure_ascii=False)
    assert private_path not in public_dump
    assert "private-note.txt" not in public_dump
    assert secret_token not in public_dump
    assert "file.read_text" not in public_dump


def test_task_explain_redacts_tool_protocol_paths_urls_and_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    secret_token = "explain-secret-token-1234567890"
    private_path = "C:/Users/example/private-result.txt"
    secret_url = f"https://example.test/callback?token={secret_token}&ok=1"
    task = Task(
        user_goal=f"Read {private_path} with {secret_token}",
        mode="efficiency",
        status="completed",
        final_summary=f"Result checked at {secret_url} from {private_path}.",
    )
    db.upsert_model("tasks", task)
    step = PlanStep(
        id="step_private",
        task_id=task.id,
        order=1,
        agent_name="ComputerAgent",
        tool_name="file.read_text",
        description=f"Read {private_path} then call {secret_url}.",
        args={"path": private_path, "token": secret_token, "url": secret_url},
        expected_observation=f"File at {private_path} is available.",
        rollback_strategy=f"No rollback for {private_path}.",
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            from_agent="PlannerAgent",
            message_type=MessageType.PROPOSAL,
            content=f"Generated plan for {private_path} using {secret_url}.",
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
            content=f"propose_tool file.read_text with {secret_token}",
            structured_payload={
                "subagent_action": {
                    "kind": "propose_tool",
                    "tool_name": "file.read_text",
                    "args": {"path": private_path, "token": secret_token, "url": secret_url},
                    "rationale": f"Needs {private_path}.",
                }
            },
        ),
    )
    db.upsert_model(
        "safety_reviews",
        SafetyReview(
            task_id=task.id,
            step_id=step.id,
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=[f"Reviewed {private_path} with token {secret_token}."],
        ),
    )
    record(
        "task.approved_step_executed",
        "ToolRuntime",
        {
            "tool_name": "file.read_text",
            "args": {"path": private_path, "token": secret_token, "url": secret_url},
            "secret_token": secret_token,
            "url": secret_url,
        },
        task_id=task.id,
    )

    explain = build_task_explain(task.id)

    public_dump = json.dumps(explain, ensure_ascii=False)
    assert private_path not in public_dump
    assert secret_token not in public_dump
    assert secret_url not in public_dump
    assert "file.read_text" not in public_dump
    assert "propose_tool" not in public_dump
    assert "task.approved_step_executed" not in public_dump
    assert explain["steps"][0]["tool_name"] == "File capability"
    assert explain["steps"][0]["subagent_suggestions"][0]["action"]["kind"] == "tool proposal"
    assert explain["steps"][0]["subagent_suggestions"][0]["action"]["raw_details_redacted"] is True
    assert "[REDACTED_LOCAL_PATH]" in public_dump
    assert "next_step" in explain


def _seed_complete_task() -> Task:
    task = Task(
        user_goal="check system information",
        mode="efficiency",
        status="completed",
        final_summary="System info checked.",
    )
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
            step_id=step.id,
            target_type="tool_result",
            verdict=SafetyVerdict.ALLOW,
            risk_level=RiskLevel.R0_READ_ONLY,
            reasons=["Post-tool supervision cleared the result."],
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
