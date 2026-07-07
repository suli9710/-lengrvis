#!/usr/bin/env python3
"""Validate reviewed diagnostics external-sharing evidence."""

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

ARTIFACT_TYPE = "diagnostics-external-review-evidence-reviewed"
DEFAULT_EVIDENCE = "build/diagnostics-external-review-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_DIAGNOSTICS_REVIEW_EVIDENCE_PATH"
ALLOWED_DECISIONS = {"support_only", "do_not_share"}
REQUIRED_CHECKS = (
    "checks.actual_exported_package_opened",
    "checks.logs_reviewed",
    "checks.path_labels_reviewed",
    "checks.task_traces_reviewed",
    "checks.model_traces_reviewed",
    "checks.device_identifiers_reviewed",
    "checks.credentials_and_secrets_reviewed",
    "checks.redaction_reviewed",
    "checks.external_sharing_decision_recorded",
)


def validate_payload(payload: dict[str, Any]) -> list[str]:
    return validate_payload_with_contract(payload)[0]


def validate_payload_with_contract(payload: dict[str, Any]) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    _require_nonempty_string(payload, "candidate.commit", errors)
    _require_nonempty_string(payload, "candidate.build_identifier", errors)
    _require_nonempty_string(payload, "candidate.diagnostics_package_label", errors)
    _validate_package_label(payload, errors)
    require_passed(payload, "review.status", errors)
    _require_nonempty_string(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    _require_utc_datetime_text(payload, "review.reviewed_at_utc", errors)
    _validate_decision(payload, errors)
    for check in REQUIRED_CHECKS:
        require_passed(payload, check, errors)
    require_true(payload, "summary.diagnostics_review_pass", errors)
    require_false(payload, "summary.rc_signoff", errors)
    require_false(payload, "summary.release_signoff", errors)
    _validate_sharing_flags(payload, errors)

    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.release_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _validate_decision(payload: dict[str, Any], errors: list[str]) -> None:
    require_nonempty(payload, "review.decision", errors)
    decision = str(get_path(payload, "review.decision") or "").strip().lower()
    if decision and decision not in ALLOWED_DECISIONS:
        errors.append(
            "review.decision must be one of "
            f"{', '.join(sorted(ALLOWED_DECISIONS))}"
        )


def _validate_package_label(payload: dict[str, Any], errors: list[str]) -> None:
    label = get_path(payload, "candidate.diagnostics_package_label")
    if not isinstance(label, str):
        return
    text = label.strip()
    if "\\" in text or "/" in text or ":" in text or text.startswith("~"):
        errors.append("candidate.diagnostics_package_label must be a redacted label, not a raw path")


def _require_nonempty_string(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} must be a non-empty string")


def _require_utc_datetime_text(payload: dict[str, Any], path: str, errors: list[str]) -> None:
    value = get_path(payload, path)
    if not isinstance(value, str) or not value.strip():
        return
    text = value.strip()
    if not (text.endswith("Z") or text.endswith("+00:00")):
        errors.append(f"{path} must include an explicit UTC timezone (Z or +00:00)")


def _validate_sharing_flags(payload: dict[str, Any], errors: list[str]) -> None:
    public_safe = get_path(payload, "summary.public_safe")
    external_allowed = get_path(payload, "summary.external_sharing_allowed")
    if not isinstance(public_safe, bool):
        errors.append("summary.public_safe must be a boolean")
    if not isinstance(external_allowed, bool):
        errors.append("summary.external_sharing_allowed must be a boolean")
    if public_safe is not False:
        errors.append("summary.public_safe must be false; public-safe approval requires a separate artifact")
    if external_allowed is not False:
        errors.append(
            "summary.external_sharing_allowed must be false; external sharing approval requires a separate artifact"
        )


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
