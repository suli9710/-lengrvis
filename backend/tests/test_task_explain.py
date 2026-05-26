from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import AgentMessage, MessageType, OpenAIMessageRole, Plan, PlanStep, SafetyReview, Task
from app.main import create_app
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services.task_explain_service import build_task_explain


def test_build_task_explain_returns_full_decision_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = _seed_complete_task()

    response = TestClient(create_app()).get(f"/api/tasks/{task.id}/explain")

    assert response.status_code == 200
    payload = response.json()
    assert payload["complete"] is True
    assert payload["final_result"]["status"] == "completed"
    assert payload["steps"][0]["safety_reviews"][0]["reasons"]


def test_explain_route_returns_404_for_unknown_task(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    response = TestClient(create_app()).get("/api/tasks/missing/explain")

    assert response.status_code == 404


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
