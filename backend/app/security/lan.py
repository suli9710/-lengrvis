from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import env_flag, get_env

LAN_PUBLIC_HTTP_PATHS = {"/health", "/api/health"}
MOBILE_TOKEN_HTTP_PATHS = {"/api/pair", "/api/pair/confirm"}
MOBILE_SECURE_TRANSPORT_ERROR = (
    "Remote mobile pairing and mobile APIs require HTTPS/WSS unless the client is on this computer."
)
DESKTOP_SECURE_TRANSPORT_ERROR = "Remote desktop API access requires HTTPS/WSS unless the client is on this computer."
UNTRUSTED_PROXY_HEADERS_ERROR = "Forwarded proxy headers require explicit trusted proxy configuration."
INVALID_PROXY_HEADERS_ERROR = "Forwarded proxy headers are malformed."
TRUSTED_PROXY_IPS_ENV = "LENGRVIS_TRUSTED_PROXY_IPS"
TRUSTED_PROXY_ALIASES_ENV = "LENGRVIS_TRUSTED_PROXIES"
FORWARDED_HEADER_NAMES = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
)
_HOST_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class ClientTransport:
    client_host: str
    scheme: str
    proxy_error: str = ""
    used_forwarded_headers: bool = False


def is_loopback_host(host: str | None) -> bool:
    # Fail closed: an absent/unknown client host must never be treated as local.
    if not host:
        return False
    normalized = normalize_host_for_security(host)
    if normalized in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def allow_lan_desktop_api() -> bool:
    return env_flag("LENGRVIS_ALLOW_LAN_DESKTOP_API")


def allow_remote_lan_desktop_api(client_host: str | None, scheme: str | None) -> bool:
    return allow_lan_desktop_api() and not is_loopback_host(client_host) and is_secure_transport_scheme(scheme)


def client_transport_from_request(request) -> ClientTransport:
    client_host = request.client.host if request.client else ""
    return resolve_client_transport(
        client_host=client_host,
        scheme=request.url.scheme,
        headers=request.headers,
    )


def client_transport_from_websocket(websocket) -> ClientTransport:
    client_host = websocket.client.host if websocket.client else ""
    return resolve_client_transport(
        client_host=client_host,
        scheme=websocket.url.scheme,
        headers=websocket.headers,
    )


def resolve_client_transport(
    *,
    client_host: str | None,
    scheme: str | None,
    headers: Mapping[str, str] | None,
) -> ClientTransport:
    base = ClientTransport(client_host=client_host or "", scheme=_normalize_scheme(scheme))
    if not _has_forwarded_headers(headers):
        return base
    if not is_trusted_proxy_host(client_host):
        return ClientTransport(
            client_host=base.client_host,
            scheme=base.scheme,
            proxy_error=UNTRUSTED_PROXY_HEADERS_ERROR,
        )
    forwarded_host = _forwarded_client_host(headers)
    forwarded_scheme = _forwarded_scheme(headers) or base.scheme
    if not forwarded_host:
        return ClientTransport(
            client_host=base.client_host,
            scheme=base.scheme,
            proxy_error=INVALID_PROXY_HEADERS_ERROR,
            used_forwarded_headers=True,
        )
    return ClientTransport(
        client_host=forwarded_host,
        scheme=forwarded_scheme,
        used_forwarded_headers=True,
    )


def is_trusted_proxy_host(host: str | None) -> bool:
    normalized = normalize_host_for_security(host)
    if not normalized:
        return False
    trusted = _trusted_proxy_networks()
    if not trusted and normalized in _trusted_proxy_hostnames():
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized in _trusted_proxy_hostnames()
    return any(address in network for network in trusted)


def normalize_host_for_security(host: str | None) -> str:
    normalized = str(host or "").strip().lower()
    if normalized.startswith("[") and "]" in normalized:
        normalized = normalized[1 : normalized.index("]")]
    elif normalized.count(":") == 1:
        candidate, maybe_port = normalized.rsplit(":", 1)
        if maybe_port.isdigit():
            normalized = candidate
    return normalized


def require_secure_non_loopback_bind(
    host: str | None,
    *,
    tls_enabled: bool,
    cert_file: str = "",
    key_file: str = "",
) -> None:
    if _is_loopback_bind_host(host):
        return
    if tls_enabled and str(cert_file or "").strip() and str(key_file or "").strip():
        return
    raise RuntimeError(
        "Refusing to bind Lengrvis to a non-loopback address without LAN TLS. "
        "Use a loopback host, or set LENGRVIS_LAN_TLS_ENABLED=true with cert/key files."
    )


def is_mobile_lan_http_path(path: str) -> bool:
    return is_public_lan_http_path(path) or is_mobile_token_http_path(path)


def is_public_lan_http_path(path: str) -> bool:
    return path in LAN_PUBLIC_HTTP_PATHS


