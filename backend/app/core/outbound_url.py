"""Shared SSRF guards for outbound HTTP(S) requests."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlsplit, urlunsplit

from app.config import env_flag

_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".intranet")
# Cloud instance metadata endpoints (AWS/GCP/Azure link-local).
_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})
_BENCHMARK_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_TRUSTED_FAKE_IP_TUN_ENV = "LENGRVIS_TRUSTED_FAKE_IP_TUN"
OutboundResolver = Callable[
    [str],
    Iterable[str | ipaddress.IPv4Address | ipaddress.IPv6Address],
]


def is_local_base_url(url: str) -> bool:
    """Return True when the URL host is clearly local-only (loopback/LAN)."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def validate_outbound_http_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: OutboundResolver | None = None,
) -> str:
    """Validate an outbound HTTP(S) URL and return it unchanged when allowed."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    hostname = parsed.hostname or ""
    if _is_cloud_metadata_host(hostname):
        raise ValueError("URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF.")
    if not allow_private and _is_blocked_outbound_host(hostname, resolver=resolver):
        raise ValueError("URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF.")
    return raw


def validate_outbound_http_url_preview(url: str, *, allow_private: bool = False) -> str:
    """Validate outbound URL syntax and static SSRF indicators without DNS resolution."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are allowed.")
    hostname = parsed.hostname or ""
    if _is_cloud_metadata_host(hostname):
        raise ValueError("URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF.")
    if not allow_private and _is_statically_blocked_host(hostname):
        raise ValueError("URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF.")
    return raw


@dataclass(frozen=True)
class PinnedOutboundRequest:
    """An outbound request whose connect target is pinned to a validated IP.

    ``url`` carries the resolved IP in place of the hostname so the HTTP client
    connects to exactly the address we validated (no second DNS lookup an
    attacker could flip — DNS-rebinding TOCTOU). ``headers``/``extensions``
    restore the original hostname for the Host header and TLS SNI/cert checks.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    extensions: dict[str, str] = field(default_factory=dict)


def pin_outbound_http_url(
    url: str,
    *,
    allow_private: bool = False,
    resolver: OutboundResolver | None = None,
) -> PinnedOutboundRequest:
    """Validate an outbound URL and pin its connect target to a checked IP.

    Returns the URL unchanged (no pin) when the host is already a literal IP,
    when private targets are explicitly allowed (local providers), or when the
    name does not resolve here (the client will fail at connect time anyway).
    Raises ``ValueError`` when validation fails or every resolved address is
    blocked.
    """
    raw = validate_outbound_http_url(url, allow_private=allow_private, resolver=resolver)
    split = urlsplit(raw)
    hostname = split.hostname or ""
    try:
        ipaddress.ip_address(hostname.split("%")[0])
        return PinnedOutboundRequest(url=raw)
    except ValueError:
        pass
    if allow_private:
        if _is_cloud_metadata_host(hostname):
            raise ValueError(
                "URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF."
            )
        return PinnedOutboundRequest(url=raw)
    pinned_ip = _resolve_pinned_outbound_ip(hostname, resolver=resolver)
    if pinned_ip is None:
        raise ValueError("Outbound URL hostname could not be resolved; refusing unpinned connect to prevent SSRF.")
    host_for_url = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = host_for_url if split.port is None else f"{host_for_url}:{split.port}"
    pinned_url = urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))
    host_header = hostname if split.port is None else f"{hostname}:{split.port}"
    extensions = {"sni_hostname": hostname} if split.scheme == "https" else {}
    return PinnedOutboundRequest(url=pinned_url, headers={"Host": host_header}, extensions=extensions)


def _resolve_pinned_outbound_ip(hostname: str, *, resolver: OutboundResolver | None = None) -> str | None:
    for ip in _resolved_outbound_ips(hostname, resolver=resolver):
        if _is_blocked_ip(ip) and not _is_explicitly_trusted_fake_ip_answer(hostname, ip):
            continue
        return str(ip)
    # The name resolved but only to blocked addresses: the benign answer seen
    # during validation was rebound underneath us. Fail closed.
    raise ValueError("URLs targeting loopback, private, link-local, or metadata hosts are blocked to prevent SSRF.")


def _is_cloud_metadata_host(hostname: str) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().rstrip(".")
    if lowered in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(lowered.split("%")[0])
    except ValueError:
        return False
    return ip == ipaddress.ip_address("169.254.169.254")


def _is_statically_blocked_host(hostname: str) -> bool:
    if not hostname:
        return True
    if _is_cloud_metadata_host(hostname):
        return True
    lowered = hostname.lower().rstrip(".")
    try:
        return _is_blocked_ip(ipaddress.ip_address(lowered.split("%")[0]))
    except ValueError:
        pass
    return lowered == "localhost" or lowered.endswith(_LOCAL_HOST_SUFFIXES) or "." not in lowered


def _is_blocked_outbound_host(hostname: str, *, resolver: OutboundResolver | None = None) -> bool:
    if _is_statically_blocked_host(hostname):
        return True
    for ip in _resolved_outbound_ips(hostname, resolver=resolver):
        if _is_blocked_ip(ip) and not _is_explicitly_trusted_fake_ip_answer(hostname, ip):
            return True
    return False


def _resolved_outbound_ips(
    hostname: str,
    *,
    resolver: OutboundResolver | None = None,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve a hostname once through an injectable, deterministic boundary."""

    try:
        raw_addresses = tuple(resolver(hostname)) if resolver is not None else _system_resolve_outbound_ips(hostname)
    except (OSError, TypeError) as exc:
        raise ValueError("Outbound URL hostname could not be resolved; refusing connect to prevent SSRF.") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for raw_address in raw_addresses:
        try:
            addresses.append(ipaddress.ip_address(str(raw_address).split("%")[0]))
        except (TypeError, ValueError):
            continue
    if not addresses:
        raise ValueError("Outbound URL hostname could not be resolved; refusing connect to prevent SSRF.")
    return tuple(addresses)


def _system_resolve_outbound_ips(hostname: str) -> tuple[str, ...]:
    infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    return tuple(str(info[4][0]) for info in infos)


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _is_explicitly_trusted_fake_ip_answer(
    hostname: str,
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Allow a TUN-managed fake DNS answer without allowing literal reserved-IP URLs.

    Operators must explicitly opt in because 198.18.0.0/15 is otherwise a
    reserved/blocked range. Literal IP targets remain blocked by the static URL
    validation path, and all private/link-local/metadata ranges remain blocked.
    """
    if not env_flag(_TRUSTED_FAKE_IP_TUN_ENV):
        return False
    try:
        ipaddress.ip_address(hostname.split("%")[0])
    except ValueError:
        pass
    else:
        return False
    return isinstance(ip, ipaddress.IPv4Address) and ip in _BENCHMARK_FAKE_IP_NETWORK
