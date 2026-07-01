from __future__ import annotations

import asyncio
import json
import queue
import threading
import time

from app.config import AppSettings
from app.core import db
from app.core.schemas import MessageType, OpenAIMessageRole
from app.orchestration.agent_bus import (
    GLOBAL_TASK_ID,
    AgentBus,
    AgentMessagePersistBackpressureError,
    flush_agent_message_writes,
)


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


def test_get_llm_messages_honors_requested_limit_above_db_default(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    settings = AppSettings(
        provider_name="mock",
        mode="efficiency",
        context_auto_compact_enabled=False,
        context_history_snip_enabled=False,
        context_micro_compact_enabled=False,
        context_session_memory_enabled=False,
    )

    for index in range(550):
        bus.publish_text("task_context_limit", "User", f"message {index}", role=OpenAIMessageRole.USER)

    assert flush_agent_message_writes(timeout_seconds=40)
    projected = bus.get_llm_messages("task_context_limit", settings, limit=500)

    assert len(projected) == 500
    assert any(message["content"] == "message 549" for message in projected)
    assert all(message["content"] != "message 0" for message in projected)


def test_get_llm_messages_redacts_non_user_payloads_without_rewriting_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    bus = AgentBus()
    settings = AppSettings(
        provider_name="mock",
        mode="efficiency",
        context_auto_compact_enabled=False,
        context_history_snip_enabled=False,
        context_micro_compact_enabled=False,
        context_session_memory_enabled=False,
    )
    local_path = r"C:\Users\Suli\Desktop\mavris\.env"
    secret = "sk-agent-bus-secret-value"

    message = bus.publish_text(
        "task_llm_redaction",
        "PlannerAgent",
        f"Tool failed while reading {local_path} token={secret}",
        structured_payload={
            "path": local_path,
            "api_key": secret,
            "nested": {"note": f"hidden prompt from {local_path}"},
        },
        metadata={"error": f"raw error {local_path} token={secret}"},
        tool_calls=[
            {
                "id": "call_secret",
                "type": "function",
                "function": {
                    "name": "file.read_text",
                    "arguments": {"path": local_path, "api_key": secret},
                },
            }
        ],
    )
    bus.publish_text(
        "task_llm_redaction",
        "file.read_text",
        '{"ok": true}',
        message_type=MessageType.OBSERVATION,
        role=OpenAIMessageRole.TOOL,
        tool_call_id="call_secret",
    )

    assert flush_agent_message_writes(timeout_seconds=10)
    persisted_messages = bus.get_messages("task_llm_redaction", limit=10)
    persisted = next(item for item in persisted_messages if item.id == message.id)
    assert persisted.id == message.id
    assert local_path in persisted.content
    assert secret in persisted.content
    assert persisted.structured_payload["path"] == local_path
    assert persisted.structured_payload["api_key"] == secret

    projected = bus.get_llm_messages("task_llm_redaction", settings, limit=10)

    assert len(projected) == 2
    dumped = str(projected)
    assert local_path not in dumped
    assert secret not in dumped
    llm_message = next(item for item in projected if item.get("tool_calls"))
    assert "[REDACTED_LOCAL_PATH]" in llm_message["content"]
    metadata = llm_message["metadata"]
    assert metadata["structured_payload"]["path"] == "[REDACTED_LOCAL_PATH]"
    assert metadata["structured_payload"]["api_key"] == "***"
    arguments = json.loads(llm_message["tool_calls"][0]["function"]["arguments"])
    assert arguments["path"] == "[REDACTED_LOCAL_PATH]"
    assert arguments["api_key"] == "***"
    assert llm_message["tool_calls"][0]["id"] == "call_secret"
    assert llm_message["tool_calls"][0]["function"]["name"] == "file.read_text"


def test_persist_queue_backpressure_preserves_tool_call_pairs(monkeypatch, tmp_path):
    from app.orchestration import agent_bus as agent_bus_module

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    monkeypatch.setattr(agent_bus_module, "_PERSIST_QUEUE", queue.Queue(maxsize=1))
    monkeypatch.setattr(agent_bus_module, "_PERSIST_QUEUE_MAX_SIZE", 1)
    monkeypatch.setattr(agent_bus_module, "_PERSIST_BACKPRESSURE_TIMEOUT", 0.01)
    monkeypatch.setattr(agent_bus_module, "_PERSIST_STATE", threading.Condition())
    monkeypatch.setattr(agent_bus_module, "_PERSIST_PENDING", 0)
    monkeypatch.setattr(agent_bus_module, "_PERSIST_THREAD", None)
    original_upsert = db.upsert_model

    def slow_upsert(table, model, **kwargs):
        if table == "agent_messages":
            time.sleep(0.03)
        return original_upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", slow_upsert)
    bus = AgentBus()

    assistant = bus.publish_text(
        "task_lossless_queue",
        "Assistant",
        "",
        tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "system.get_info", "arguments": "{}"}}],
    )
    tool_result = bus.publish_text(
        "task_lossless_queue",
        "system.get_info",
        '{"ok": true}',
        message_type=MessageType.OBSERVATION,
        role=OpenAIMessageRole.TOOL,
        tool_call_id="call_1",
    )
    final = bus.publish_text("task_lossless_queue", "Assistant", "done")

    assert agent_bus_module.flush_agent_message_writes(timeout_seconds=10)
    persisted = bus.get_messages("task_lossless_queue", limit=10)
    persisted_by_id = {message.id: message for message in persisted}

    assert {assistant.id, tool_result.id, final.id}.issubset(persisted_by_id)
    assert persisted_by_id[assistant.id].tool_calls[0]["id"] == "call_1"
    assert persisted_by_id[tool_result.id].tool_call_id == "call_1"


def test_persist_queue_full_does_not_block_event_loop(monkeypatch, tmp_path):
    from app.core.schemas import AgentMessage
    from app.orchestration import agent_bus as agent_bus_module

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    full_queue: queue.Queue[tuple[AgentMessage, str]] = queue.Queue(maxsize=1)
    full_queue.put((AgentMessage(task_id="already_full", content="queued"), str(tmp_path)))
    monkeypatch.setattr(agent_bus_module, "_PERSIST_QUEUE", full_queue)
    monkeypatch.setattr(agent_bus_module, "_PERSIST_QUEUE_MAX_SIZE", 1)
    monkeypatch.setattr(agent_bus_module, "_PERSIST_STATE", threading.Condition())
    monkeypatch.setattr(agent_bus_module, "_PERSIST_PENDING", 0)
    monkeypatch.setattr(agent_bus_module, "_ensure_persist_thread", lambda: None)

    async def run() -> None:
        bus = AgentBus()
        started = time.monotonic()
        try:
            try:
                bus.publish_text("task_loop_backpressure", "PlannerAgent", "queued from loop")
            except AgentMessagePersistBackpressureError:
                pass
            else:
                raise AssertionError("full persist queue should fail closed in the event loop")
        finally:
            elapsed = time.monotonic() - started
            assert elapsed < 0.5
            assert agent_bus_module._PERSIST_PENDING == 0

    asyncio.run(run())


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
