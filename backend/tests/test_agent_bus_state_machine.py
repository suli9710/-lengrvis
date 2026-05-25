from __future__ import annotations

import pytest

from conftest import import_first, require_attr


BUS_MODULES = (
    "backend.agent.bus",
    "backend.agents.bus",
    "backend.core.agent_bus",
    "mavris.agent.bus",
)

STATE_MODULES = (
    "backend.agent.state_machine",
    "backend.agents.state_machine",
    "backend.core.state_machine",
    "mavris.agent.state_machine",
)


def test_agent_bus_publishes_events_in_order():
    module = import_first(BUS_MODULES)
    bus_cls = require_attr(module, ("AgentBus", "EventBus", "MessageBus"))
    bus = bus_cls()
    received = []

    if hasattr(bus, "subscribe"):
        bus.subscribe("task.created", received.append)
    else:
        pytest.skip(f"{bus_cls.__name__} does not expose subscribe")

    if hasattr(bus, "publish"):
        bus.publish("task.created", {"id": "task-1"})
        bus.publish("task.created", {"id": "task-2"})
    elif hasattr(bus, "emit"):
        bus.emit("task.created", {"id": "task-1"})
        bus.emit("task.created", {"id": "task-2"})
    else:
        pytest.skip(f"{bus_cls.__name__} does not expose publish/emit")

    ids = [
        item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        for item in received
    ]
    assert ids == ["task-1", "task-2"]


def test_state_machine_rejects_invalid_transition():
    module = import_first(STATE_MODULES)
    machine_cls = require_attr(module, ("AgentStateMachine", "StateMachine", "TaskStateMachine"))
    machine = machine_cls()

    transition = getattr(machine, "transition", None) or getattr(machine, "move_to", None)
    if transition is None:
        pytest.skip(f"{machine_cls.__name__} does not expose transition/move_to")

    with pytest.raises((ValueError, RuntimeError, PermissionError)):
        transition("completed")


def test_state_machine_can_follow_happy_path():
    module = import_first(STATE_MODULES)
    machine_cls = require_attr(module, ("AgentStateMachine", "StateMachine", "TaskStateMachine"))
    machine = machine_cls()

    transition = getattr(machine, "transition", None) or getattr(machine, "move_to", None)
    if transition is None:
        pytest.skip(f"{machine_cls.__name__} does not expose transition/move_to")

    for state in ("queued", "running", "completed"):
        transition(state)

    current = getattr(machine, "state", getattr(machine, "current_state", None))
    assert str(current).lower().endswith("completed")