def is_mobile_token_http_path(path: str) -> bool:
    return path in MOBILE_TOKEN_HTTP_PATHS or path.startswith("/api/mobile/")


def is_mobile_token_websocket_path(path: str) -> bool:
    normalized = "/" + str(path or "").lstrip("/")
    return (
        normalized == "/ws/mobile"
        or normalized.startswith("/ws/mobile/")
        or normalized == "/api/ws/mobile"
        or normalized.startswith("/api/ws/mobile/")
        or normalized == "/ws/remote"
        or normalized.startswith("/ws/remote/")
        or normalized == "/api/ws/remote"
        or normalized.startswith("/api/ws/remote/")
    )


def is_secure_transport_scheme(scheme: str | None) -> bool:
    return _normalize_scheme(scheme) in {"https", "wss"}


def is_secure_mobile_transport(client_host: str | None, scheme: str | None) -> bool:
    return is_loopback_host(client_host) or is_secure_transport_scheme(scheme)


def _normalize_scheme(scheme: str | None) -> str:
    return str(scheme or "").strip().lower().rstrip(":")


def _has_forwarded_headers(headers: Mapping[str, str] | None) -> bool:
    if not headers:
        return False
    return any(_header_value(headers, name) for name in FORWARDED_HEADER_NAMES)


def _forwarded_client_host(headers: Mapping[str, str] | None) -> str:
    forwarded_chain = _forwarded_for_chain(headers)
    if forwarded_chain:
        return _client_ip_from_forwarded_chain(forwarded_chain)
    xff = _header_value(headers, "x-forwarded-for")
    if xff:
        xff_chain = [_valid_forwarded_ip(part) for part in xff.split(",")]
        xff_chain = [item for item in xff_chain if item]
        if xff_chain:
            return _client_ip_from_forwarded_chain(xff_chain)
    real_ip = _header_value(headers, "x-real-ip")
    if real_ip:
        return _valid_forwarded_ip(real_ip)
    return ""


def _forwarded_scheme(headers: Mapping[str, str] | None) -> str:
    forwarded = _first_forwarded_value(headers, "proto")
    if forwarded:
        return forwarded if forwarded in {"http", "https", "ws", "wss"} else ""
    proto = _header_value(headers, "x-forwarded-proto").lower()
    proto = proto.split(",", 1)[0].strip().rstrip(":")
    return proto if proto in {"http", "https", "ws", "wss"} else ""


def _first_forwarded_value(headers: Mapping[str, str] | None, key: str) -> str:
    raw = _header_value(headers, "forwarded")
    if not raw:
        return ""
    first = raw.split(",", 1)[0]
    for part in first.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip().lower() == key:
            value = value.strip().strip('"')
            return _valid_forwarded_ip(value) if key == "for" else value.strip().lower().rstrip(":")
    return ""


def _forwarded_for_chain(headers: Mapping[str, str] | None) -> list[str]:
    raw = _header_value(headers, "forwarded")
    if not raw:
        return []
    result: list[str] = []
    for forwarded_item in raw.split(","):
        for part in forwarded_item.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name.strip().lower() == "for":
                parsed = _valid_forwarded_ip(value.strip().strip('"'))
                if parsed:
                    result.append(parsed)
                break
    return result


def _client_ip_from_forwarded_chain(chain: list[str]) -> str:
    for item in reversed(chain):
        if not is_trusted_proxy_host(item):
            return item
    return chain[0] if chain else ""


def _valid_forwarded_ip(value: str) -> str:
    candidate = normalize_host_for_security(value.strip().strip('"'))
    if not candidate or candidate.lower() in {"unknown", "none"}:
        return ""
    if candidate.startswith("_"):
        return ""
    if not _HOST_TOKEN_RE.match(candidate):
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""


def _trusted_proxy_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in _trusted_proxy_items():
        if item in {"localhost", "testclient"}:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _trusted_proxy_hostnames() -> set[str]:
    return {item for item in _trusted_proxy_items() if item in {"localhost", "testclient"}}


def _trusted_proxy_items() -> list[str]:
    raw = str(get_env(TRUSTED_PROXY_IPS_ENV) or get_env(TRUSTED_PROXY_ALIASES_ENV) or "")
    items: list[str] = []
    for item in re.split(r"[;,]\s*|\s+", raw):
        normalized = normalize_host_for_security(item)
        if normalized and normalized not in {"*", "0.0.0.0", "::"}:  # noqa: S104 - compared, not bound.
            items.append(normalized)
    return items


def _header_value(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    direct = headers.get(name)
    if direct is not None:
        return str(direct or "").strip()
    lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lower:
            return str(value or "").strip()
    return ""


def _is_loopback_bind_host(host: str | None) -> bool:
    normalized = normalize_host_for_security(host)
    if not normalized:
        return True
    if normalized in {"localhost", "testclient"}:
        return True
    if normalized in {"0.0.0.0", "::", "*"}:  # noqa: S104 - compared, not bound.
        return False
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
