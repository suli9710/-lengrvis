from __future__ import annotations

import sqlite3
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import db
from app.core.errors import AppError
from app.orchestration import agent_bus
from app.services.privacy_erase_service import erase_local_content


@pytest.fixture(autouse=True)
def isolated_messages(monkeypatch, tmp_path):
    assert agent_bus.flush_agent_message_writes()
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    agent_bus.reset_agent_message_persist_failures_for_tests()
    yield
    agent_bus.reset_agent_message_persist_failures_for_tests()


def test_erase_invalidates_messages_waiting_to_enter_writer_queue(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    enqueue = agent_bus._put_persist_item_with_backpressure

    def blocked_enqueue(item, message_id):
        entered.set()
        assert release.wait(10)
        enqueue(item, message_id)

    monkeypatch.setattr(agent_bus, "_put_persist_item_with_backpressure", blocked_enqueue)
    bus = agent_bus.AgentBus()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(bus.publish_text, "old-task", "User", "PRIVATE_QUEUED_MESSAGE")
        try:
            assert entered.wait(10)
            erase_local_content(tmp_path, include_settings=False)
        finally:
            release.set()
        pending.result()
    assert agent_bus.flush_agent_message_writes()
    assert db.fetch_many("agent_messages") == []
    message = bus.publish_text("new-task", "User", "new message after erase")
    assert [row["id"] for row in db.fetch_many("agent_messages")] == [message.id]


def test_erase_refuses_active_message_write_then_removes_it(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    upsert = db.upsert_model

    def blocked_upsert(table, model, **kwargs):
        if table == "agent_messages":
            entered.set()
            assert release.wait(10)
        return upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", blocked_upsert)
    bus = agent_bus.AgentBus()
    bus.publish_text("old-task", "User", "PRIVATE_ACTIVE_MESSAGE")
    try:
        assert entered.wait(10)
        with pytest.raises(AppError, match="任务或索引操作"):
            erase_local_content(tmp_path, include_settings=False)
    finally:
        release.set()
        assert agent_bus.flush_agent_message_writes()
    erase_local_content(tmp_path, include_settings=False)
    assert db.fetch_many("agent_messages") == []


@pytest.mark.parametrize("failure_limit", [1000, 1])
def test_erase_invalidates_failed_retries_and_overflow(monkeypatch, tmp_path, failure_limit):
    monkeypatch.setattr(agent_bus, "_PERSIST_FAILURE_LIMIT", failure_limit)
    monkeypatch.setattr(agent_bus, "_PERSIST_FAILURES", deque(maxlen=failure_limit))
    upsert = db.upsert_model

    def failed_upsert(table, model, **kwargs):
        if table == "agent_messages":
            raise sqlite3.OperationalError("synthetic disk full")
        return upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", failed_upsert)
    bus = agent_bus.AgentBus()
    for _ in range(2):
        bus.publish_text("old-task", "User", "PRIVATE_FAILED_MESSAGE")
    with pytest.raises(agent_bus.AgentMessagePersistError):
        agent_bus.flush_agent_message_writes()
    monkeypatch.setattr(db, "upsert_model", upsert)
    erase_local_content(tmp_path, include_settings=False)
    assert db.fetch_many("agent_messages") == []  # Reads and shutdown both flush/retry writes.
    assert agent_bus.flush_agent_message_writes()
    message = bus.publish_text("new-task", "User", "new message after erase")
    assert [row["id"] for row in db.fetch_many("agent_messages")] == [message.id]


def test_erasing_one_database_preserves_another_databases_failed_message(monkeypatch, tmp_path):
    other = tmp_path.with_name(tmp_path.name + "-other")
    upsert = db.upsert_model

    def failed_upsert(table, model, **kwargs):
        if table == "agent_messages":
            raise sqlite3.OperationalError("synthetic disk full")
        return upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", failed_upsert)
    bus = agent_bus.AgentBus()
    bus.publish_text("erased-task", "User", "ERASE_THIS_MESSAGE")
    with pytest.raises(agent_bus.AgentMessagePersistError):
        agent_bus.flush_agent_message_writes()
    with db.using_data_dir(str(other)):
        db.init_db()
        preserved = bus.publish_text("other-task", "User", "PRESERVE_OTHER_DATABASE")
        with pytest.raises(agent_bus.AgentMessagePersistError):
            agent_bus.flush_agent_message_writes()
    monkeypatch.setattr(db, "upsert_model", upsert)
    erase_local_content(tmp_path, include_settings=False)
    assert db.fetch_many("agent_messages") == []
    with db.using_data_dir(str(other)):
        assert [row["id"] for row in db.fetch_many("agent_messages")] == [preserved.id]
