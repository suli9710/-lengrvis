from __future__ import annotations

import pytest

from app.orchestration.events import (
    Event,
    TaskCreated,
    GoalReviewed,
    PlanGenerated,
    ConsultationDone,
    PlanReviewed,
    StepReady,
    SubagentResponded,
    SafetyReviewDone,
    ApprovalNeeded,
    ApprovalReceived,
    ToolExecuted,
    ToolFailed,
    ObservationRecorded,
    ReflectionDone,
    AllStepsResolved,
    TaskFinalized,
    event_to_dict,
    event_from_dict,
    EVENT_REGISTRY,
)


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

def test_registry_has_all_sixteen_types():
    assert len(EVENT_REGISTRY) == 16


def test_registry_values_are_event_subclasses():
    for key, cls in EVENT_REGISTRY.items():
        assert issubclass(cls, Event)
        assert cls is not Event


# ---------------------------------------------------------------------------
# Individual event construction
# ---------------------------------------------------------------------------

class TestTaskCreated:
    def test_fields(self):
        e = TaskCreated(task_id="task_abc", user_goal="test goal", source_agent="Supervisor")
        assert e.event_type == "task.created"
        assert e.task_id == "task_abc"
        assert e.user_goal == "test goal"
        assert e.source_agent == "Supervisor"
        assert e.mode == "privacy"

    def test_summary(self):
        e = TaskCreated(task_id="t1", user_goal="Do something")
        assert "Do something" in e.summary()


class TestGoalReviewed:
    def test_fields(self):
        e = GoalReviewed(task_id="t1", analysis="looks good", feasibility="high")
        assert e.event_type == "goal.reviewed"
        assert e.analysis == "looks good"
        assert e.feasibility == "high"

    def test_summary(self):
        e = GoalReviewed(task_id="t1", feasibility="high")
        assert "high" in e.summary()


class TestPlanGenerated:
    def test_fields(self):
        e = PlanGenerated(task_id="t1", plan_id="plan_1", step_count=5, global_risk_level="R2")
        assert e.event_type == "plan.generated"
        assert e.plan_id == "plan_1"
        assert e.step_count == 5
        assert e.global_risk_level == "R2"

    def test_summary(self):
        e = PlanGenerated(task_id="t1", plan_id="p1", step_count=3, global_risk_level="R0")
        s = e.summary()
        assert "p1" in s
        assert "3" in s


class TestConsultationDone:
    def test_fields(self):
        e = ConsultationDone(task_id="t1", consulted_agents=["FileAgent", "SearchAgent"], findings="all clear")
        assert e.event_type == "consultation.done"
        assert e.consulted_agents == ["FileAgent", "SearchAgent"]
        assert e.findings == "all clear"

    def test_summary(self):
        e = ConsultationDone(task_id="t1", consulted_agents=["A", "B"])
        assert "A" in e.summary()


class TestPlanReviewed:
    def test_fields(self):
        e = PlanReviewed(task_id="t1", plan_id="p1", verdict="approved", reasons=["safe"])
        assert e.event_type == "plan.reviewed"
        assert e.verdict == "approved"
        assert e.reasons == ["safe"]

    def test_summary(self):
        e = PlanReviewed(task_id="t1", plan_id="p1", verdict="revision_needed")
        assert "revision_needed" in e.summary()


class TestStepReady:
    def test_fields(self):
        e = StepReady(task_id="t1", step_id="s1", agent_name="FileAgent", tool_name="read_file")
        assert e.event_type == "step.ready"
        assert e.step_id == "s1"
        assert e.agent_name == "FileAgent"
        assert e.tool_name == "read_file"

    def test_summary(self):
        e = StepReady(task_id="t1", step_id="s1", agent_name="FileAgent", tool_name="read_file")
        s = e.summary()
        assert "s1" in s
        assert "FileAgent" in s


class TestSubagentResponded:
    def test_fields(self):
        e = SubagentResponded(task_id="t1", step_id="s1", agent_name="FileAgent", response_summary="done")
        assert e.event_type == "subagent.responded"
        assert e.response_summary == "done"

    def test_summary(self):
        e = SubagentResponded(task_id="t1", step_id="s1", agent_name="FileAgent")
        assert "FileAgent" in e.summary()


class TestSafetyReviewDone:
    def test_fields(self):
        e = SafetyReviewDone(task_id="t1", step_id="s1", verdict="approved", risk_level="R1")
        assert e.event_type == "safety_review.done"
        assert e.verdict == "approved"
        assert e.risk_level == "R1"

    def test_summary(self):
        e = SafetyReviewDone(task_id="t1", step_id="s1", verdict="blocked", risk_level="R3")
        s = e.summary()
        assert "blocked" in s
        assert "R3" in s


