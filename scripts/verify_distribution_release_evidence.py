#!/usr/bin/env python3
"""Validate reviewed distribution/signing evidence for an RC candidate."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    get_path,
    load_json,
    print_result,
    require_any_nonempty,
    require_artifact_type,
    require_false,
    require_iso_datetime,
    require_nonempty,
    require_passed,
    require_true,
    result_payload,
    reviewed_evidence_contract_status,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "distribution-release-evidence-reviewed"
DEFAULT_EVIDENCE = "build/distribution-release-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_DISTRIBUTION_EVIDENCE_PATH"


def validate_payload(payload: dict[str, Any]) -> list[str]:
    return validate_payload_with_contract(payload)[0]


def validate_payload_with_contract(payload: dict[str, Any]) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    require_any_nonempty(
        payload,
        (
            "candidate.artifact_label",
            "candidate.installer_artifact_label",
            "candidate.portable_artifact_label",
        ),
        errors,
    )
    require_nonempty(payload, "candidate.artifact_sha256", errors)
    require_nonempty(payload, "signing.subject", errors)
    require_nonempty(payload, "signing.thumbprint", errors)
    require_passed(payload, "signing.status", errors)
    for check in (
        "checks.artifact_hash",
        "checks.signature_verification",
        "checks.upgrade",
        "checks.rollback",
        "checks.uninstall",
    ):
        require_passed(payload, check, errors)
    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.distribution_pass", errors)
    require_false(payload, "summary.release_signoff", errors)
    sha = get_path(payload, "candidate.artifact_sha256")
    if isinstance(sha, str) and (len(sha.strip()) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in sha.strip())):
        errors.append("candidate.artifact_sha256 must be a 64-character SHA256 hex digest")
    contract = reviewed_evidence_contract_status(payload, release_signoff_path="summary.release_signoff", errors=errors)
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    payload, errors = load_json(evidence_path)
    contract: dict[str, bool] | None = None
    if payload is not None:
        payload_errors, contract = validate_payload_with_contract(payload)
        errors.extend(payload_errors)
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
