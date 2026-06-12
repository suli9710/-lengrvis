from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import MessageType
from app.api.routes_chat import bus as chat_bus, ws_router
from app.orchestration.agent_bus import AgentBus


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    app.include_router(ws_router, prefix="/api")
    return app


def test_task_websocket_receives_agent_bus_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()
    task_id = "task_ws_stream"

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
            connected = websocket.receive_json()
            assert connected["type"] == "connected"
            assert connected["task_id"] == task_id

            message = chat_bus.publish_text(
                task_id,
                "PlannerAgent",
                "Plan ready over WebSocket.",
                message_type=MessageType.PROPOSAL,
                structured_payload={"phase": "plan"},
            )

            event = websocket.receive_json()

    assert event["type"] == "agent_message"
    assert event["task_id"] == task_id
    assert event["message"]["id"] == message.id
    assert event["message"]["content"] == "Plan ready over WebSocket."
    assert event["message"]["metadata"]["structured_payload"] == {"phase": "plan"}


def test_task_websocket_replays_persisted_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()
    task_id = "task_ws_replay"
    message = AgentBus().publish_text(
        task_id,
        "PlannerAgent",
        "Already persisted before connect.",
        message_type=MessageType.OBSERVATION,
    )

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
            assert websocket.receive_json() == {"type": "connected", "task_id": task_id}
            event = websocket.receive_json()

    assert event["type"] == "agent_message"
    assert event["task_id"] == task_id
    assert event["message"]["id"] == message.id
    assert event["message"]["content"] == "Already persisted before connect."


def test_task_websocket_is_scoped_to_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks/task_a") as websocket:
            assert websocket.receive_json()["type"] == "connected"

            chat_bus.publish_text("task_b", "PlannerAgent", "Wrong task")
            chat_bus.publish_text("task_a", "PlannerAgent", "Right task")

            event = websocket.receive_json()

    assert event["type"] == "agent_message"
    assert event["task_id"] == "task_a"
    assert event["message"]["content"] == "Right task"


def test_task_websocket_rebinds_to_orchestrator_bus_bound_after_connect(monkeypatch, tmp_path):
    """R4-M7 guard: a socket that connected before the orchestrator bound its
    bus must start receiving messages published on the late-bound bus."""
    from types import SimpleNamespace

    from app.orchestration.orchestrator_registry import orchestrator_registry

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()
    task_id = "task_ws_rebind"
    late_bus = AgentBus()

    try:
        with TestClient(app) as client:
            with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
                assert websocket.receive_json()["type"] == "connected"

                # Orchestrator binds its own bus only after the socket connected.
                orchestrator_registry.bind(task_id=task_id, orchestrator=SimpleNamespace(bus=late_bus))
                message = late_bus.publish_text(task_id, "PlannerAgent", "Published on the late-bound bus.")

                event = websocket.receive_json()

        assert event["type"] == "agent_message"
        assert event["message"]["id"] == message.id
        assert event["message"]["content"] == "Published on the late-bound bus."
    finally:
        orchestrator_registry.release_task(task_id)


def test_task_websocket_is_also_available_under_api_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws/tasks/task_api") as websocket:
            assert websocket.receive_json() == {"type": "connected", "task_id": "task_api"}


def test_notification_websocket_streams_system_notifications(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/notifications") as websocket:
            assert websocket.receive_json() == {"type": "connected", "task_id": "__system__"}

            message = chat_bus.publish_text(
                "__system__",
                "NotificationService",
                "Background job finished.",
                message_type=MessageType.NOTIFICATION,
                structured_payload={
                    "title": "Job done",
                    "body": "Background job finished.",
                    "severity": "info",
                },
            )

            event = websocket.receive_json()

    assert event["type"] == "agent_message"
    assert event["task_id"] == "__system__"
    assert event["message"]["id"] == message.id
    assert event["message"]["metadata"]["message_type"] == MessageType.NOTIFICATION.value
    assert event["message"]["metadata"]["structured_payload"]["title"] == "Job done"
