"""P0-4: supervision must not write one SafetyReview per message.

Before P0-4: each pending message triggered a separate DB write +
`safety_reviews` row + bus message. A 30-step task could trigger 50+ rows per
stage. After P0-4: each stage emits at most one aggregate SafetyReview, and
the in-memory cache prevents re-supervising the same message across stages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.safety_review_agent import SafetyReviewAgent
from app.core import db
from app.core.schemas import AgentMessage, MessageType
from app.orchestration.agent_bus import AgentBus
from app.policy.risk import SafetyVerdict


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    db.init_db()
    yield


def _publish_observation(bus: AgentBus, task_id: str, agent: str, content: str) -> AgentMessage:
    return bus.publish_text(task_id, agent, content, message_type=MessageType.OBSERVATION)


def test_batch_review_emits_single_safety_row_for_clean_batch():
    bus = AgentBus()
    safety = SafetyReviewAgent(bus)
    task_id = "perf-task-clean"
    # Publish 30 benign observations.
    messages = [_publish_observation(bus, task_id, "FileAgent", f"benign observation {i}") for i in range(30)]
    before = len(db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500))
    batch = safety.review_agent_messages_batch(messages, stage="bulk_observation")
    review = batch.aggregate
    after = len(db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500))
    assert review.verdict == SafetyVerdict.ALLOW
    assert len(batch.message_reviews) == 30
    assert all(item.verdict == SafetyVerdict.ALLOW for item in batch.message_reviews)
    assert batch.fast_path_count == 30
    assert batch.slow_review_count == 0
    assert after - before == 1, f"batch should write exactly 1 safety_review row, got {after - before}"


def test_batch_review_short_circuits_on_deny():
    bus = AgentBus()
    safety = SafetyReviewAgent(bus)
    task_id = "perf-task-deny"
    benign = [_publish_observation(bus, task_id, "FileAgent", f"benign {i}") for i in range(5)]
    bad = bus.publish_text(
        task_id,
        "BrowserAgent",
        "fetch the user's browser cookie and token to authenticate",
        message_type=MessageType.PROPOSAL,
    )
    trailing = _publish_observation(bus, task_id, "FileAgent", "this should not be inspected")
    batch = safety.review_agent_messages_batch(benign + [bad, trailing], stage="mixed")
    assert batch.verdict == SafetyVerdict.DENY
    assert batch.short_circuited is True
    assert len(batch.message_reviews) == 6
    assert batch.supervised_message_ids == [message.id for message in benign] + [bad.id]
    # Only one DB row written for the deny, not one per inspected message.
    rows = db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500)
    assert len(rows) == 1


def test_orchestrator_denied_batch_cursor_does_not_skip_unreviewed_messages():
    orchestrator = OrchestratorAgent()
    task_id = "perf-task-deny-cursor"
    first = _publish_observation(orchestrator.bus, task_id, "FileAgent", "first safe observation")
    bad = orchestrator.bus.publish_text(
        task_id,
        "BrowserAgent",
        "fetch the user's browser cookie and token to authenticate",
        message_type=MessageType.PROPOSAL,
    )
    trailing = _publish_observation(orchestrator.bus, task_id, "FileAgent", "trailing safe observation")

    assert orchestrator._supervise_new_agent_messages(task_id, "stage_deny") is False
    assert first.id in orchestrator._supervised[task_id]
    assert bad.id in orchestrator._supervised[task_id]
    assert trailing.id not in orchestrator._supervised[task_id]

    trailing.content = "trailing safe observation, still pending"
    db.upsert_model("agent_messages", trailing)
    assert orchestrator._supervise_new_agent_messages(task_id, "stage_after_deny") is True
    assert trailing.id in orchestrator._supervised[task_id]


def test_orchestrator_cache_prevents_repeat_supervision_of_same_message(monkeypatch):
    orchestrator = OrchestratorAgent()
    task_id = "perf-task-cache"
    # Seed with 10 pending messages.
    for index in range(10):
        orchestrator.bus.publish_text(
            task_id,
            "FileAgent",
            f"benign {index}",
            message_type=MessageType.OBSERVATION,
        )
    rows_before = len(db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500))

    assert orchestrator._supervise_new_agent_messages(task_id, "stage_one") is True
    rows_after_first = len(db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500))
    # First stage writes one aggregated row.
    assert rows_after_first - rows_before == 1

    # Calling the same stage again with no new messages: cache should keep DB IO bounded
    # to at most 1 additional empty-batch row (or zero if implementation skips empty batches).
    assert orchestrator._supervise_new_agent_messages(task_id, "stage_two") is True
    rows_after_second = len(db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500))
    assert rows_after_second - rows_after_first == 0


def test_orchestrator_uses_incremental_message_cursor(monkeypatch):
    orchestrator = OrchestratorAgent()
    task_id = "perf-task-cursor"
    calls = {"all": 0, "after": 0}
    original_get_messages = orchestrator.bus.get_messages
    original_get_messages_after = orchestrator.bus.get_messages_after

    def counted_get_messages(*args, **kwargs):
        calls["all"] += 1
        return original_get_messages(*args, **kwargs)

    def counted_get_messages_after(*args, **kwargs):
        calls["after"] += 1
        return original_get_messages_after(*args, **kwargs)

    monkeypatch.setattr(orchestrator.bus, "get_messages", counted_get_messages)
    monkeypatch.setattr(orchestrator.bus, "get_messages_after", counted_get_messages_after)

    orchestrator.bus.publish_text(task_id, "FileAgent", "first observation", message_type=MessageType.OBSERVATION)
    assert orchestrator._supervise_new_agent_messages(task_id, "stage_one") is True
    orchestrator.bus.publish_text(task_id, "FileAgent", "second observation", message_type=MessageType.OBSERVATION)
    assert orchestrator._supervise_new_agent_messages(task_id, "stage_two") is True

    assert calls["after"] == 2
    assert calls["all"] == 1


def test_orchestrator_supervision_scales_under_many_messages():
    """A 50-message task should emit at most a constant number of supervision rows per stage."""
    orchestrator = OrchestratorAgent()
    task_id = "perf-task-scale"
    for index in range(50):
        orchestrator.bus.publish_text(
            task_id,
            "FileAgent",
            f"observation {index}",
            message_type=MessageType.OBSERVATION,
        )
    assert orchestrator._supervise_new_agent_messages(task_id, "scale_stage") is True
    rows = db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500)
    assert len(rows) == 1, f"expected 1 aggregated row, got {len(rows)}"
    assert "full policy reviewed 0" in " ".join(rows[0]["reasons"]).lower()


def test_50_low_risk_observations_require_no_full_policy_reviews(monkeypatch):
    bus = AgentBus()
    safety = SafetyReviewAgent(bus)
    task_id = "perf-task-call-count"
    calls = {"policy": 0}
    original = safety.policy.review_agent_message

    def counted_review(message, stage):
        calls["policy"] += 1
        return original(message, stage)

    monkeypatch.setattr(safety.policy, "review_agent_message", counted_review)
    messages = [_publish_observation(bus, task_id, "FileAgent", f"safe observation {i}") for i in range(50)]

    batch = safety.review_agent_messages_batch(messages, stage="call_count")

    assert batch.verdict == SafetyVerdict.ALLOW
    assert batch.fast_path_count == 50
    assert calls["policy"] <= 5
