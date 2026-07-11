#!/usr/bin/env python3
"""Validate reviewed paid-launch claims and asset evidence."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    CandidateBinding,
    candidate_binding_from_environment,
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

ARTIFACT_TYPE = "claims-launch-evidence-reviewed"
DEFAULT_EVIDENCE = "build/claims-launch-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_CLAIMS_LAUNCH_EVIDENCE_PATH"


def validate_payload(
    payload: dict[str, Any], *, repo_root: Path | None = None, expected_candidate_binding: CandidateBinding | None = None
) -> list[str]:
    return validate_payload_with_contract(
        payload,
        repo_root=repo_root,
        expected_candidate_binding=expected_candidate_binding,
    )[0]


def validate_payload_with_contract(
    payload: dict[str, Any], *, repo_root: Path | None = None, expected_candidate_binding: CandidateBinding | None = None
) -> tuple[list[str], dict[str, bool]]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)

    _require_claim_area(payload, errors)
    _require_repository_contracts(repo_root or Path.cwd(), errors)

    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.claims_ready", errors)
    require_false(payload, "summary.public_launch_signoff", errors)
    require_false(payload, "summary.release_signoff", errors)
    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.release_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _require_claim_area(payload: dict[str, Any], errors: list[str]) -> None:
    for path in (
        "pricing.status",
        "feature_matrix.status",
        "entitlement_tests.status",
        "platform_preview_labels.status",
        "security_privacy_claims.status",
        "release_notes.status",
        "onboarding.status",
        "rollback_communication.status",
    ):
        require_passed(payload, path, errors)
    for path in (
        "pricing.approved_pricing_page_label",
        "pricing.tax_and_payment_terms_label",
        "feature_matrix.docs_pricing_review_label",
        "feature_matrix.entitlement_code_review_label",
        "entitlement_tests.test_run_label",
        "platform_preview_labels.review_label",
        "security_privacy_claims.review_label",
        "release_notes.review_label",
        "onboarding.review_label",
        "rollback_communication.review_label",
    ):
        require_nonempty(payload, path, errors)


def _require_repository_contracts(repo_root: Path, errors: list[str]) -> None:
    pricing_path = repo_root / "docs" / "pricing.md"
    business_pricing_path = repo_root / "docs" / "business" / "pricing.md"
    entitlements_path = repo_root / "backend" / "app" / "commerce" / "entitlements.py"
    usage_path = repo_root / "backend" / "app" / "commerce" / "usage.py"
    required_paths = (pricing_path, business_pricing_path, entitlements_path, usage_path)
    for path in required_paths:
        if not path.exists():
            errors.append(f"required claims source is missing: {path.relative_to(repo_root)}")
            return

    pricing_text = pricing_path.read_text(encoding="utf-8")
    business_text = business_pricing_path.read_text(encoding="utf-8")
    entitlements_text = entitlements_path.read_text(encoding="utf-8")
    usage_text = usage_path.read_text(encoding="utf-8")

    for marker in (
        "Free",
        "Plus",
        "Pro",
        "¥49/月",
        "¥129/月",
        "remote_control",
        "audit_export",
        "private_deployment",
    ):
        if marker not in pricing_text:
            errors.append(f"docs/pricing.md is missing claims matrix marker: {marker}")
    if "../pricing.md" not in business_text:
        errors.append("docs/business/pricing.md must point to docs/pricing.md")
    for marker in ("Feature.REMOTE_CONTROL", "Feature.AUDIT_EXPORT", "Feature.PRIVATE_DEPLOYMENT"):
        if marker not in entitlements_text:
            errors.append(f"entitlement code is missing marker: {marker}")
    for marker in ("Plan.FREE", "Plan.PLUS", "Plan.PRO", "5_000_000", "10_000_000", "100_000_000"):
        if marker not in usage_text:
            errors.append(f"usage quota code is missing marker: {marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--repo-root", default=".")
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
            repo_root=Path(args.repo_root).resolve(),
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
