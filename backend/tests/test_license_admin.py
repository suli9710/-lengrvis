from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "license_admin.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("license_admin", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _future_expiry() -> str:
    return (datetime.now(UTC) + timedelta(days=365)).isoformat()


def test_keygen_requires_encryption_or_explicit_override(tmp_path: Path) -> None:
    assert (
        mod.main(
            [
                "keygen",
                "--private-key-out",
                str(tmp_path / "private.pem"),
                "--public-key-out",
                str(tmp_path / "public.key"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "private.pem").exists()


def test_issue_revoke_and_inspect_offline_lifecycle(tmp_path: Path) -> None:
    private_key = tmp_path / "issuer-private.pem"
    public_key = tmp_path / "issuer-public.key"
    passphrase = tmp_path / "passphrase.txt"
    ledger = tmp_path / "issuer-ledger.jsonl"
    license_file = tmp_path / "customer.lic"
    manifest = tmp_path / "license-revocations.key"
    passphrase.write_text("correct horse battery staple", encoding="utf-8")

    assert (
        mod.main(
            [
                "keygen",
                "--private-key-out",
                str(private_key),
                "--public-key-out",
                str(public_key),
                "--private-key-passphrase-file",
                str(passphrase),
            ]
        )
        == 0
    )
    assert b"ENCRYPTED PRIVATE KEY" in private_key.read_bytes()

    assert (
        mod.main(
            [
                "issue",
                "--private-key",
                str(private_key),
                "--private-key-passphrase-file",
                str(passphrase),
                "--issuer",
                "Lengrvis Sales",
                "--subject",
                "ACME",
                "--plan",
                "pro",
                "--seats",
                "10",
                "--expires-at",
                _future_expiry(),
                "--license-id",
                "lic_acme_001",
                "--order-ref",
                "order-redacted-001",
                "--subscription-id",
                "sub-redacted-001",
                "--subscription-status",
                "active",
                "--output",
                str(license_file),
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    token = license_file.read_text(encoding="utf-8").strip()
    assert token and "ACME" not in token
    events = mod.load_ledger(ledger)
    assert len(events) == 1
    assert events[0]["event"] == "issued"
    assert events[0]["license_id"] == "lic_acme_001"
    assert events[0]["plan"] == "pro"
    assert events[0]["subscription_id"] == "sub-redacted-001"
    assert "token" not in events[0]

    assert (
        mod.main(
            [
                "inspect",
                "--public-key",
                str(public_key),
                "--license",
                str(license_file),
            ]
        )
        == 0
    )
    assert (
        mod.main(
            [
                "revoke",
                "--private-key",
                str(private_key),
                "--private-key-passphrase-file",
                str(passphrase),
                "--issuer",
                "Lengrvis Sales",
                "--license-id",
                "lic_acme_001",
                "--reason",
                "refund",
                "--ledger",
                str(ledger),
                "--manifest-out",
                str(manifest),
            ]
        )
        == 0
    )
    events = mod.load_ledger(ledger)
    assert [event["event"] for event in events] == ["issued", "revoked"]
    parsed_manifest = mod.parse_revocation_manifest(
        manifest.read_text(encoding="utf-8").strip(),
        public_key.read_text(encoding="utf-8").strip(),
    )
    assert parsed_manifest.is_revoked("lic_acme_001")
    assert (
        mod.main(
            [
                "inspect",
                "--public-key",
                str(public_key),
                "--license",
                str(license_file),
                "--revocations",
                str(manifest),
            ]
        )
        == 2
    )


def test_ledger_tampering_is_detected(tmp_path: Path) -> None:
    ledger = tmp_path / "issuer-ledger.jsonl"
    mod.append_ledger_event(
        ledger,
        {
            "event": "issued",
            "timestamp": datetime.now(UTC).isoformat(),
            "license_id": "lic_1",
        },
    )
    event = json.loads(ledger.read_text(encoding="utf-8"))
    event["license_id"] = "lic_changed"
    ledger.write_text(json.dumps(event), encoding="utf-8")

    try:
        mod.load_ledger(ledger)
    except ValueError as exc:
        assert "event hash" in str(exc)
    else:
        raise AssertionError("Tampered ledger must fail validation")
