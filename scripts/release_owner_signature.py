#!/usr/bin/env python3
"""Create and verify cryptographic release-owner candidate sign-off.

The private key is intentionally never accepted by this script. Release owners
generate the canonical payload, sign its exact UTF-8 bytes with an offline
Ed25519 key, and provide only the public key plus detached signature to CI.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PAYLOAD_SCHEMA = "lengrvis-release-owner-signoff-v1"
EVIDENCE_SCHEMA = "lengrvis-release-owner-signature-evidence-v1"
SIGNOFF_STATEMENT = (
    "I approve promotion of this exact reviewed Lengrvis candidate and accept "
    "the recorded residual risks for the stated release tag."
)
MANUAL_SIGNOFF_STATUSES = frozenset(
    {
        "rc_signoff_recorded",
        "release_signoff_recorded",
        "paid_launch_signoff_recorded",
    }
)
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_RELEASE_TAG_RE = re.compile(
    r"^v[0-9]+(?:\.[0-9]+){2}(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?"
    r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$"
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\]]{0,99}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "statement",
        "repository",
        "release_tag",
        "candidate_commit",
        "candidate_run_id",
        "candidate_run_attempt",
        "reviewed_evidence_run_id",
        "reviewed_evidence_run_attempt",
        "build_identifier",
        "release_owner",
        "manual_signoff_status",
    }
)


def create_signoff_payload(
    *,
    repository: str,
    release_tag: str,
    candidate_commit: str,
    candidate_run_id: str,
    candidate_run_attempt: str,
    reviewed_evidence_run_id: str,
    reviewed_evidence_run_attempt: str,
    build_identifier: str,
    release_owner: str,
    manual_signoff_status: str,
) -> dict[str, str]:
    repository = str(repository or "").strip()
    release_tag = str(release_tag or "").strip()
    candidate_commit = str(candidate_commit or "").strip().lower()
    candidate_run_id = _positive_integer_text(candidate_run_id, "candidate_run_id")
    candidate_run_attempt = _positive_integer_text(
        candidate_run_attempt, "candidate_run_attempt"
    )
    reviewed_evidence_run_id = _positive_integer_text(
        reviewed_evidence_run_id, "reviewed_evidence_run_id"
    )
    reviewed_evidence_run_attempt = _positive_integer_text(
        reviewed_evidence_run_attempt, "reviewed_evidence_run_attempt"
    )
    build_identifier = str(build_identifier or "").strip()
    release_owner = str(release_owner or "").strip()
    manual_signoff_status = str(manual_signoff_status or "").strip()

    repository_parts = repository.split("/", 1)
    if (
        not _REPOSITORY_RE.fullmatch(repository)
        or len(repository_parts) != 2
        or any(part in {".", ".."} for part in repository_parts)
    ):
        raise ValueError("repository must be an exact owner/name GitHub repository identity")
    if not _RELEASE_TAG_RE.fullmatch(release_tag):
        raise ValueError("release_tag must be a v-prefixed semantic version")
    if not _COMMIT_RE.fullmatch(candidate_commit):
        raise ValueError("candidate_commit must be a full lowercase Git commit SHA")
    if not _OWNER_RE.fullmatch(release_owner):
        raise ValueError("release_owner contains unsupported characters")
    if manual_signoff_status not in MANUAL_SIGNOFF_STATUSES:
        raise ValueError("manual_signoff_status is not an accepted release approval state")
    expected_build_identifier = (
        f"rc-{candidate_run_id}-{candidate_run_attempt}-{candidate_commit}"
    )
    if build_identifier != expected_build_identifier:
        raise ValueError("build_identifier does not bind the exact candidate run and commit")

    return {
        "schema_version": PAYLOAD_SCHEMA,
        "statement": SIGNOFF_STATEMENT,
        "repository": repository,
        "release_tag": release_tag,
        "candidate_commit": candidate_commit,
        "candidate_run_id": candidate_run_id,
        "candidate_run_attempt": candidate_run_attempt,
        "reviewed_evidence_run_id": reviewed_evidence_run_id,
        "reviewed_evidence_run_attempt": reviewed_evidence_run_attempt,
        "build_identifier": build_identifier,
        "release_owner": release_owner,
        "manual_signoff_status": manual_signoff_status,
    }


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    validated = _validate_payload(payload)
    return json.dumps(
        validated,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_release_owner_signature(
    *,
    public_key_text: str,
    signature_text: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    canonical = canonical_payload_bytes(payload)
    public_key_bytes = _decode_ed25519_value(
        public_key_text,
        expected_length=32,
        label="public key",
    )
    signature_bytes = _decode_ed25519_value(
        signature_text,
        expected_length=64,
        label="signature",
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes,
            canonical,
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("release-owner Ed25519 signature verification failed") from exc

    normalized_public_key = f"ed25519:{_b64url(public_key_bytes)}"
    normalized_signature = f"ed25519:{_b64url(signature_bytes)}"
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "verified": True,
        "algorithm": "ed25519",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "payload": _validate_payload(payload),
        "payload_sha256": _sha256_label(canonical),
        "public_key": normalized_public_key,
        "public_key_fingerprint": _sha256_label(public_key_bytes),
        "signature": normalized_signature,
        "signature_sha256": _sha256_label(signature_bytes),
    }


def _validate_payload(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict) or frozenset(payload) != _PAYLOAD_KEYS:
        raise ValueError("release-owner payload has an invalid field set")
    if payload.get("schema_version") != PAYLOAD_SCHEMA:
        raise ValueError("release-owner payload schema_version is invalid")
    if payload.get("statement") != SIGNOFF_STATEMENT:
        raise ValueError("release-owner payload statement is invalid")
    return create_signoff_payload(
        repository=str(payload.get("repository") or ""),
        release_tag=str(payload.get("release_tag") or ""),
        candidate_commit=str(payload.get("candidate_commit") or ""),
        candidate_run_id=str(payload.get("candidate_run_id") or ""),
        candidate_run_attempt=str(payload.get("candidate_run_attempt") or ""),
        reviewed_evidence_run_id=str(payload.get("reviewed_evidence_run_id") or ""),
        reviewed_evidence_run_attempt=str(
            payload.get("reviewed_evidence_run_attempt") or ""
        ),
        build_identifier=str(payload.get("build_identifier") or ""),
        release_owner=str(payload.get("release_owner") or ""),
        manual_signoff_status=str(payload.get("manual_signoff_status") or ""),
    )


def _positive_integer_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", normalized):
        raise ValueError(f"{label} must be a positive integer")
    return normalized


def _decode_ed25519_value(value: str, *, expected_length: int, label: str) -> bytes:
    normalized = str(value or "").strip()
    if not normalized.startswith("ed25519:"):
        raise ValueError(f"release-owner {label} must use the ed25519: prefix")
    encoded = normalized.removeprefix("ed25519:")
    if not _B64URL_RE.fullmatch(encoded):
        raise ValueError(f"release-owner {label} is not valid base64url")
    padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"release-owner {label} is not valid base64url") from exc
    if len(decoded) != expected_length:
        raise ValueError(f"release-owner {label} has an invalid Ed25519 length")
    return decoded


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _sha256_label(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or verify the exact Ed25519 release-owner candidate sign-off."
    )
    parser.add_argument(
        "--repository",
        default=_first_env(
            "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY",
            "GITHUB_REPOSITORY",
        ),
    )
    parser.add_argument("--release-tag", default=_first_env("RELEASE_TAG"))
    parser.add_argument(
        "--candidate-commit",
        default=_first_env("LENGRVIS_RELEASE_CANDIDATE_COMMIT"),
    )
    parser.add_argument(
        "--candidate-run-id",
        default=_first_env("LENGRVIS_RELEASE_CANDIDATE_RUN_ID"),
    )
    parser.add_argument(
        "--candidate-run-attempt",
        default=_first_env("LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT"),
    )
    parser.add_argument(
        "--reviewed-evidence-run-id",
        default=_first_env("LENGRVIS_REVIEWED_EVIDENCE_RUN_ID"),
    )
    parser.add_argument(
        "--reviewed-evidence-run-attempt",
        default=_first_env("LENGRVIS_REVIEWED_EVIDENCE_RUN_ATTEMPT"),
    )
    parser.add_argument(
        "--build-identifier",
        default=_first_env("LENGRVIS_RELEASE_BUILD_IDENTIFIER"),
    )
    parser.add_argument(
        "--release-owner",
        default=_first_env("RELEASE_OWNER", "GITHUB_ACTOR"),
    )
    parser.add_argument(
        "--manual-signoff-status",
        default=_first_env("RELEASE_EVIDENCE_MANUAL_SIGNOFF_STATUS"),
    )
    parser.add_argument(
        "--public-key",
        default=_first_env("LENGRVIS_RELEASE_OWNER_PUBLIC_KEY"),
    )
    parser.add_argument(
        "--signature",
        default=_first_env("RELEASE_OWNER_SIGNATURE"),
    )
    parser.add_argument(
        "--output",
        default="build/release-owner-signature-verification.json",
    )
    parser.add_argument(
        "--emit-payload-only",
        action="store_true",
        help="Write the canonical UTF-8 payload bytes for offline signing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_path = Path(args.output)
    try:
        payload = create_signoff_payload(
            repository=args.repository,
            release_tag=args.release_tag,
            candidate_commit=args.candidate_commit,
            candidate_run_id=args.candidate_run_id,
            candidate_run_attempt=args.candidate_run_attempt,
            reviewed_evidence_run_id=args.reviewed_evidence_run_id,
            reviewed_evidence_run_attempt=args.reviewed_evidence_run_attempt,
            build_identifier=args.build_identifier,
            release_owner=args.release_owner,
            manual_signoff_status=args.manual_signoff_status,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        if args.emit_payload_only:
            canonical = canonical_payload_bytes(payload)
            output_path.write_bytes(canonical + b"\n")
            print(
                "release-owner-signature: canonical payload written "
                f"({ _sha256_label(canonical) })"
            )
            return 0

        evidence = verify_release_owner_signature(
            public_key_text=args.public_key,
            signature_text=args.signature,
            payload=payload,
        )
        output_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "release-owner-signature: verified "
            f"{evidence['payload_sha256']} with {evidence['public_key_fingerprint']}"
        )
        return 0
    except (OSError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        print(f"release-owner-signature: verification blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
