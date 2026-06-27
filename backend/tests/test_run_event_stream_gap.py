from __future__ import annotations

import asyncio

from app.api import routes_runs
from app.core.schemas import RunEvent


def test_run_event_stream_replays_gap_before_live_event(monkeypatch):
    sent: list[dict] = []
    replay_events = [
        RunEvent(run_id="run_gap", name="tool.progress", sequence=2, payload={"index": 2}),
        RunEvent(run_id="run_gap", name="tool.progress", sequence=3, payload={"index": 3}),
        RunEvent(run_id="run_gap", name="tool.progress", sequence=4, payload={"index": 4}),
    ]

    class FakeWebSocket:
        async def send_json(self, payload):  # noqa: ANN001
            sent.append(payload)

    class FakeBus:
        def replay(self, run_id: str, *, after_sequence: int = 0, limit: int = 1000):  # noqa: ARG002
            assert run_id == "run_gap"
            assert after_sequence == 1
            return replay_events

    monkeypatch.setattr(routes_runs, "run_event_bus", FakeBus())

    last_sequence = asyncio.run(
        routes_runs._replay_missing_events(
            FakeWebSocket(),
            "run_gap",
            last_sequence=1,
            target_sequence=4,
        )
    )

    assert last_sequence == 3
    assert [event["sequence"] for event in sent] == [2, 3]
    assert all(event["replay"] is True for event in sent)
