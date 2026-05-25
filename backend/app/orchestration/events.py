from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.schemas import new_id, now_iso


class StepEvent(StrEnum):
    TOOL_FAILED = "tool.failed"


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------

class Event(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str
    task_id: str
    timestamp: str = Field(default_factory=now_iso)
    source_agent: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        return f"[{self.event_type}] task={self.task_id}"

    # --- helpers used by event_to_dict / event_from_dict ---

    def _typed_fields(self) -> dict[str, Any]:
        """Return fields that are specific to the concrete subclass."""
        base_keys = set(Event.model_fields.keys())
        return {k: v for k, v in self.model_dump().items() if k not in base_keys}


# ---------------------------------------------------------------------------
# Concrete event types
# ---------------------------------------------------------------------------

class TaskCreated(Event):
    event_type: str = "task.created"
    user_goal: str = ""
    mode: str = "privacy"

    def summary(self) -> str:
        return f"Task created: {self.user_goal[:80]}"


class GoalReviewed(Event):
    event_type: str = "goal.reviewed"
    analysis: str = ""
    feasibility: str = ""

    def summary(self) -> str:
        return f"Goal reviewed — feasibility: {self.feasibility}"


class PlanGenerated(Event):
    event_type: str = "plan.generated"
    plan_id: str = ""
    step_count: int = 0
    global_risk_level: str = ""

    def summary(self) -> str:
        return f"Plan {self.plan_id} generated with {self.step_count} steps (risk={self.global_risk_level})"


class ConsultationDone(Event):
    event_type: str = "consultation.done"
    consulted_agents: list[str] = Field(default_factory=list)
    findings: str = ""

    def summary(self) -> str:
        agents = ", ".join(self.consulted_agents) or "none"
        return f"Consultation done — agents: {agents}"


class PlanReviewed(Event):
    event_type: str = "plan.reviewed"
    plan_id: str = ""
    verdict: str = ""
    reasons: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        return f"Plan {self.plan_id} reviewed — verdict: {self.verdict}"


class StepReady(Event):
    event_type: str = "step.ready"
    step_id: str = ""
    agent_name: str = ""
    tool_name: str = ""

    def summary(self) -> str:
        return f"Step {self.step_id} ready — agent={self.agent_name}, tool={self.tool_name}"


class SubagentResponded(Event):
    event_type: str = "subagent.responded"
    step_id: str = ""
    agent_name: str = ""
    response_summary: str = ""

    def summary(self) -> str:
        return f"Subagent {self.agent_name} responded for step {self.step_id}"


class SafetyReviewDone(Event):
    event_type: str = "safety_review.done"
    step_id: str = ""
    verdict: str = ""
    risk_level: str = ""

    def summary(self) -> str:
        return f"Safety review for step {self.step_id}: {self.verdict} (risk={self.risk_level})"


class ApprovalNeeded(Event):
    event_type: str = "approval.needed"
    step_id: str = ""
    approval_id: str = ""
    message: str = ""

    def summary(self) -> str:
        return f"Approval needed for step {self.step_id}: {self.message[:80]}"


class ApprovalReceived(Event):
    event_type: str = "approval.received"
    approval_id: str = ""
    decision: str = ""

    def summary(self) -> str:
        return f"Approval {self.approval_id}: {self.decision}"


class ToolExecuted(Event):
    event_type: str = "tool.executed"
    step_id: str = ""
    tool_name: str = ""
    ok: bool = True
    changed_paths: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        status = "ok" if self.ok else "FAILED"
        return f"Tool {self.tool_name} executed ({status}) for step {self.step_id}"


class ToolFailed(Event):
    event_type: str = StepEvent.TOOL_FAILED.value
    step_id: str = ""
    tool_name: str = ""
    error: str = ""
    retry_count: int = 0

    def summary(self) -> str:
        return f"Tool {self.tool_name} failed for step {self.step_id}: {self.error[:80]}"


class ObservationRecorded(Event):
    event_type: str = "observation.recorded"
    step_id: str = ""
    observation: str = ""

    def summary(self) -> str:
        return f"Observation for step {self.step_id}: {self.observation[:80]}"


class ReflectionDone(Event):
    event_type: str = "reflection.done"
    step_id: str = ""
    next_action: str = ""
    rationale: str = ""

    def summary(self) -> str:
        return f"Reflection for step {self.step_id}: next={self.next_action}"


class AllStepsResolved(Event):
    event_type: str = "all_steps.resolved"
    total_steps: int = 0
    succeeded: int = 0
    failed: int = 0

    def summary(self) -> str:
        return f"All steps resolved: {self.succeeded}/{self.total_steps} succeeded, {self.failed} failed"


class TaskFinalized(Event):
    event_type: str = "task.finalized"
    final_summary: str = ""
    final_status: str = ""

    def summary(self) -> str:
        return f"Task finalized ({self.final_status}): {self.final_summary[:80]}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

EVENT_REGISTRY: dict[str, type[Event]] = {
    cls.model_fields["event_type"].default: cls
    for cls in [
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
    ]
}

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an Event to a dict compatible with AgentBus message format.

    The returned dict contains:
    - All base Event fields (id, event_type, task_id, timestamp, source_agent)
    - ``structured_payload``: the typed subclass fields as a dict
    - ``content``: the human-readable summary
    """
    base = {
        "id": event.id,
        "event_type": event.event_type,
        "task_id": event.task_id,
        "timestamp": event.timestamp,
        "source_agent": event.source_agent,
        "content": event.summary(),
        "structured_payload": event._typed_fields(),
    }
    return base


def event_from_dict(data: dict[str, Any]) -> Event:
    """Reconstruct an Event from a dict produced by ``event_to_dict``.

    Uses ``event_type`` to look up the correct subclass in
    ``EVENT_REGISTRY``.  Falls back to the base ``Event`` if the type
    is unrecognised.
    """
    event_type = data.get("event_type", "")
    cls = EVENT_REGISTRY.get(event_type, Event)

    # Merge structured_payload fields into top-level so pydantic can
    # populate the typed subclass fields.
    payload = data.get("structured_payload") or data.get("payload") or {}
    merged = {**data, **payload}

    return cls.model_validate(merged)
