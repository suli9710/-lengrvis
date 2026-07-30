#!/usr/bin/env python3
"""Generate an Ed25519 keypair for reviewed release-evidence sealing."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_keypair() -> tuple[str, str, str]:
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private_text = "ed25519:" + _b64url(private_raw)
    public_text = "ed25519:" + _b64url(public_raw)
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    return private_text, public_text, fingerprint


def write_keypair(*, private_key_path: Path, public_key_path: Path) -> str:
    private_path = private_key_path.expanduser().resolve(strict=False)
    public_path = public_key_path.expanduser().resolve(strict=False)
    if private_path == public_path:
        raise ValueError("private and public key output paths must be different")
    for path in (private_path, public_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing key file: {path}")

    private_text, public_text, fingerprint = generate_keypair()
    _exclusive_write(private_path, private_text, mode=0o600)
    try:
        _exclusive_write(public_path, public_text, mode=0o644)
    except Exception:
        private_path.unlink(missing_ok=True)
        raise
    return fingerprint


def _exclusive_write(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, mode)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key-output", required=True)
    parser.add_argument("--public-key-output", required=True)
    args = parser.parse_args(argv)
    try:
        fingerprint = write_keypair(
            private_key_path=Path(args.private_key_output),
            public_key_path=Path(args.public_key_output),
        )
    except (OSError, ValueError) as exc:
        print(f"reviewed-evidence-keypair: generation blocked: {exc}", file=sys.stderr)
        return 1
    print(f"reviewed-evidence-keypair: generated public key sha256:{fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