class TestApprovalNeeded:
    def test_fields(self):
        e = ApprovalNeeded(task_id="t1", step_id="s1", approval_id="a1", message="Please confirm")
        assert e.event_type == "approval.needed"
        assert e.approval_id == "a1"
        assert e.message == "Please confirm"

    def test_summary(self):
        e = ApprovalNeeded(task_id="t1", step_id="s1", message="Please confirm")
        assert "Please confirm" in e.summary()


class TestApprovalReceived:
    def test_fields(self):
        e = ApprovalReceived(task_id="t1", approval_id="a1", decision="approved")
        assert e.event_type == "approval.received"
        assert e.decision == "approved"

    def test_summary(self):
        e = ApprovalReceived(task_id="t1", approval_id="a1", decision="rejected")
        assert "rejected" in e.summary()


class TestToolExecuted:
    def test_fields(self):
        e = ToolExecuted(task_id="t1", step_id="s1", tool_name="write_file", ok=False, changed_paths=["/a.txt"])
        assert e.event_type == "tool.executed"
        assert e.ok is False
        assert e.changed_paths == ["/a.txt"]

    def test_summary_ok(self):
        e = ToolExecuted(task_id="t1", step_id="s1", tool_name="read_file", ok=True)
        assert "ok" in e.summary()

    def test_summary_failed(self):
        e = ToolExecuted(task_id="t1", step_id="s1", tool_name="write_file", ok=False)
        assert "FAILED" in e.summary()


class TestToolFailed:
    def test_fields(self):
        e = ToolFailed(task_id="t1", step_id="s1", tool_name="write_file", error="boom", retry_count=1)
        assert e.event_type == "tool.failed"
        assert e.step_id == "s1"
        assert e.tool_name == "write_file"
        assert e.error == "boom"
        assert e.retry_count == 1

    def test_summary(self):
        e = ToolFailed(task_id="t1", step_id="s1", tool_name="write_file", error="boom")
        assert "boom" in e.summary()


class TestObservationRecorded:
    def test_fields(self):
        e = ObservationRecorded(task_id="t1", step_id="s1", observation="File written successfully")
        assert e.event_type == "observation.recorded"
        assert e.observation == "File written successfully"

    def test_summary(self):
        e = ObservationRecorded(task_id="t1", step_id="s1", observation="File written")
        assert "File written" in e.summary()


class TestReflectionDone:
    def test_fields(self):
        e = ReflectionDone(task_id="t1", step_id="s1", next_action="proceed", rationale="all clear")
        assert e.event_type == "reflection.done"
        assert e.next_action == "proceed"
        assert e.rationale == "all clear"

    def test_summary(self):
        e = ReflectionDone(task_id="t1", step_id="s1", next_action="retry")
        assert "retry" in e.summary()


class TestAllStepsResolved:
    def test_fields(self):
        e = AllStepsResolved(task_id="t1", total_steps=10, succeeded=8, failed=2)
        assert e.event_type == "all_steps.resolved"
        assert e.total_steps == 10
        assert e.succeeded == 8
        assert e.failed == 2

    def test_summary(self):
        e = AllStepsResolved(task_id="t1", total_steps=5, succeeded=5, failed=0)
        s = e.summary()
        assert "5" in s


class TestTaskFinalized:
    def test_fields(self):
        e = TaskFinalized(task_id="t1", final_summary="All done", final_status="completed")
        assert e.event_type == "task.finalized"
        assert e.final_summary == "All done"
        assert e.final_status == "completed"

    def test_summary(self):
        e = TaskFinalized(task_id="t1", final_summary="All done", final_status="completed")
        s = e.summary()
        assert "completed" in s
        assert "All done" in s


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestEventToDict:
    def test_structure(self):
        e = TaskCreated(task_id="task_abc", user_goal="test goal", source_agent="Supervisor")
        d = event_to_dict(e)
        assert d["event_type"] == "task.created"
        assert d["task_id"] == "task_abc"
        assert d["source_agent"] == "Supervisor"
        assert "content" in d
        assert isinstance(d["structured_payload"], dict)
        assert d["structured_payload"]["user_goal"] == "test goal"
        assert d["structured_payload"]["mode"] == "privacy"

    def test_content_is_summary(self):
        e = TaskCreated(task_id="t1", user_goal="my goal")
        d = event_to_dict(e)
        assert d["content"] == e.summary()


