from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

import pytest

from app.llm import local_provider
from app.services import guardian_runtime, ollama_service


@pytest.fixture
def isolated_http_servers(monkeypatch):
    captured: dict[str, list[tuple[str, str]]] = {"backend": [], "proxy": []}
    servers = []
    for label in captured:

        def make_handler(target):
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802 - stdlib handler contract
                    captured[target].append((self.path, self.headers.get("x-lengrvis-desktop-token", "")))
                    body = b'{"models":[{"name":"test-model"}],"activeRunIds":[]}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_args):
                    pass

            return Handler

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(label))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    for key in os.environ:
        if key.lower().endswith("_proxy"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{servers[1].server_port}")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        yield f"http://127.0.0.1:{servers[0].server_port}", captured
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_guardian_keeps_desktop_token_off_environment_proxy(monkeypatch, isolated_http_servers):
    base_url, captured = isolated_http_servers
    monkeypatch.setattr(guardian_runtime, "FULL_BACKEND_URL", base_url)
    monkeypatch.setattr(
        guardian_runtime, "desktop_api_token_headers", lambda: {"x-lengrvis-desktop-token": "test-only-marker"}
    )
    runtime = guardian_runtime.GuardianRuntime()
    runtime.shell_mode = "foreground"
    monkeypatch.setattr(runtime, "ensure_full_backend", AsyncMock())
    response = asyncio.run(runtime.proxy("GET", "/api/runtime/status"))
    assert response.status_code == 200
    assert captured["backend"] == [("/api/runtime/status", "test-only-marker")]
    assert captured["proxy"] == []


def test_local_model_detection_ignores_environment_proxy(monkeypatch, isolated_http_servers):
    base_url, captured = isolated_http_servers
    monkeypatch.setattr(local_provider, "_PROBES", [("ollama", f"{base_url}/api/tags", f"{base_url}/v1")])
    backend = local_provider.detect_local_backend()
    assert backend is not None and backend.models == ["test-model"]
    assert len(captured["backend"]) == 1
    assert captured["proxy"] == []


def test_ollama_health_and_model_list_ignore_environment_proxy(monkeypatch, isolated_http_servers):
    base_url, captured = isolated_http_servers
    monkeypatch.setattr(ollama_service, "OLLAMA_API", base_url)
    assert asyncio.run(ollama_service.is_running()) is True
    assert asyncio.run(ollama_service.list_models()) == ["test-model"]
    assert len(captured["backend"]) == 2
    assert captured["proxy"] == []


def test_guardian_websocket_keeps_token_off_environment_proxy(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.api import routes_guardian

    captured = []

    @asynccontextmanager
    async def connect(_target, **kwargs):
        captured.append(kwargs)
        yield object()

    websocket = SimpleNamespace(
        url=SimpleNamespace(path="/ws/tasks/test", query=""),
        accept=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(routes_guardian, "close_unauthorized_desktop_websocket", AsyncMock(return_value=False))
    monkeypatch.setattr(routes_guardian, "desktop_websocket_subprotocol", lambda _ws: None)
    monkeypatch.setattr(
        routes_guardian, "desktop_api_token_headers", lambda: {"x-lengrvis-desktop-token": "test-only-marker"}
    )
    monkeypatch.setattr(routes_guardian.runtime, "shell_mode", "foreground")
    monkeypatch.setattr(routes_guardian.runtime, "ensure_full_backend", AsyncMock())
    monkeypatch.setattr(routes_guardian.websockets, "connect", connect)
    monkeypatch.setattr(routes_guardian, "_client_to_upstream", AsyncMock())
    monkeypatch.setattr(routes_guardian, "_upstream_to_client", AsyncMock())

    asyncio.run(routes_guardian.proxy_websocket(websocket, "ws/tasks/test"))
    assert len(captured) == 1
    assert captured[0]["proxy"] is None
    assert captured[0]["additional_headers"]["x-lengrvis-desktop-token"] == "test-only-marker"
