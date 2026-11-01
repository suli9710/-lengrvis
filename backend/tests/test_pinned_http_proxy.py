from __future__ import annotations

import socket
import socketserver
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.security.pinned_http_proxy import PinnedHttpProxy


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = self.request.recv(1024)
        self.request.sendall(data)


class _HttpHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        body = f"{self.headers.get('Host')} {self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def test_connect_tunnel_uses_resolved_ip_instead_of_untrusted_hostname() -> None:
    upstream = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    resolved: list[tuple[str, int]] = []

    def resolve_target(hostname: str, port: int) -> tuple[str, int]:
        resolved.append((hostname, port))
        return "127.0.0.1", port

    try:
        with PinnedHttpProxy(resolve_target=resolve_target) as proxy:
            with socket.create_connection(proxy.address, timeout=2) as client:
                authority = f"rebind.invalid:{upstream.server_address[1]}"
                client.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
                response = client.recv(4096)
                assert response.startswith(b"HTTP/1.1 200")
                client.sendall(b"pinned-connect")
                assert client.recv(1024) == b"pinned-connect"
        assert resolved == [("rebind.invalid", upstream.server_address[1])]
    finally:
        upstream.shutdown()
        upstream.server_close()


def test_http_request_uses_pinned_ip_and_preserves_original_host_header() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _HttpHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    def resolve_target(_hostname: str, port: int) -> tuple[str, int]:
        return "127.0.0.1", port

    try:
        with PinnedHttpProxy(resolve_target=resolve_target) as proxy:
            client = HTTPConnection(*proxy.address, timeout=2)
            port = upstream.server_address[1]
            client.request("GET", f"http://rebind.invalid:{port}/hello?x=1")
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == f"rebind.invalid:{port} /hello?x=1".encode()
            client.close()
    finally:
        upstream.shutdown()
        upstream.server_close()


class _StreamingHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self.send_response(200)
        self.send_header("Connection", "close, X-Upstream-Only")
        self.send_header("X-Upstream-Only", "must-not-be-forwarded")
        if self.path == "/chunked":
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        else:
            self.end_headers()
            self.wfile.write(b"close-delimited body")

    def log_message(self, _format: str, *_args: object) -> None:
        return None


def test_http_proxy_frames_decoded_and_close_delimited_responses() -> None:
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingHttpHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        with PinnedHttpProxy(resolve_target=lambda _host, port: ("127.0.0.1", port)) as proxy:
            for path, expected in (("/chunked", b"hello world"), ("/close", b"close-delimited body")):
                client = HTTPConnection(*proxy.address, timeout=2)
                try:
                    client.request("GET", f"http://upstream.invalid:{upstream.server_port}{path}")
                    response = client.getresponse()
                    assert response.status == 200
                    assert response.getheader("Transfer-Encoding") is None
                    assert response.getheader("X-Upstream-Only") is None
                    assert response.getheader("Connection") == "close"
                    assert response.read() == expected
                finally:
                    client.close()
    finally:
        upstream.shutdown()
        upstream.server_close()


def test_proxy_removes_connection_nominated_request_headers() -> None:
    from email.message import Message
    from types import SimpleNamespace

    from app.security.pinned_http_proxy import _forward_request_headers

    headers = Message()
    headers["Connection"] = "keep-alive, X-Hop-Only"
    headers["X-Hop-Only"] = "private-hop-value"
    headers["X-End-To-End"] = "preserved"
    forwarded = _forward_request_headers(SimpleNamespace(headers=headers), "public.example")
    assert "X-Hop-Only" not in forwarded
    assert forwarded["X-End-To-End"] == "preserved"
    assert forwarded["Connection"] == "close"
    assert forwarded["Host"] == "public.example"