class TestEventFromDict:
    def test_reconstruct_typed(self):
        e = TaskCreated(task_id="task_abc", user_goal="test goal")
        d = event_to_dict(e)
        restored = event_from_dict(d)
        assert isinstance(restored, TaskCreated)
        assert restored.user_goal == "test goal"
        assert restored.event_type == "task.created"
        assert restored.task_id == "task_abc"

    def test_unknown_event_type_falls_back_to_base(self):
        d = {"event_type": "custom.unknown", "task_id": "t1", "payload": {"x": 1}}
        restored = event_from_dict(d)
        assert isinstance(restored, Event)
        assert restored.event_type == "custom.unknown"


class TestRoundTrip:
    @pytest.mark.parametrize("cls,kwargs", [
        (TaskCreated, {"task_id": "t1", "user_goal": "g", "mode": "stealth"}),
        (GoalReviewed, {"task_id": "t1", "analysis": "ok", "feasibility": "high"}),
        (PlanGenerated, {"task_id": "t1", "plan_id": "p1", "step_count": 3, "global_risk_level": "R0"}),
        (ConsultationDone, {"task_id": "t1", "consulted_agents": ["A"], "findings": "f"}),
        (PlanReviewed, {"task_id": "t1", "plan_id": "p1", "verdict": "approved", "reasons": ["r"]}),
        (StepReady, {"task_id": "t1", "step_id": "s1", "agent_name": "A", "tool_name": "T"}),
        (SubagentResponded, {"task_id": "t1", "step_id": "s1", "agent_name": "A", "response_summary": "s"}),
        (SafetyReviewDone, {"task_id": "t1", "step_id": "s1", "verdict": "ok", "risk_level": "R0"}),
        (ApprovalNeeded, {"task_id": "t1", "step_id": "s1", "approval_id": "a1", "message": "m"}),
        (ApprovalReceived, {"task_id": "t1", "approval_id": "a1", "decision": "approved"}),
        (ToolExecuted, {"task_id": "t1", "step_id": "s1", "tool_name": "T", "ok": True, "changed_paths": ["/a"]}),
        (ToolFailed, {"task_id": "t1", "step_id": "s1", "tool_name": "T", "error": "boom", "retry_count": 1}),
        (ObservationRecorded, {"task_id": "t1", "step_id": "s1", "observation": "o"}),
        (ReflectionDone, {"task_id": "t1", "step_id": "s1", "next_action": "go", "rationale": "r"}),
        (AllStepsResolved, {"task_id": "t1", "total_steps": 3, "succeeded": 2, "failed": 1}),
        (TaskFinalized, {"task_id": "t1", "final_summary": "done", "final_status": "completed"}),
    ])
    def test_all_event_types_round_trip(self, cls, kwargs):
        original = cls(**kwargs)
        d = event_to_dict(original)
        restored = event_from_dict(d)
        assert type(restored) is cls
        for key, value in kwargs.items():
            assert getattr(restored, key) == value


# ---------------------------------------------------------------------------
# Summary non-empty
# ---------------------------------------------------------------------------

class TestSummaries:
    @pytest.mark.parametrize("cls,kwargs", [
        (TaskCreated, {"task_id": "t1", "user_goal": "g"}),
        (GoalReviewed, {"task_id": "t1"}),
        (PlanGenerated, {"task_id": "t1"}),
        (ConsultationDone, {"task_id": "t1"}),
        (PlanReviewed, {"task_id": "t1"}),
        (StepReady, {"task_id": "t1"}),
        (SubagentResponded, {"task_id": "t1"}),
        (SafetyReviewDone, {"task_id": "t1"}),
        (ApprovalNeeded, {"task_id": "t1"}),
        (ApprovalReceived, {"task_id": "t1"}),
        (ToolExecuted, {"task_id": "t1"}),
        (ToolFailed, {"task_id": "t1"}),
        (ObservationRecorded, {"task_id": "t1"}),
        (ReflectionDone, {"task_id": "t1"}),
        (AllStepsResolved, {"task_id": "t1"}),
        (TaskFinalized, {"task_id": "t1"}),
    ])
    def test_summary_returns_non_empty_string(self, cls, kwargs):
        e = cls(**kwargs)
        s = e.summary()
        assert isinstance(s, str)
        assert len(s) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_auto_generated_id_prefix(self):
        e = TaskCreated(task_id="t1")
        assert e.id.startswith("evt_")

    def test_timestamp_is_populated(self):
        e = TaskCreated(task_id="t1")
        assert len(e.timestamp) > 0

    def test_base_event_summary(self):
        e = Event(event_type="test", task_id="t1")
        s = e.summary()
        assert "test" in s
        assert "t1" in s

    def test_payload_field_default(self):
        e = TaskCreated(task_id="t1")
        assert isinstance(e.payload, dict)
