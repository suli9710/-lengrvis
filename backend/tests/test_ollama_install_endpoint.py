"""Tests for the install-local-model endpoint and streaming pull."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core import db
from app.security.desktop_api import DESKTOP_API_WS_PROTOCOL_PREFIX


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
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


@pytest.fixture(autouse=True)
def _mock_ready_hardware(monkeypatch):
    monkeypatch.setattr(
        "app.services.ollama_service.hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
            "memory_total_bytes": 16 * 1024**3,
            "disk_free_bytes": 32 * 1024**3,
            "cpu_logical_cores": 8,
            "gpu_summary": "",
        },
    )


@pytest.mark.asyncio
async def test_install_local_model_already_installed():
    from app.services import ollama_service

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "_bundled_model_available", return_value=False), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        async def fake_stream(model=None):
            yield {"status": "success", "model": model or "test"}

        mock_pull.side_effect = fake_stream

        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
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
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
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
        assert results[-1]["repair_action"]["code"] == "install_runtime"
        assert any(item["key"] == "runtime" for item in results[-1]["evidence"])


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
        async for progress in ollama_service.pull_model_streaming("qwen2.5:3b"):
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
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "_bundled_model_available", return_value=False), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        async def fake_stream(model=None):
            yield {"status": "success", "model": model or "test"}

        mock_pull.side_effect = fake_stream

        client = TestClient(create_app())
        resp = client.post("/api/settings/install-local-model", json={"model": "qwen2.5:3b"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


def test_install_local_model_endpoint_does_not_claim_hidden_bundled_model_ready():
    """Bundled files alone are not enough; Ollama must list the model before success."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services import ollama_service

    running_states = iter([False, True])

    async def _is_running():
        return next(running_states, True)

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_is_running), \
         patch.object(ollama_service, "start_server", new_callable=AsyncMock, return_value={"ok": True, "models_dir_configured": True}), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "_bundled_model_available", return_value=True), \
         patch.object(ollama_service, "pull_model_streaming") as mock_pull:

        client = TestClient(create_app())
        resp = client.post("/api/settings/install-local-model", json={"model": "qwen2.5:3b"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["final"]["status"] == "error"
    assert data["final"]["next_action"] == "download_model"
    assert data["final"]["repair_action"]["code"] == "download_model"
    assert not any(item.get("phase") == "switch" for item in data["progress"])
    mock_pull.assert_not_called()


def test_install_local_model_endpoint_restricts_model_name():
    """The install endpoint must not pass arbitrary model names to Ollama."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.post("/api/settings/install-local-model", json={"model": "../huge:latest"})
    assert resp.status_code == 400


def test_install_local_model_websocket_requires_desktop_token(monkeypatch):
    """The streaming endpoint is a mutating desktop operation and needs WS auth."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import create_app

    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/settings/install-local-model?model=qwen2.5:3b"):
            raise AssertionError("install-local-model websocket should require desktop token")

    assert exc_info.value.code == 1008


def test_install_local_model_websocket_restricts_model_name(monkeypatch):
    """Even authenticated WS clients can only install approved local models."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from app.main import create_app

    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/settings/install-local-model?model=not-approved:70b",
            subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}desktop-secret"],
        ) as websocket:
            assert websocket.receive_json()["status"] == "error"

    assert exc_info.value.code == 1008


def test_install_local_model_endpoint_rejects_unsupported_model():
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.post("/api/settings/install-local-model", json={"model": "arbitrary:latest"})

    assert resp.status_code == 400
    assert "restricted" in resp.json()["detail"]


def test_ollama_pull_endpoint_restricts_model_name():
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.post("/api/settings/ollama/pull", json={"model": "not-approved:70b"})

    assert resp.status_code == 400
    assert "restricted" in resp.json()["detail"]


def test_local_model_readiness_endpoint():
    """Test the hardware readiness endpoint used by the desktop setup UI."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.get("/api/settings/local-model/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_install"] is True
    assert data["recommended_model"] == "qwen2.5:3b"


def test_local_model_setup_plan_endpoint(mock_ollama_not_installed):
    """Test the privacy onboarding setup plan endpoint."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app())
    resp = client.get("/api/settings/local-model/setup-plan?model=qwen2.5:3b")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["next_action"] == "install_runtime"
    assert data["model"] == "qwen2.5:3b"
    assert data["repair_action"]["code"] == "install_runtime"
    assert any(item["key"] == "runtime" for item in data["evidence"])
    assert data["steps"][0]["key"] == "hardware"


def test_local_llm_health_endpoint_redacts_paths_urls_and_tokens(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import routes_settings
    from app.main import create_app

    monkeypatch.setattr(
        routes_settings,
        "health_snapshot",
        lambda settings: {
            "available": False,
            "selected_backend": {"kind": "ollama", "base_url": "http://127.0.0.1:11434/v1", "models": []},
            "probe_order": ["ollama"],
            "onnx": {
                "llm": {
                    "onnx_model_path": r"C:\Users\Suli\models\private-model.onnx",
                    "cache_dir": r"C:\Users\Suli\AppData\Local\Lengrvis\onnx-cache",
                },
                "bundle": {"manifest_path": r"C:\Users\Suli\models\manifest.json"},
            },
            "error": r"Tried http://127.0.0.1:11434/api/tags with token=sk-1234567890abcdef at C:\Users\Suli\.ollama",
        },
    )

    client = TestClient(create_app())
    resp = client.get("/api/settings/local-llm/health")

    assert resp.status_code == 200
    payload = resp.json()
    text = str(payload)
    assert payload["selected_backend"]["base_url"] == ""
    assert payload["onnx"]["llm"]["onnx_model_path"] == ""
    assert payload["onnx"]["llm"]["cache_dir"] == ""
    assert payload["onnx"]["bundle"]["manifest_path"] == ""
    assert "http://127.0.0.1" not in text
    assert "sk-1234567890abcdef" not in text
    assert "C:\\Users" not in text
    assert "private-model.onnx" not in text
    assert "manifest.json" not in text
    assert "[REDACTED_URL]" in payload["error"]
