#!/usr/bin/env python3
"""Offline commercial license issuer for Lengrvis.

The private key must stay on an offline/admin machine. This tool never writes a
private key into application configuration and never prints license tokens to
stdout. Issuance and revocation events are appended to a hash-chained JSONL
ledger so replacement, refund, and migration actions remain reviewable.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.commerce.licensing import (  # noqa: E402
    LicenseError,
    parse_revocation_manifest,
    sign_license,
    sign_revocation_manifest,
    verify_license,
)
from app.commerce.entitlements import PLAN_CATALOG_CURRENT, normalize_plan  # noqa: E402

LEDGER_SCHEMA = 1
MANIFEST_SCHEMA = 1
GENESIS_HASH = "0" * 64


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str, *, field: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_passphrase(path: str | None) -> bytes | None:
    if not path:
        return None
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("Private-key passphrase file is empty")
    return value.encode("utf-8")


def _load_private_key(path: str, passphrase_file: str | None) -> tuple[Ed25519PrivateKey, bytes | None]:
    password = _read_passphrase(passphrase_file)
    raw = Path(path).read_bytes()
    key = serialization.load_pem_private_key(raw, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Private key must be Ed25519")
    return key, password


def _public_key_text(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"ed25519:{_b64url(raw)}"


def _public_key_fingerprint(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _atomic_write(path: Path, text: str, *, force: bool = False, private: bool = False) -> None:
    path = path.expanduser().resolve()
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        if private:
            os.chmod(path, 0o600)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _ledger_lock(ledger_path: Path) -> Iterator[None]:
    lock_path = ledger_path.with_suffix(f"{ledger_path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"License ledger is locked by another issuer process: {lock_path}") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ledger line {line_number} is not valid JSON") from exc
        if not isinstance(event, dict):
            raise ValueError(f"Ledger line {line_number} must be an object")
        expected_sequence = len(events) + 1
        if event.get("schema") != LEDGER_SCHEMA or event.get("sequence") != expected_sequence:
            raise ValueError(f"Ledger line {line_number} has an invalid schema or sequence")
        if event.get("previous_hash") != previous_hash:
            raise ValueError(f"Ledger line {line_number} breaks the previous-hash chain")
        claimed_hash = str(event.get("event_hash") or "")
        unsigned = {key: value for key, value in event.items() if key != "event_hash"}
        actual_hash = hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
        if claimed_hash != actual_hash:
            raise ValueError(f"Ledger line {line_number} has an invalid event hash")
        events.append(event)
        previous_hash = actual_hash
    return events


def append_ledger_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    path = path.expanduser().resolve()
    with _ledger_lock(path):
        events = load_ledger(path)
        previous_hash = events[-1]["event_hash"] if events else GENESIS_HASH
        record = {
            "schema": LEDGER_SCHEMA,
            "sequence": len(events) + 1,
            "previous_hash": previous_hash,
            **event,
        }
        record["event_hash"] = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record


def _issued_license_ids(events: list[dict[str, Any]]) -> set[str]:
    return {
        str(event.get("license_id") or "")
        for event in events
        if event.get("event") == "issued" and event.get("license_id")
    }


def _revocation_records(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event") != "revoked":
            continue
        license_id = str(event.get("license_id") or "").strip()
        if not license_id:
            continue
        record = {
            "license_id": license_id,
            "revoked_at": event.get("timestamp"),
            "reason": event.get("reason"),
        }
        replacement = str(event.get("replacement_license_id") or "").strip()
        if replacement:
            record["replacement_license_id"] = replacement
        by_id[license_id] = record
    return [by_id[key] for key in sorted(by_id)]


def _write_revocation_manifest(
    *,
    events: list[dict[str, Any]],
    issuer: str,
    private_key_path: str,
    passphrase_file: str | None,
    output: Path,
    force: bool,
) -> dict[str, Any]:
    key, password = _load_private_key(private_key_path, passphrase_file)
    generated_at = _iso(_utc_now())
    payload = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at,
        "issuer": issuer,
        "revoked": _revocation_records(events),
    }
    token = sign_revocation_manifest(
        payload,
        Path(private_key_path).read_text(encoding="utf-8"),
        password=password,
    )
    _atomic_write(output, token, force=force)
    parsed = parse_revocation_manifest(token, _public_key_text(key.public_key()))
    return {
        "manifest": str(output.resolve()),
        "generated_at": generated_at,
        "revoked_count": len(parsed.revoked_license_ids),
        "public_key_fingerprint": _public_key_fingerprint(key.public_key()),
    }


def command_keygen(args: argparse.Namespace) -> dict[str, Any]:
    if not args.private_key_passphrase_file and not args.allow_unencrypted_private_key:
        raise ValueError(
            "Refusing to create an unencrypted private key; provide "
            "--private-key-passphrase-file or explicitly pass --allow-unencrypted-private-key"
        )
    password = _read_passphrase(args.private_key_passphrase_file)
    key = Ed25519PrivateKey.generate()
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(password)
        if password is not None
        else serialization.NoEncryption()
    )
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    ).decode("ascii")
    public_text = _public_key_text(key.public_key())
    _atomic_write(Path(args.private_key_out), private_pem, force=args.force, private=True)
    try:
        _atomic_write(Path(args.public_key_out), public_text, force=args.force)
    except Exception:
        Path(args.private_key_out).unlink(missing_ok=True)
        raise
    return {
        "private_key": str(Path(args.private_key_out).resolve()),
        "public_key": str(Path(args.public_key_out).resolve()),
        "private_key_encrypted": password is not None,
        "public_key_fingerprint": _public_key_fingerprint(key.public_key()),
    }


def command_issue(args: argparse.Namespace) -> dict[str, Any]:
    now = _utc_now()
    expires_at = None if args.perpetual else _parse_datetime(args.expires_at, field="expires-at")
    if expires_at is not None and expires_at <= now:
        raise ValueError("expires-at must be in the future")
    plan = normalize_plan(args.plan)
    if plan.value == "free":
        raise ValueError("Only Plus or Pro licenses can be issued")
    key, password = _load_private_key(args.private_key, args.private_key_passphrase_file)
    license_id = args.license_id or f"lic_{uuid.uuid4().hex}"
    payload: dict[str, Any] = {
        "schema": 2,
        "plan_catalog": PLAN_CATALOG_CURRENT,
        "license_id": license_id,
        "issuer": args.issuer,
        "subject": args.subject,
        "plan": plan.value,
        "seats": args.seats,
        "issued_at": _iso(now),
        "expires_at": _iso(expires_at) if expires_at else None,
    }
    if args.subscription_id:
        payload["subscription_id"] = args.subscription_id
        payload["subscription_status"] = args.subscription_status
    if args.renews_at:
        payload["renews_at"] = _iso(_parse_datetime(args.renews_at, field="renews-at"))
    if args.cancel_at_period_end:
        payload["cancel_at_period_end"] = True
    if args.replaces:
        payload["replaces"] = args.replaces
    if args.order_ref:
        payload["order_ref"] = args.order_ref
    token = sign_license(
        payload,
        Path(args.private_key).read_text(encoding="utf-8"),
        password=password,
    )
    verify_license(token, _public_key_text(key.public_key()), now=now)

    output = Path(args.output)
    ledger = Path(args.ledger)
    _atomic_write(output, token, force=args.force)
    try:
        append_ledger_event(
            ledger,
            {
                "event": "issued",
                "timestamp": _iso(now),
                "license_id": license_id,
                "issuer": args.issuer,
                "subject": args.subject,
                "plan": plan.value,
                "seats": args.seats,
                "expires_at": payload["expires_at"],
                "replaces": args.replaces or None,
                "order_ref": args.order_ref or None,
                "subscription_id": args.subscription_id or None,
                "subscription_status": args.subscription_status if args.subscription_id else None,
                "public_key_fingerprint": _public_key_fingerprint(key.public_key()),
                "artifact_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            },
        )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {
        "license_id": license_id,
        "output": str(output.resolve()),
        "ledger": str(ledger.resolve()),
        "plan": plan.value,
        "subject": args.subject,
        "expires_at": payload["expires_at"],
        "subscription_id": args.subscription_id or None,
        "subscription_status": args.subscription_status if args.subscription_id else None,
        "public_key_fingerprint": _public_key_fingerprint(key.public_key()),
    }


def command_revoke(args: argparse.Namespace) -> dict[str, Any]:
    ledger = Path(args.ledger)
    events = load_ledger(ledger)
    issued_ids = _issued_license_ids(events)
    if args.license_id not in issued_ids:
        raise ValueError(f"License id was not issued by this ledger: {args.license_id}")
    if any(
        event.get("event") == "revoked" and event.get("license_id") == args.license_id
        for event in events
    ):
        raise ValueError(f"License is already revoked: {args.license_id}")
    if args.replacement_license_id and args.replacement_license_id not in issued_ids:
        raise ValueError(f"Replacement license id was not issued by this ledger: {args.replacement_license_id}")
    append_ledger_event(
        ledger,
        {
            "event": "revoked",
            "timestamp": _iso(_utc_now()),
            "license_id": args.license_id,
            "reason": args.reason,
            "replacement_license_id": args.replacement_license_id or None,
        },
    )
    events = load_ledger(ledger)
    result = _write_revocation_manifest(
        events=events,
        issuer=args.issuer,
        private_key_path=args.private_key,
        passphrase_file=args.private_key_passphrase_file,
        output=Path(args.manifest_out),
        force=True,
    )
    return {
        "license_id": args.license_id,
        "reason": args.reason,
        "replacement_license_id": args.replacement_license_id or None,
        "ledger": str(ledger.resolve()),
        **result,
    }


def command_publish_revocations(args: argparse.Namespace) -> dict[str, Any]:
    events = load_ledger(Path(args.ledger))
    return _write_revocation_manifest(
        events=events,
        issuer=args.issuer,
        private_key_path=args.private_key,
        passphrase_file=args.private_key_passphrase_file,
        output=Path(args.manifest_out),
        force=args.force,
    )


def command_inspect(args: argparse.Namespace) -> dict[str, Any]:
    public_key = Path(args.public_key).read_text(encoding="utf-8").strip()
    token = Path(args.license).read_text(encoding="utf-8").strip()
    revocations = None
    if args.revocations:
        revocations = parse_revocation_manifest(
            Path(args.revocations).read_text(encoding="utf-8").strip(),
            public_key,
        )
    license_ = verify_license(token, public_key, revocations=revocations)
    return {
        "license_id": license_.license_id,
        "issuer": license_.issuer,
        "subject": license_.subject,
        "plan": license_.plan.value,
        "seats": license_.seats,
        "subscription_id": license_.subscription_id or None,
        "subscription_status": license_.subscription_status or None,
        "renews_at": license_.renews_at.isoformat() if license_.renews_at else None,
        "cancel_at_period_end": license_.cancel_at_period_end,
        "issued_at": license_.issued_at.isoformat() if license_.issued_at else None,
        "expires_at": license_.expires_at.isoformat() if license_.expires_at else None,
        "replaces": license_.replaces or None,
        "revocation_checked": revocations is not None,
        "active": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline Lengrvis commercial license administration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    keygen = subparsers.add_parser("keygen", help="Generate an Ed25519 issuer keypair")
    keygen.add_argument("--private-key-out", required=True)
    keygen.add_argument("--public-key-out", required=True)
    keygen.add_argument("--private-key-passphrase-file")
    keygen.add_argument("--allow-unencrypted-private-key", action="store_true")
    keygen.add_argument("--force", action="store_true")
    keygen.set_defaults(handler=command_keygen)

    issue = subparsers.add_parser("issue", help="Issue a signed Plus or Pro license")
    issue.add_argument("--private-key", required=True)
    issue.add_argument("--private-key-passphrase-file")
    issue.add_argument("--issuer", required=True)
    issue.add_argument("--subject", required=True)
    issue.add_argument("--plan", choices=("plus", "pro", "max", "team"), required=True)
    issue.add_argument("--seats", type=int, default=1)
    expiry = issue.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--expires-at")
    expiry.add_argument("--perpetual", action="store_true")
    issue.add_argument("--license-id")
    issue.add_argument("--replaces")
    issue.add_argument("--order-ref")
    issue.add_argument("--subscription-id")
    issue.add_argument(
        "--subscription-status",
        choices=("active", "trialing", "past_due", "canceled", "expired", "revoked"),
        default="active",
    )
    issue.add_argument("--renews-at")
    issue.add_argument("--cancel-at-period-end", action="store_true")
    issue.add_argument("--output", required=True)
    issue.add_argument("--ledger", required=True)
    issue.add_argument("--force", action="store_true")
    issue.set_defaults(handler=command_issue)

    revoke = subparsers.add_parser("revoke", help="Revoke a license and publish a signed manifest")
    revoke.add_argument("--private-key", required=True)
    revoke.add_argument("--private-key-passphrase-file")
    revoke.add_argument("--issuer", required=True)
    revoke.add_argument("--license-id", required=True)
    revoke.add_argument("--reason", choices=("refund", "chargeback", "replacement", "breach", "admin"), required=True)
    revoke.add_argument("--replacement-license-id")
    revoke.add_argument("--ledger", required=True)
    revoke.add_argument("--manifest-out", required=True)
    revoke.set_defaults(handler=command_revoke)

    publish = subparsers.add_parser("publish-revocations", help="Rebuild the signed revocation manifest")
    publish.add_argument("--private-key", required=True)
    publish.add_argument("--private-key-passphrase-file")
    publish.add_argument("--issuer", required=True)
    publish.add_argument("--ledger", required=True)
    publish.add_argument("--manifest-out", required=True)
    publish.add_argument("--force", action="store_true")
    publish.set_defaults(handler=command_publish_revocations)

    inspect = subparsers.add_parser("inspect", help="Verify a license without printing its token")
    inspect.add_argument("--public-key", required=True)
    inspect.add_argument("--license", required=True)
    inspect.add_argument("--revocations")
    inspect.set_defaults(handler=command_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "seats", 1) < 1:
        parser.error("--seats must be at least 1")
    try:
        result = args.handler(args)
    except (LicenseError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
