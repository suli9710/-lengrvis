from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from app.core import db
from app.core.schemas import new_id, now_iso
from app.orchestration.dispatcher import EventDispatcher


# ---------------------------------------------------------------------------
# Minimal Event stub (independent of events.py)
# ---------------------------------------------------------------------------

class StubEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str = "test.event"
    task_id: str = "task_test"
    timestamp: str = Field(default_factory=now_iso)
    source_agent: str = "TestAgent"
    payload: dict = Field(default_factory=dict)

    def summary(self) -> str:
        return "test event"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


@pytest.fixture
def dispatcher():
    return EventDispatcher()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_and_dispatch(dispatcher):
    received = []
    dispatcher.register("test.event", lambda e: received.append(e))
    event = StubEvent()
    await dispatcher.dispatch(event)
    assert len(received) == 1
    assert received[0].event_type == "test.event"


@pytest.mark.asyncio
async def test_handler_call_order(dispatcher):
    order = []
    dispatcher.register("test.event", lambda e: order.append("first"))
    dispatcher.register("test.event", lambda e: order.append("second"))
    await dispatcher.dispatch(StubEvent())
    assert order == ["first", "second"]


@pytest.mark.asyncio
async def test_wildcard_handler(dispatcher):
    received = []
    dispatcher.register("*", lambda e: received.append(e.event_type))
    await dispatcher.dispatch(StubEvent(event_type="a.b"))
    await dispatcher.dispatch(StubEvent(event_type="c.d"))
    assert received == ["a.b", "c.d"]


@pytest.mark.asyncio
async def test_handler_error_does_not_block_others(dispatcher):
    results = []

    def bad_handler(e):
        raise ValueError("boom")

    dispatcher.register("test.event", bad_handler)
    dispatcher.register("test.event", lambda e: results.append("ok"))
    await dispatcher.dispatch(StubEvent())
    assert results == ["ok"]


@pytest.mark.asyncio
async def test_async_handler(dispatcher):
    received = []

    async def async_handler(e):
        await asyncio.sleep(0)
        received.append(e.event_type)

    dispatcher.register("test.event", async_handler)
    await dispatcher.dispatch(StubEvent())
    assert received == ["test.event"]


@pytest.mark.asyncio
async def test_dispatch_returns_results(dispatcher):
    dispatcher.register("test.event", lambda e: 42)
    results = await dispatcher.dispatch(StubEvent())
    assert 42 in results


@pytest.mark.asyncio
async def test_register_many(dispatcher):
    received = []
    dispatcher.register_many("test.event", [
        lambda e: received.append("a"),
        lambda e: received.append("b"),
    ])
    await dispatcher.dispatch(StubEvent())
    assert received == ["a", "b"]


@pytest.mark.asyncio
async def test_async_queue_processing(dispatcher):
    received = []
    dispatcher.register("test.event", lambda e: received.append(e.task_id))
    task = asyncio.create_task(dispatcher.start())
    await dispatcher.dispatch_async(StubEvent(task_id="t1"))
    await dispatcher.dispatch_async(StubEvent(task_id="t2"))
    # Give the loop a moment to process both events.
    await asyncio.sleep(0.1)
    await dispatcher.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert received == ["t1", "t2"]
