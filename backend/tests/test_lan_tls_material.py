from __future__ import annotations

import ssl
from pathlib import Path

from app.security.lan_tls import ensure_lan_tls_material


def test_ensure_lan_tls_material_generates_host_covered_cert(tmp_path: Path) -> None:
    material = ensure_lan_tls_material(
        data_dir=tmp_path,
        host="0.0.0.0",  # noqa: S104 - verifies wildcard input is replaced for advertised TLS material.
        port=9443,
        public_base_url="https://lengrvis.local:9443",
    )

    assert material.created is True
    assert material.origin == "https://lengrvis.local:9443"
    assert material.cert_file.exists()
    assert material.key_file.exists()
    assert material.fingerprint_sha256
    decoded = ssl._ssl._test_decode_cert(str(material.cert_file))  # type: ignore[attr-defined]
    san = set(decoded["subjectAltName"])
    assert ("DNS", "lengrvis.local") in san
    assert ("DNS", "localhost") in san
    assert ("IP Address", "127.0.0.1") in san


def test_ensure_lan_tls_material_reuses_existing_valid_material(tmp_path: Path) -> None:
    first = ensure_lan_tls_material(
        data_dir=tmp_path,
        host="192.168.56.10",
        port=9443,
    )
    second = ensure_lan_tls_material(
        data_dir=tmp_path,
        host="192.168.56.10",
        port=9443,
    )

    assert first.created is True
    assert second.created is False
    assert second.origin == "https://192.168.56.10:9443"
    assert second.fingerprint_sha256 == first.fingerprint_sha256


def test_ensure_lan_tls_material_replaces_invalid_existing_material(tmp_path: Path) -> None:
    material_dir = tmp_path / "lan-tls"
    material_dir.mkdir()
    (material_dir / "lengrvis-lan.crt").write_text("not a certificate", encoding="utf-8")
    (material_dir / "lengrvis-lan.key").write_text("not a key", encoding="utf-8")

    material = ensure_lan_tls_material(
        data_dir=tmp_path,
        host="192.168.56.10",
        port=9443,
    )

    assert material.created is True
    decoded = ssl._ssl._test_decode_cert(str(material.cert_file))  # type: ignore[attr-defined]
    san = set(decoded["subjectAltName"])
    assert ("IP Address", "192.168.56.10") in san
