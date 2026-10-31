"""Connection pools are owned and closed by the loops that use them."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.config import AppSettings
from app.core.outbound_url import PinnedOutboundRequest
from app.llm import openai_compatible as provider


class LoopOwnedClient:
    def __init__(self, **kwargs):
        self.loop = asyncio.get_running_loop()
        self.trust_env = kwargs["trust_env"]
        self.is_closed = False

    async def aclose(self):
        assert asyncio.get_running_loop() is self.loop
        self.is_closed = True


def test_short_lived_loops_close_and_do_not_reuse_clients(monkeypatch):
    monkeypatch.setattr(provider.httpx, "AsyncClient", LoopOwnedClient)
    clients = []

    async def use_client():
        first = provider._shared_http_client()
        assert provider._shared_http_client() is first
        direct = provider._shared_http_client(trust_env=False)
        assert direct is not first
        assert not direct.trust_env
        clients.extend([first, direct])

    asyncio.run(use_client())
    asyncio.run(use_client())
    assert clients[0] is not clients[2]
    assert all(client.is_closed for client in clients)
    assert not provider._SHARED_HTTP_CLIENTS


def test_shutdown_closes_foreign_loop_clients_on_their_owner(monkeypatch):
    monkeypatch.setattr(provider.httpx, "AsyncClient", LoopOwnedClient)
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    clients = []

    async def prepare():
        clients.append(provider._shared_http_client())
        await asyncio.sleep(0)
        ready.set()

    def drive():
        asyncio.set_event_loop(loop)
        loop.create_task(prepare())
        loop.run_forever()
        loop.close()

    thread = threading.Thread(target=drive)
    thread.start()
    try:
        assert ready.wait(5)
        asyncio.run(provider.close_shared_http_client())
        assert clients[0].is_closed
        assert not provider._SHARED_HTTP_CLIENTS
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def test_close_before_client_lifetime_first_turn(monkeypatch):
    monkeypatch.setattr(provider.httpx, "AsyncClient", LoopOwnedClient)

    async def use_client():
        client = provider._shared_http_client()
        await provider.close_shared_http_client()
        assert client.is_closed
        assert not provider._SHARED_HTTP_CLIENTS

    asyncio.run(use_client())


def test_real_keepalive_requests_succeed_across_sequential_loops():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    async def request():
        response = await provider._shared_http_client(trust_env=False).get(f"http://127.0.0.1:{server.server_port}")
        assert response.status_code == 200

    try:
        asyncio.run(request())
        asyncio.run(request())
        assert not provider._SHARED_HTTP_CLIENTS
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.asyncio
@pytest.mark.parametrize("local_route", [True, False])
async def test_local_provider_requests_bypass_environment_proxy(monkeypatch, local_route):
    calls = []

    class Client:
        async def post(self, url, **kwargs):
            return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    def client_factory(**kwargs):
        calls.append(kwargs.get("trust_env", True))
        return Client()

    instance = provider.OpenAICompatibleProvider(AppSettings())
    monkeypatch.setattr(instance, "_circuit_key", lambda *_: ("test", "test", "test", "test"))
    monkeypatch.setattr(instance, "_allow_private_base", lambda _: local_route)
    monkeypatch.setattr(provider, "_shared_http_client", client_factory)
    monkeypatch.setattr(provider, "pin_outbound_http_url", lambda url, **_: PinnedOutboundRequest(url=url))
    await instance._post_json("http://127.0.0.1/v1/chat/completions", {}, endpoint_kind="chat", model="test")
    assert calls == [not local_route]
