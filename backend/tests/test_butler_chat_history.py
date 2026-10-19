from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_butler import router
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter
from app.services.butler_service import ButlerCoordinator
from app.services.task_service import list_chat_messages


def test_butler_greeting_and_task_restore_the_original_chat_turns(monkeypatch):
    coordinator = ButlerCoordinator()
    monkeypatch.setattr(coordinator, "_spawn", lambda _: None)
    monkeypatch.setattr("app.api.routes_butler.get_butler", lambda: coordinator)
    store.set_control(paused=True)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_api_route("/api/chat/messages", list_chat_messages)
    with TestClient(app) as client:
        sent = []
        for content in ("你好", "整理我的项目进度", "查看管家进度", "恢复管家"):
            response = client.post("/api/butler/messages", json={"message": content})
            assert response.status_code == 200, response.text
            turn = response.json()
            assert turn["user_message"]["content"] == content
            assert turn["assistant_message"]["content"] == turn["reply"]
            sent.extend([turn["user_message"], turn["assistant_message"]])
        assert client.get("/api/chat/messages").json() == sent
        # Reopen the durable storage and replace the in-memory coordinator.
        db.close_thread_connection()
        restored = ButlerCoordinator()
        monkeypatch.setattr("app.api.routes_butler.get_butler", lambda: restored)
        assert client.get("/api/chat/messages").json() == sent
    assert len({message["id"] for message in sent}) == len(sent)
    assert len(store.list_records(ButlerMatter)) == 1


def test_butler_rejected_credentials_are_not_written_to_chat(monkeypatch):
    db.init_db()
    monkeypatch.setattr("app.api.routes_butler.get_butler", ButlerCoordinator)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        result = client.post("/butler/messages", json={"message": "api_key=sk-private-super-secret-value-123456"})
    assert result.status_code == 400
    assert list_chat_messages() == []
