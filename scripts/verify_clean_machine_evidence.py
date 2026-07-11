#!/usr/bin/env python3
"""Validate reviewed clean-machine evidence for install/runtime readiness."""

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
    require_sha256_hex,
    require_true,
    result_payload,
    reviewed_evidence_contract_status,
    validate_candidate_binding,
    validate_dist_artifact_sha256_cross_check,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "clean-machine-release-evidence-reviewed"
DEFAULT_EVIDENCE = "build/clean-machine-release-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_CLEAN_MACHINE_EVIDENCE_PATH"


def validate_payload(
    payload: dict[str, Any],
    *,
    require_local_model: bool = False,
    repo_root: Path | None = None,
    expected_candidate_binding: CandidateBinding | None = None,
) -> list[str]:
    return validate_payload_with_contract(
        payload,
        require_local_model=require_local_model,
        repo_root=repo_root,
        expected_candidate_binding=expected_candidate_binding,
    )[0]


def validate_payload_with_contract(
    payload: dict[str, Any],
    *,
    require_local_model: bool = False,
    repo_root: Path | None = None,
    expected_candidate_binding: CandidateBinding | None = None,
) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)
    require_nonempty(payload, "candidate.artifact_label", errors)
    require_nonempty(payload, "candidate.artifact_sha256", errors)
    require_nonempty(payload, "machine.profile_label_redacted", errors)
    require_nonempty(payload, "machine.os_label_redacted", errors)
    require_sha256_hex(payload, "candidate.artifact_sha256", errors)
    validate_dist_artifact_sha256_cross_check(payload, errors, repo_root=repo_root)
    for check in (
        "checks.install",
        "checks.launch",
        "checks.backend_health",
        "checks.first_read_only_task",
        "checks.diagnostics_export",
        "checks.uninstall_or_rollback",
        "checks.screenshot_log_redaction_review",
    ):
        require_passed(payload, check, errors)
    _validate_audit_anchor(payload, errors)
    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.clean_machine_pass", errors)
    require_false(payload, "summary.release_signoff", errors)

    local_model_claimed = (
        get_path(payload, "claims.privacy_mode_or_local_model") is True
    )
    if require_local_model or local_model_claimed:
        _validate_local_model(payload, errors)

    contract = reviewed_evidence_contract_status(
        payload, release_signoff_path="summary.release_signoff", errors=errors
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _validate_audit_anchor(payload: dict[str, Any], errors: list[str]) -> None:
    for path in (
        "audit_anchor.anchor_label",
        "audit_anchor.anchor_sha256",
    ):
        require_nonempty(payload, path, errors)
    require_passed(payload, "audit_anchor.verify_audit_log", errors)
    sha = get_path(payload, "audit_anchor.anchor_sha256")
    if isinstance(sha, str) and (
        len(sha.strip()) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in sha.strip())
    ):
        errors.append(
            "audit_anchor.anchor_sha256 must be a 64-character SHA256 hex digest"
        )


def _validate_local_model(payload: dict[str, Any], errors: list[str]) -> None:
    for path in (
        "local_model.runtime",
        "local_model.runtime_version",
        "local_model.model",
        "local_model.model_version",
    ):
        require_nonempty(payload, path, errors)
    for check in (
        "local_model.install",
        "local_model.start",
        "local_model.pull",
        "local_model.privacy_task_smoke",
    ):
        require_passed(payload, check, errors)
    require_true(payload, "summary.local_model_pass", errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--require-local-model", action="store_true")
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
            require_local_model=args.require_local_model,
            repo_root=Path(__file__).resolve().parent.parent,
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result(
        result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract)
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
