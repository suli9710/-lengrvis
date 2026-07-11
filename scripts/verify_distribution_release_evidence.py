#!/usr/bin/env python3
"""Validate reviewed distribution/signing evidence for an RC candidate."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    CandidateBinding,
    candidate_binding_from_environment,
    get_path,
    load_json,
    print_result,
    require_any_nonempty,
    require_artifact_type,
    require_false,
    require_iso_datetime,
    require_nonempty,
    require_passed,
    require_sha256_hex,
    require_true,
    result_payload,
    reviewed_evidence_contract_status,
    validate_candidate_binding,
    validate_dist_artifact_sha256_cross_check,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "distribution-release-evidence-reviewed"
DEFAULT_EVIDENCE = "build/distribution-release-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_DISTRIBUTION_EVIDENCE_PATH"


def validate_payload(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    expected_candidate_binding: CandidateBinding | None = None,
) -> list[str]:
    return validate_payload_with_contract(
        payload,
        repo_root=repo_root,
        expected_candidate_binding=expected_candidate_binding,
    )[0]


def validate_payload_with_contract(
    payload: dict[str, Any],
    *,
    repo_root: Path | None = None,
    expected_candidate_binding: CandidateBinding | None = None,
) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)
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
    require_sha256_hex(payload, "candidate.artifact_sha256", errors)
    validate_dist_artifact_sha256_cross_check(payload, errors, repo_root=repo_root)
    contract = reviewed_evidence_contract_status(payload, release_signoff_path="summary.release_signoff", errors=errors)
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--require-candidate-binding", action="store_true")
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    payload, errors = load_json(evidence_path)
    contract: dict[str, bool] | None = None
    expected_candidate_binding: CandidateBinding | None = None
    if args.require_candidate_binding:
        expected_candidate_binding, binding_errors = candidate_binding_from_environment()
        errors.extend(binding_errors)
    if payload is not None:
        payload_errors, contract = validate_payload_with_contract(
            payload,
            repo_root=Path(__file__).resolve().parent.parent,
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
