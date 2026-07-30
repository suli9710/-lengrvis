#!/usr/bin/env python3
"""Seal completed Android real-device evidence for a specific release candidate."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

from evidence_contracts import (
    EVIDENCE_SIGNATURE_ENV,
    EVIDENCE_SIGNATURE_PAYLOAD_V2,
    canonical_evidence_payload_hash,
    candidate_binding_from_environment,
    is_sha256_hex,
    load_json,
    print_result,
    validate_evidence_signature_secret,
)
from verify_android_reviewed_evidence import ARTIFACT_TYPE, validate_payload


def seal_payload(payload: dict[str, Any], *, secret: str, signing_key_fingerprint: str = "") -> dict[str, Any]:
    secret = validate_evidence_signature_secret(secret)
    if payload.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"artifact_type must be {ARTIFACT_TYPE!r}")
    if payload.get("template_status") or payload.get("template_mode"):
        raise ValueError("template evidence cannot be sealed")
    if payload.get("real_device_result") != "passed":
        raise ValueError("real_device_result must be passed before evidence can be sealed")

    sealed = deepcopy(payload)
    evidence = sealed.setdefault("evidence", {})
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object when present")
    evidence["payload_sha256"] = ""
    evidence["signature"] = ""
    evidence["signature_payload_version"] = EVIDENCE_SIGNATURE_PAYLOAD_V2
    fingerprint = signing_key_fingerprint.strip() or sha256(secret.encode("utf-8")).hexdigest()
    if not is_sha256_hex(fingerprint):
        raise ValueError("signing_key_fingerprint must be a full SHA256 hex digest")
    evidence["signing_key_fingerprint"] = fingerprint.lower()
    payload_hash = canonical_evidence_payload_hash(sealed)
    evidence["payload_sha256"] = payload_hash
    evidence["signature"] = hmac.new(secret.encode("utf-8"), payload_hash.encode("utf-8"), sha256).hexdigest()
    return sealed


def write_sealed_evidence(
    *,
    input_path: Path,
    output_path: Path,
    force: bool,
    signing_key_fingerprint: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    payload, errors = load_json(input_path)
    if payload is None:
        return None, errors
    if output_path.exists() and not force:
        return None, [f"output already exists: {output_path}; pass --force to overwrite"]
    binding, binding_errors = candidate_binding_from_environment()
    if binding is None:
        return None, binding_errors
    try:
        sealed = seal_payload(
            payload,
            secret=str(os.getenv(EVIDENCE_SIGNATURE_ENV) or ""),
            signing_key_fingerprint=signing_key_fingerprint,
        )
    except ValueError as exc:
        return None, [str(exc)]

    validation_errors = validate_payload(sealed, expected_candidate_binding=binding)
    if validation_errors:
        return None, validation_errors

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sealed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sealed, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Completed reviewed Android real-device draft JSON.")
    parser.add_argument("--output", default="build/android-real-device-evidence-reviewed.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--signing-key-fingerprint", default="")
    args = parser.parse_args()

    output_path = Path(args.output)
    sealed, errors = write_sealed_evidence(
        input_path=Path(args.input),
        output_path=output_path,
        force=bool(args.force),
        signing_key_fingerprint=args.signing_key_fingerprint,
    )
    print_result(
        {
            "ok": not errors,
            "artifact_type": ARTIFACT_TYPE,
            "sealed": sealed is not None and not errors,
            "errors": errors,
        }
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
