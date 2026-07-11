from __future__ import annotations

from queue import Queue
from threading import Thread

from fastapi.testclient import TestClient
from httpx import Response

from app.main import app


def test_browser_host_bridge_publishes_latest_redacted_snapshot() -> None:
    client = TestClient(app, client=("127.0.0.1", 50100))
    snapshot = {
        "sessions": [{"id": "session-1", "current_url": "https://example.test/"}],
        "events": [
            {
                "id": "event-1",
                "type": "action.screenshot",
                "screenshot_url": "data:image/png;base64,raw-private-image",
                "created_at": "2026-07-10T00:00:00Z",
            }
        ],
        "activeSessionId": "session-1",
        "visible": True,
        "hostAvailable": True,
    }

    with client.websocket_connect("/api/ws/browser-host") as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "snapshot", "snapshot": snapshot})
        websocket.send_json({"type": "ping", "request_id": "barrier"})
        assert websocket.receive_json() == {"type": "pong", "request_id": "barrier"}

        response = client.get("/api/browser-host/bridge/snapshot")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["snapshot"]["activeSessionId"] == "session-1"
    assert payload["snapshot"]["events"][0]["screenshot_url"] == "[redacted:screenshot]"
    assert "raw-private-image" not in response.text


def test_browser_host_bridge_allows_only_read_only_actions() -> None:
    response_queue: Queue[Response] = Queue()

    def dispatch_action() -> None:
        request_client = TestClient(app, client=("127.0.0.1", 50101))
        response_queue.put(
            request_client.post(
                "/api/browser-host/bridge/action",
                json={"session_id": "session-1", "action": {"kind": "observe"}},
            )
        )

    client = TestClient(app, client=("127.0.0.1", 50100))
    with client.websocket_connect("/api/ws/browser-host") as websocket:
        assert websocket.receive_json()["type"] == "connected"
        worker = Thread(target=dispatch_action, daemon=True)
        worker.start()

        request = websocket.receive_json()
        assert request["type"] == "action"
        assert request["session_id"] == "session-1"
        assert request["action"] == {"kind": "observe"}
        websocket.send_json(
            {
                "type": "result",
                "request_id": request["request_id"],
                "ok": True,
                "session": {"id": "session-1"},
                "event": {"type": "action.observe"},
            }
        )
        worker.join(timeout=5)

    assert not worker.is_alive()
    response = response_queue.get_nowait()
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "session": {"id": "session-1"},
        "event": {"type": "action.observe"},
        "error": None,
    }


def test_browser_host_bridge_rejects_remote_write_actions() -> None:
    client = TestClient(app, client=("127.0.0.1", 50100))

    response = client.post(
        "/api/browser-host/bridge/action",
        json={"session_id": "session-1", "action": {"kind": "click", "selector": "button"}},
    )

    assert response.status_code == 403


def test_browser_host_bridge_preserves_only_bounded_screenshot_artifact_results() -> None:
    response_queue: Queue[Response] = Queue()

    def dispatch_action() -> None:
        request_client = TestClient(app, client=("127.0.0.1", 50101))
        response_queue.put(
            request_client.post(
                "/api/browser-host/bridge/action",
                json={"session_id": "session-1", "action": {"kind": "screenshot"}},
            )
        )

    artifact_url = (
        "file:///C:/Temp/lengrvis-browser-screenshots-123-"
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.png"
    )
    client = TestClient(app, client=("127.0.0.1", 50100))
    with client.websocket_connect("/api/ws/browser-host") as websocket:
        assert websocket.receive_json()["type"] == "connected"
        worker = Thread(target=dispatch_action, daemon=True)
        worker.start()

        request = websocket.receive_json()
        assert request["action"] == {"kind": "screenshot"}
        websocket.send_json(
            {
                "type": "result",
                "request_id": request["request_id"],
                "ok": True,
                "event": {
                    "type": "action.screenshot",
                    "screenshot_url": artifact_url,
                    "image_url": "data:image/png;base64,raw-private-image",
                },
            }
        )
        worker.join(timeout=5)

    assert not worker.is_alive()
    response = response_queue.get_nowait()
    assert response.status_code == 200
    assert response.json()["event"]["screenshot_url"] == artifact_url
    assert response.json()["event"]["image_url"] == "[redacted:screenshot]"
    assert "raw-private-image" not in response.text


def test_browser_host_bridge_dispatches_task_scoped_cancellation() -> None:
    from app.services.browser_host_bridge_service import browser_host_bridge_hub

    response_queue: Queue[dict] = Queue()

    def dispatch_cancel() -> None:
        import asyncio

        response_queue.put(asyncio.run(browser_host_bridge_hub.request_task_cancel(task_id="task-1")))

    client = TestClient(app, client=("127.0.0.1", 50100))
    with client.websocket_connect("/api/ws/browser-host") as websocket:
        assert websocket.receive_json()["type"] == "connected"
        worker = Thread(target=dispatch_cancel, daemon=True)
        worker.start()

        request = websocket.receive_json()
        assert request["type"] == "cancel_task"
        assert request["task_id"] == "task-1"
        websocket.send_json({"type": "result", "request_id": request["request_id"], "ok": True})
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert response_queue.get_nowait()["ok"] is True
