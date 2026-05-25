"""Tests for the install-local-model endpoint and streaming pull."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core import db


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


@pytest.fixture
def mock_ollama_installed(monkeypatch):
    monkeypatch.setattr("app.services.ollama_service.is_installed", lambda: True)


@pytest.fixture
def mock_ollama_not_installed(monkeypatch):
    monkeypatch.setattr("app.services.ollama_service.is_installed", lambda: False)


@pytest.fixture
def mock_ollama_running(monkeypatch):
    async def _running():
        return True
    monkeypatch.setattr("app.services.ollama_service.is_running", _running)


@pytest.mark.asyncio
async def test_install_local_model_already_installed():
    from app.services import ollama_service

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        async def fake_stream(model=None):
            yield {"status": "success", "model": model or "test"}

        mock_pull.side_effect = fake_stream

        results = []
        async for progress in ollama_service.install_local_model("test-model"):
            results.append(progress)

        assert any(r.get("phase") == "install" and r.get("status") == "skipped" for r in results)
        assert any(r.get("phase") == "switch" for r in results)


@pytest.mark.asyncio
async def test_install_local_model_needs_install():
    from app.services import ollama_service

    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service, "install", new_callable=AsyncMock, return_value={"ok": True}), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        async def fake_stream(model=None):
            yield {"status": "success", "model": model or "test"}

        mock_pull.side_effect = fake_stream

        results = []
        async for progress in ollama_service.install_local_model("test-model"):
            results.append(progress)

        assert any(r.get("phase") == "install" and r.get("status") in ("installing", "done") for r in results)


@pytest.mark.asyncio
async def test_install_local_model_install_fails():
    from app.services import ollama_service

    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service, "install", new_callable=AsyncMock, return_value={"ok": False, "error": "no winget"}):

        results = []
        async for progress in ollama_service.install_local_model():
            results.append(progress)

        assert any(r.get("status") == "error" for r in results)


@pytest.mark.asyncio
async def test_pull_model_streaming_success():
    from app.services import ollama_service
    import httpx

    mock_lines = [
        '{"status":"downloading","total":1000,"completed":500}',
        '{"status":"downloading","total":1000,"completed":1000}',
        '{"status":"success"}',
    ]

    class FakeResponse:
        status_code = 200
        async def aiter_lines(self):
            for line in mock_lines:
                yield line
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aclose(self):
            pass

    class FakeClient:
        def __init__(self, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def stream(self, *args, **kwargs):
            return FakeResponse()

    with patch("httpx.AsyncClient", FakeClient):
        results = []
        async for progress in ollama_service.pull_model_streaming("test"):
            results.append(progress)

        assert len(results) >= 2
        assert any(r.get("percent", 0) > 0 for r in results)


def test_install_local_model_endpoint():
    """Test the REST endpoint via TestClient."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services import ollama_service

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        async def fake_stream(model=None):
            yield {"status": "success", "model": model or "test"}

        mock_pull.side_effect = fake_stream

        client = TestClient(create_app())
        resp = client.post("/api/settings/install-local-model", json={"model": "test"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
