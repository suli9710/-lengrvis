"""Tests for shared outbound URL SSRF validation."""

from __future__ import annotations

import socket

import pytest

from app.core import outbound_url
from app.core.outbound_url import (
    is_local_base_url,
    pin_outbound_http_url,
    validate_outbound_http_url,
    validate_outbound_http_url_preview,
)


def _addrinfo(*addresses: str) -> list[tuple]:
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
        for address in addresses
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost/admin",
        "http://[::1]:8000/",
        "http://10.0.0.5/",
        "http://172.16.1.1/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://router.local/",
        "http://intranet/",
    ],
)
def test_validate_outbound_http_url_blocks_private_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="blocked to prevent SSRF"):
        validate_outbound_http_url(url, allow_private=False)


def test_validate_outbound_http_url_allows_private_when_opted_in() -> None:
    url = "http://192.168.1.1/router"
    assert validate_outbound_http_url(url, allow_private=True) == url


def test_validate_outbound_http_url_allows_public_hosts() -> None:
    url = "https://api.openai.com/v1"
    assert validate_outbound_http_url(url, allow_private=False) == url


def test_validate_outbound_http_url_preview_allows_unresolvable_public_hostnames() -> None:
    url = "https://hooks.example.test/lengrvis"
    assert validate_outbound_http_url_preview(url, allow_private=False) == url


def test_validate_outbound_http_url_preview_blocks_static_private_hosts() -> None:
    with pytest.raises(ValueError, match="blocked to prevent SSRF"):
        validate_outbound_http_url_preview("http://127.0.0.1/hook", allow_private=False)


def test_validate_outbound_http_url_rejects_unresolvable_public_hostnames() -> None:
    url = "https://example.test/page"
    with pytest.raises(ValueError, match="could not be resolved"):
        validate_outbound_http_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "not-a-url",
        "http://",
    ],
)
def test_validate_outbound_http_url_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(ValueError, match="Only absolute http"):
        validate_outbound_http_url(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434", True),
        ("http://localhost:8080", True),
        ("https://api.openai.com/v1", False),
        ("", False),
    ],
)
def test_is_local_base_url(url: str, expected: bool) -> None:
    assert is_local_base_url(url) is expected


def test_pin_outbound_http_url_rewrites_host_to_validated_ip(monkeypatch) -> None:
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: _addrinfo("93.184.216.34"),
    )

    pinned = pin_outbound_http_url("https://api.example.com/v1/chat?x=1", allow_private=False)

    assert pinned.url == "https://93.184.216.34/v1/chat?x=1"
    assert pinned.headers == {"Host": "api.example.com"}
    assert pinned.extensions == {"sni_hostname": "api.example.com"}


def test_pin_outbound_http_url_preserves_explicit_port_and_skips_sni_for_http(monkeypatch) -> None:
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: _addrinfo("93.184.216.34"),
    )

    pinned = pin_outbound_http_url("http://api.example.com:8080/hook", allow_private=False)

    assert pinned.url == "http://93.184.216.34:8080/hook"
    assert pinned.headers == {"Host": "api.example.com:8080"}
    assert pinned.extensions == {}


def test_pin_outbound_http_url_leaves_literal_ip_urls_unpinned() -> None:
    pinned = pin_outbound_http_url("http://192.168.1.1/router", allow_private=True)

    assert pinned.url == "http://192.168.1.1/router"
    assert pinned.headers == {}
    assert pinned.extensions == {}


def test_pin_outbound_http_url_fails_closed_when_dns_rebinds_to_private(monkeypatch) -> None:
    """Validation sees a public answer; the connect-time lookup flips private."""
    answers = iter([_addrinfo("93.184.216.34"), _addrinfo("127.0.0.1", "10.0.0.5")])
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: next(answers),
    )

    with pytest.raises(ValueError, match="blocked to prevent SSRF"):
        pin_outbound_http_url("https://rebind.example.com/", allow_private=False)


def test_pin_outbound_http_url_rejects_unresolvable_hosts(monkeypatch) -> None:
    def raise_gaierror(host, *args, **kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(outbound_url.socket, "getaddrinfo", raise_gaierror)

    with pytest.raises(ValueError, match="could not be resolved"):
        pin_outbound_http_url("https://example.test/page", allow_private=False)


def test_pin_outbound_http_url_pins_tunnel_fake_ips(monkeypatch) -> None:
    monkeypatch.setattr(
        outbound_url.socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: _addrinfo("198.18.0.42"),
    )

    pinned = pin_outbound_http_url("https://api.example.com/v1", allow_private=False)

    assert pinned.url == "https://198.18.0.42/v1"
    assert pinned.headers == {"Host": "api.example.com"}
