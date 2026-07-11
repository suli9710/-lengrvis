#!/usr/bin/env python3
"""Validate reviewed subscription commercial loop evidence."""

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

ARTIFACT_TYPE = "commercial-loop-evidence-reviewed"
DEFAULT_EVIDENCE = "build/commercial-loop-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_COMMERCIAL_LOOP_EVIDENCE_PATH"


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
    require_nonempty(payload, "pilot.scope", errors)
    if get_path(payload, "pilot.scope") != "subscription_activation_free_plus_pro":
        errors.append("pilot.scope must be 'subscription_activation_free_plus_pro'")

    _require_contracting(payload, errors)
    _require_legal(payload, errors)
    _require_payment_pilot(payload, errors)
    _require_subscription_activation(payload, errors)
    _require_license_issuer(payload, errors)
    _require_support_privacy(payload, errors)
    _require_claims(payload, errors)

    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.subscription_activation_ready", errors)
    require_false(payload, "summary.self_serve_checkout_enabled", errors)
    require_false(payload, "summary.commercial_launch_signoff", errors)
    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.commercial_launch_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _require_contracting(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "contracting.status", errors)
    for path in (
        "contracting.entity_label",
        "contracting.tax_treatment_label",
        "contracting.billing_descriptor_label",
    ):
        require_nonempty(payload, path, errors)


def _require_legal(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "legal.status", errors)
    for path in (
        "legal.eula_approval_label",
        "legal.privacy_policy_approval_label",
        "legal.refund_policy_approval_label",
        "legal.supported_jurisdictions_label",
    ):
        require_nonempty(payload, path, errors)


def _require_payment_pilot(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "payment_pilot.status", errors)
    for path in (
        "payment_pilot.processor_or_manual_invoice_label",
        "payment_pilot.receipt_or_invoice_label",
        "payment_pilot.refund_rehearsal_label",
        "payment_pilot.chargeback_runbook_label",
    ):
        require_nonempty(payload, path, errors)


def _require_subscription_activation(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "subscription_activation.status", errors)
    for path in (
        "subscription_activation.activation_api_https_label",
        "subscription_activation.reverse_proxy_label",
        "subscription_activation.activation_key_creation_label",
        "subscription_activation.first_activation_label",
        "subscription_activation.idempotent_repeat_activation_label",
        "subscription_activation.device_limit_label",
        "subscription_activation.strong_device_binding_label",
        "subscription_activation.renewal_refresh_label",
        "subscription_activation.cancel_period_end_label",
        "subscription_activation.refund_revocation_label",
        "subscription_activation.expired_downgrade_label",
        "subscription_activation.rate_limit_label",
        "subscription_activation.activation_audit_log_label",
        "subscription_activation.operations_runbook_label",
        "subscription_activation.secret_redaction_label",
    ):
        require_nonempty(payload, path, errors)


def _require_license_issuer(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "license_issuer.status", errors)
    key_profile = str(get_path(payload, "license_issuer.key_profile") or "").strip().lower()
    if key_profile != "production":
        errors.append("license_issuer.key_profile must be production")
    for path in (
        "license_issuer.public_key_fingerprint_label",
        "license_issuer.private_key_custody_label",
        "license_issuer.issuance_log_label",
        "license_issuer.revocation_manifest_freshness_label",
    ):
        require_nonempty(payload, path, errors)
    for check in (
        "license_issuer.issuance_rehearsal",
        "license_issuer.renewal_rehearsal",
        "license_issuer.replacement_rehearsal",
        "license_issuer.revocation_rehearsal",
    ):
        require_passed(payload, check, errors)


def _require_support_privacy(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "support_privacy.status", errors)
    for path in (
        "support_privacy.support_channel_label",
        "support_privacy.privacy_request_runbook_label",
        "support_privacy.diagnostic_handling_label",
    ):
        require_nonempty(payload, path, errors)


def _require_claims(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "claims.status", errors)
    for path in (
        "claims.pricing_page_label",
        "claims.feature_matrix_label",
        "claims.preview_labels_review_label",
        "claims.security_privacy_claims_review_label",
    ):
        require_nonempty(payload, path, errors)


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
