from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import DEFAULT_DATA_DIR
from app.security.lan import normalize_host_for_security

DEFAULT_CERT_DIR = "lan-tls"
DEFAULT_CERT_NAME = "lengrvis-lan.crt"
DEFAULT_KEY_NAME = "lengrvis-lan.key"
DEFAULT_VALID_DAYS = 825


@dataclass(frozen=True)
class LanTlsMaterial:
    cert_file: Path
    key_file: Path
    origin: str
    host: str
    port: int
    fingerprint_sha256: str
    dns_names: tuple[str, ...]
    ip_addresses: tuple[str, ...]
    created: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "cert_file": str(self.cert_file),
            "key_file": str(self.key_file),
            "origin": self.origin,
            "host": self.host,
            "port": self.port,
            "fingerprint_sha256": self.fingerprint_sha256,
            "dns_names": list(self.dns_names),
            "ip_addresses": list(self.ip_addresses),
            "created": self.created,
        }


def ensure_lan_tls_material(
    *,
    data_dir: str | Path | None = None,
    host: str = "",
    port: int | str = 8000,
    public_base_url: str = "",
    cert_file: str | Path = "",
    key_file: str | Path = "",
    valid_days: int = DEFAULT_VALID_DAYS,
) -> LanTlsMaterial:
    """Create or reuse local LAN TLS material for mobile HTTPS/WSS pairing.

    The certificate is self-signed and intended for local Android trust/pinning
    flows. It never relaxes LAN transport policy; callers still need to enable
    HTTPS explicitly and pass these files to uvicorn.
    """

    resolved_host = advertised_host(public_base_url=public_base_url, host=host)
    resolved_port = _normalize_port(port)
    origin = _origin(public_base_url=public_base_url, host=resolved_host, port=resolved_port)
    cert_path, key_path = _material_paths(data_dir=data_dir, cert_file=cert_file, key_file=key_file)
    names = _subject_names(resolved_host)
    if _material_reusable(cert_path, key_path, names):
        created = False
    else:
        _write_material(cert_path, key_path, names, valid_days=valid_days)
        created = True

    return LanTlsMaterial(
        cert_file=cert_path,
        key_file=key_path,
        origin=origin,
        host=resolved_host,
        port=resolved_port,
        fingerprint_sha256=certificate_fingerprint_sha256(cert_path),
        dns_names=tuple(names["dns"]),
        ip_addresses=tuple(str(item) for item in names["ip"]),
        created=created,
    )


def advertised_host(*, public_base_url: str = "", host: str = "") -> str:
    configured = str(public_base_url or "").strip()
    if configured:
        parsed = urlparse(configured)
        if parsed.scheme and parsed.scheme != "https":
            raise ValueError("LAN public base URL must use https:// when LAN TLS is enabled.")
        if parsed.hostname:
            return parsed.hostname

    normalized = normalize_host_for_security(host)
    if normalized and normalized not in {"0.0.0.0", "::", "*"}:  # noqa: S104 - sentinel values are rejected, not bound.
        return normalized
    return best_lan_host()


def best_lan_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]
            if candidate:
                return candidate
    except OSError:
        pass

    try:
        candidate = socket.gethostbyname(socket.gethostname())
        if candidate:
            return candidate
    except OSError:
        pass
    return "127.0.0.1"


def certificate_fingerprint_sha256(cert_path: str | Path) -> str:
    path = Path(cert_path).expanduser()
    data = path.read_bytes()
    text = data.decode("utf-8", errors="ignore")
    if "-----BEGIN CERTIFICATE-----" in text:
        data = ssl.PEM_cert_to_DER_cert(text)
    return sha256(data).hexdigest()


def _material_paths(
    *,
    data_dir: str | Path | None,
    cert_file: str | Path,
    key_file: str | Path,
) -> tuple[Path, Path]:
    cert_text = str(cert_file or "").strip()
    key_text = str(key_file or "").strip()
    if bool(cert_text) != bool(key_text):
        raise ValueError("LAN TLS certificate and private key must be configured together.")
    if cert_text and key_text:
        return Path(cert_text).expanduser().resolve(), Path(key_text).expanduser().resolve()

    root = Path(data_dir or os.environ.get("LENGRVIS_DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
    material_dir = root / DEFAULT_CERT_DIR
    return material_dir / DEFAULT_CERT_NAME, material_dir / DEFAULT_KEY_NAME


def _origin(*, public_base_url: str, host: str, port: int) -> str:
    configured = str(public_base_url or "").strip().rstrip("/")
    if configured:
        parsed = urlparse(configured)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("LAN public base URL must be an https:// origin.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("LAN public base URL must be an origin without path, query, fragment, or credentials.")
        return f"https://{parsed.netloc}"
    return f"https://{_format_url_host(host)}:{port}"


def _format_url_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        return f"[{address.compressed}]"
    return address.compressed


def _normalize_port(port: int | str) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("LAN TLS backend port must be a number.") from exc
    if value < 1 or value > 65535:
        raise ValueError("LAN TLS backend port must be between 1 and 65535.")
    return value


def _subject_names(primary_host: str) -> dict[str, list[object]]:
    dns_names: list[str] = []
    ip_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []

    def add_host(value: str) -> None:
        normalized = normalize_host_for_security(value)
        if not normalized:
            return
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            if normalized not in dns_names:
                dns_names.append(normalized)
            return
        if address not in ip_addresses:
            ip_addresses.append(address)

    add_host(primary_host)
    add_host("localhost")
    add_host("127.0.0.1")
    add_host("::1")
    try:
        add_host(socket.gethostname())
    except OSError:
        pass

    return {"dns": dns_names, "ip": ip_addresses}


def _material_reusable(cert_path: Path, key_path: Path, names: dict[str, list[object]]) -> bool:
    if not cert_path.exists() or not key_path.exists():
        return False
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, ssl.SSLError, x509.ExtensionNotFound):
        return False

    now = datetime.now(UTC)
    if cert.not_valid_after_utc <= now + timedelta(days=14):
        return False
    for dns_name in names["dns"]:
        if str(dns_name) not in san.get_values_for_type(x509.DNSName):
            return False
    san_ips = san.get_values_for_type(x509.IPAddress)
    return all(ip in san_ips for ip in names["ip"])


def _write_material(
    cert_path: Path,
    key_path: Path,
    names: dict[str, list[object]],
    *,
    valid_days: int,
) -> None:
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    common_name = str(names["dns"][0] if names["dns"] else names["ip"][0])
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Lengrvis Local"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    san_entries: list[x509.GeneralName] = [
        *(x509.DNSName(str(item)) for item in names["dns"]),
        *(x509.IPAddress(item) for item in names["ip"]),
    ]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(days=max(1, int(valid_days))))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or reuse local Lengrvis LAN TLS material.")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", default="8000")
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--cert-file", default="")
    parser.add_argument("--key-file", default="")
    parser.add_argument("--valid-days", type=int, default=DEFAULT_VALID_DAYS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    material = ensure_lan_tls_material(
        data_dir=args.data_dir,
        host=args.host,
        port=args.port,
        public_base_url=args.public_base_url,
        cert_file=args.cert_file,
        key_file=args.key_file,
        valid_days=args.valid_days,
    )
    print(json.dumps(material.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
