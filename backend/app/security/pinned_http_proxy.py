"""Loopback-only HTTP CONNECT proxy with connect-time DNS/IP pinning."""

from __future__ import annotations

import select
import socket
import threading
from collections.abc import Callable
from contextlib import suppress
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urlunsplit

from app.core.outbound_url import pin_outbound_http_url

PinnedTargetResolver = Callable[[str, int], tuple[str, int]]
BlockCallback = Callable[[str], None]


class _ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class PinnedHttpProxy:
    """Proxy Chromium traffic through an IP selected and checked at connect time."""

    def __init__(
        self,
        *,
        allow_private: bool = False,
        resolve_target: PinnedTargetResolver | None = None,
        on_block: BlockCallback | None = None,
    ) -> None:
        self._allow_private = allow_private
        self._resolve_target = resolve_target or self._resolve_pinned_target
        self._on_block = on_block
        self._server: _ProxyServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("Pinned HTTP proxy is not running")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> PinnedHttpProxy:
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_CONNECT(self) -> None:  # noqa: N802 - stdlib handler contract
                owner._handle_connect(self)

            def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler contract
                owner._handle_http(self)

            do_GET = do_DELETE
            do_HEAD = do_DELETE
            do_OPTIONS = do_DELETE
            do_PATCH = do_DELETE
            do_POST = do_DELETE
            do_PUT = do_DELETE

            def log_message(self, _format: str, *_args: object) -> None:
                return None

        self._server = _ProxyServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="pinned-http-proxy", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def __enter__(self) -> PinnedHttpProxy:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _resolve_pinned_target(self, hostname: str, port: int) -> tuple[str, int]:
        authority = f"[{hostname}]:{port}" if ":" in hostname else f"{hostname}:{port}"
        pinned = pin_outbound_http_url(f"https://{authority}/", allow_private=self._allow_private)
        pinned_host = urlsplit(pinned.url).hostname
        if not pinned_host:
            raise ValueError("Proxy target could not be pinned")
        return pinned_host, port

    def _handle_connect(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            hostname, port = _parse_authority(handler.path, default_port=443)
            pinned_host, pinned_port = self._resolve_target(hostname, port)
            upstream = socket.create_connection((pinned_host, pinned_port), timeout=15)
        except (OSError, ValueError) as exc:
            self._report_block(exc)
            handler.send_error(403, "Blocked proxy target")
            return
        try:
            handler.send_response(200, "Connection Established")
            handler.end_headers()
            _relay_bidirectional(handler.connection, upstream)
        finally:
            with suppress(OSError):
                upstream.close()

    def _handle_http(self, handler: BaseHTTPRequestHandler) -> None:
        connection: HTTPConnection | None = None
        try:
            parsed = urlsplit(handler.path)
            if parsed.scheme != "http" or not parsed.hostname:
                raise ValueError("Only absolute http proxy targets are allowed")
            port = parsed.port or 80
            pinned_host, pinned_port = self._resolve_target(parsed.hostname, port)
            body = _read_request_body(handler)
            headers = _forward_request_headers(handler, parsed.netloc)
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection = HTTPConnection(pinned_host, pinned_port, timeout=30)
            connection.request(handler.command, target, body=body, headers=headers)
            response = connection.getresponse()
            handler.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in _HOP_BY_HOP_HEADERS:
                    handler.send_header(name, value)
            handler.end_headers()
            if handler.command != "HEAD":
                while chunk := response.read(64 * 1024):
                    handler.wfile.write(chunk)
        except (OSError, ValueError) as exc:
            self._report_block(exc)
            handler.send_error(403, "Blocked proxy target")
        finally:
            if connection is not None:
                connection.close()

    def _report_block(self, exc: BaseException) -> None:
        if self._on_block is not None:
            self._on_block(str(exc) or "Blocked proxy target")


def _parse_authority(authority: str, *, default_port: int) -> tuple[str, int]:
    parsed = urlsplit(f"//{str(authority or '').strip()}")
    hostname = parsed.hostname or ""
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError("Invalid proxy target port") from exc
    if not hostname or not 1 <= port <= 65535:
        raise ValueError("Invalid proxy target")
    return hostname, port


def _relay_bidirectional(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    while True:
        readable, _, exceptional = select.select(sockets, [], sockets, 30)
        if exceptional or not readable:
            return
        for source in readable:
            try:
                data = source.recv(64 * 1024)
            except OSError:
                return
            if not data:
                return
            target = upstream if source is client else client
            try:
                target.sendall(data)
            except OSError:
                return


_HOP_BY_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}
)


def _read_request_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").strip().lower()
    if transfer_encoding:
        raise ValueError("Chunked proxy request bodies are not supported")
    raw_length = str(handler.headers.get("Content-Length") or "0").strip()
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ValueError("Invalid proxy request content length") from exc
    if length < 0 or length > 16 * 1024 * 1024:
        raise ValueError("Proxy request body is too large")
    return handler.rfile.read(length) if length else None


def _forward_request_headers(handler: BaseHTTPRequestHandler, authority: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in handler.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS and name.lower() != "host"
    }
    headers["Host"] = authority
    headers["Connection"] = "close"
    return headers
