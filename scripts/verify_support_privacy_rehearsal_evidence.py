#!/usr/bin/env python3
"""Validate reviewed support and privacy operations rehearsal evidence."""

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
    require_artifact_type,
    require_false,
    require_iso_datetime,
    require_nonempty,
    require_passed,
    require_true,
    result_payload,
    reviewed_evidence_contract_status,
    validate_candidate_binding,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "support-privacy-operations-evidence-reviewed"
DEFAULT_EVIDENCE = "build/support-privacy-operations-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_SUPPORT_PRIVACY_EVIDENCE_PATH"


def validate_payload(
    payload: dict[str, Any], *, expected_candidate_binding: CandidateBinding | None = None
) -> list[str]:
    return validate_payload_with_contract(payload, expected_candidate_binding=expected_candidate_binding)[0]


def validate_payload_with_contract(
    payload: dict[str, Any], *, expected_candidate_binding: CandidateBinding | None = None
) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)

    _require_ownership(payload, errors)
    _require_operating_model(payload, errors)
    _require_rehearsals(payload, errors)

    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.support_privacy_ready", errors)
    require_false(payload, "summary.public_support_launch_signoff", errors)
    require_false(payload, "summary.release_signoff", errors)
    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.release_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _require_ownership(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "ownership.status", errors)
    for path in (
        "ownership.primary_support_owner_label",
        "ownership.backup_support_owner_label",
        "ownership.privacy_owner_label",
        "ownership.security_escalation_label",
        "ownership.public_support_channel_label",
        "ownership.public_privacy_channel_label",
    ):
        require_nonempty(payload, path, errors)


def _require_operating_model(payload: dict[str, Any], errors: list[str]) -> None:
    for path in (
        "operating_model.support_scope.status",
        "operating_model.intake.status",
        "operating_model.severity_routing.status",
        "operating_model.diagnostic_package_handling.status",
        "operating_model.data_subject_requests.status",
        "operating_model.retention.status",
        "operating_model.response_ownership.status",
    ):
        require_passed(payload, path, errors)
    for path in (
        "operating_model.support_scope.label",
        "operating_model.intake.label",
        "operating_model.severity_routing.label",
        "operating_model.diagnostic_package_handling.label",
        "operating_model.data_subject_requests.label",
        "operating_model.retention.label",
        "operating_model.response_ownership.label",
        "operating_model.jurisdiction_guidance_label",
    ):
        require_nonempty(payload, path, errors)


def _require_rehearsals(payload: dict[str, Any], errors: list[str]) -> None:
    required = (
        "desktop_deletion_with_settings",
        "desktop_deletion_without_settings",
        "wrong_phrase_denied",
        "native_confirmation_cancelled",
        "diagnostic_export_content_review",
        "controlled_diagnostic_receipt",
        "diagnostic_package_deletion",
        "mock_p1_privacy_escalation",
        "ordinary_support_case",
    )
    rehearsals = get_path(payload, "release_rehearsal.checks")
    if not isinstance(rehearsals, dict):
        errors.append("release_rehearsal.checks must be an object")
        rehearsals = {}
    for key in required:
        require_passed(payload, f"release_rehearsal.checks.{key}.status", errors)
        require_nonempty(payload, f"release_rehearsal.checks.{key}.evidence_label", errors)
    require_passed(payload, "release_rehearsal.status", errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--require-candidate-binding", action="store_true")
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    payload, errors = load_json(evidence_path)
    expected_candidate_binding: CandidateBinding | None = None
    if args.require_candidate_binding:
        expected_candidate_binding, binding_errors = candidate_binding_from_environment()
        errors.extend(binding_errors)
    contract: dict[str, bool] | None = None
    if payload is not None:
        payload_errors, contract = validate_payload_with_contract(
            payload,
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
