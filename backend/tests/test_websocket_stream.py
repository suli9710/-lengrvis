from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import MessageType
from app.api.routes_chat import ws_router
from app.orchestration.agent_bus import AgentBus


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_router)
    app.include_router(ws_router, prefix="/api")
    return app


def test_task_websocket_receives_agent_bus_messages(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()
    task_id = "task_ws_stream"

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
            connected = websocket.receive_json()
            assert connected["type"] == "connected"
            assert connected["task_id"] == task_id

            message = AgentBus().publish_text(
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/tasks/task_a") as websocket:
            assert websocket.receive_json()["type"] == "connected"

            AgentBus().publish_text("task_b", "PlannerAgent", "Wrong task")
            AgentBus().publish_text("task_a", "PlannerAgent", "Right task")

            event = websocket.receive_json()

    assert event["type"] == "agent_message"
    assert event["task_id"] == "task_a"
    assert event["message"]["content"] == "Right task"


def test_task_websocket_is_also_available_under_api_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws/tasks/task_api") as websocket:
            assert websocket.receive_json() == {"type": "connected", "task_id": "task_api"}


def test_notification_websocket_streams_system_notifications(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/ws/notifications") as websocket:
            assert websocket.receive_json() == {"type": "connected", "task_id": "__system__"}

            message = AgentBus().publish_text(
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
