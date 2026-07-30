from __future__ import annotations

import asyncio

from app.services.approval_event_service import ApprovalEventBus


def test_approval_event_bus_subscriptions_are_instance_scoped() -> None:
    async def exercise() -> None:
        first = ApprovalEventBus()
        second = ApprovalEventBus()
        first_queue = first.subscribe()
        second_queue = second.subscribe()

        first.publish({"type": "approval_created", "approval": {"id": "approval-1"}})
        await asyncio.sleep(0)

        assert (await asyncio.wait_for(first_queue.get(), timeout=1))["approval"]["id"] == "approval-1"
        assert second_queue.empty()

    asyncio.run(exercise())


def test_approval_event_bus_drops_loop_that_closes_during_publish() -> None:
    class ClosingLoop:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, callback, *args):  # noqa: ANN001, ARG002
            raise RuntimeError("event loop is closed")

    bus = ApprovalEventBus()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    subscription = (ClosingLoop(), queue)
    bus._subscriptions.add(subscription)  # noqa: SLF001 - simulate the close race after subscription.

    bus.publish({"type": "approval_created", "approval": {"id": "approval-1"}})

    assert subscription not in bus._subscriptions  # noqa: SLF001
