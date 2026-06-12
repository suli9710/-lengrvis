from __future__ import annotations

import asyncio

from app.core import db
from app.core.schemas import MessageType
from app.orchestration.agent_bus import GLOBAL_TASK_ID, AgentBus


def test_publish_cross_task_persists_global_message(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()

    message = bus.publish_cross_task(
        "SafetyReviewAgent",
        "Global safety trend observed.",
        event_type="safety.trend",
        structured_payload={"window": "5m"},
    )

    assert message.task_id == GLOBAL_TASK_ID
    assert message.message_type == MessageType.NOTIFICATION
    assert message.metadata["cross_task"] is True
    assert message.structured_payload["event_type"] == "safety.trend"
    assert bus.get_messages(GLOBAL_TASK_ID)[0].id == message.id


def test_publish_persists_off_thread_and_reads_flush_pending_writes(monkeypatch, tmp_path):
    """publish() must not run the SQLite insert on the calling thread, while
    any DB read of agent_messages still observes the message (read barrier)."""
    import threading

    from app.orchestration import agent_bus as agent_bus_module

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    writer_threads: set[str] = []
    original_upsert = db.upsert_model

    def tracking_upsert(table, model, **kwargs):
        if table == "agent_messages":
            writer_threads.append(threading.current_thread().name)
        return original_upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", tracking_upsert)

    message = bus.publish_text("task_async_persist", "PlannerAgent", "off-thread persist")

    rows = db.fetch_many("agent_messages", "task_id = ?", ("task_async_persist",), limit=5)
    assert [row["id"] for row in rows] == [message.id]
    assert agent_bus_module.flush_agent_message_writes(timeout_seconds=5)
    assert writer_threads == ["agent-bus-writer"]


def test_global_subscription_receives_matching_event_type(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    async def run() -> None:
        bus = AgentBus()
        safety_queue = bus.subscribe_global("safety.event")
        all_queue = bus.subscribe_global()
        try:
            bus.publish_text("task_1", "PlannerAgent", "Not relevant.", message_type=MessageType.PROPOSAL)
            bus.publish_cross_task("SafetyReviewAgent", "Risk spike.", event_type="safety.event")

            matched = await asyncio.wait_for(safety_queue.get(), timeout=1)
            first_global = await asyncio.wait_for(all_queue.get(), timeout=1)
            second_global = await asyncio.wait_for(all_queue.get(), timeout=1)

            assert matched.content == "Risk spike."
            assert first_global.content == "Not relevant."
            assert second_global.content == "Risk spike."
        finally:
            bus.unsubscribe_global(safety_queue, "safety.event")
            bus.unsubscribe_global(all_queue)

    asyncio.run(run())


def test_global_subscription_does_not_replace_task_scoped_subscription(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    async def run() -> None:
        bus = AgentBus()
        task_queue = bus.subscribe("task_a")
        global_queue = bus.subscribe_global("review")
        try:
            bus.publish_text("task_b", "SafetyReviewAgent", "Global review.", message_type=MessageType.REVIEW)
            bus.publish_text("task_a", "PlannerAgent", "Task scoped.", message_type=MessageType.PROPOSAL)

            scoped = await asyncio.wait_for(task_queue.get(), timeout=1)
            global_message = await asyncio.wait_for(global_queue.get(), timeout=1)

            assert scoped.content == "Task scoped."
            assert global_message.content == "Global review."
        finally:
            bus.unsubscribe("task_a", task_queue)
            bus.unsubscribe_global(global_queue, "review")

    asyncio.run(run())


def test_unsubscribe_global_without_event_type_removes_specific_subscription(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    async def run() -> None:
        bus = AgentBus()
        queue = bus.subscribe_global("review")
        bus.unsubscribe_global(queue)

        bus.publish_text("task_a", "SafetyReviewAgent", "Global review.", message_type=MessageType.REVIEW)

        assert queue.empty()

    asyncio.run(run())
