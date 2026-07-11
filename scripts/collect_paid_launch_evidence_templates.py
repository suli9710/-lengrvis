#!/usr/bin/env python3
"""Create fail-closed paid-launch reviewed-evidence templates."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUPPORT_TEMPLATE_TYPE = "support-privacy-operations-evidence-template"
CLAIMS_TEMPLATE_TYPE = "claims-launch-evidence-template"
OPERATIONS_TEMPLATE_TYPE = "commercial-operations-evidence-template"


def _pending_check(label: str = "") -> dict[str, str]:
    return {"status": "pending", "evidence_label": label}


def _candidate_binding(
    *,
    candidate_commit: str,
    build_identifier: str,
    candidate_repository: str = "uncollected",
    candidate_run_id: str = "uncollected",
    candidate_run_attempt: str = "uncollected",
) -> dict[str, str]:
    return {
        "commit": candidate_commit,
        "build_identifier": build_identifier,
        "repository": candidate_repository,
        "ci_run_id": candidate_run_id,
        "ci_run_attempt": candidate_run_attempt,
    }


def build_support_privacy_template(
    *,
    candidate_commit: str,
    build_identifier: str,
    candidate_repository: str = "uncollected",
    candidate_run_id: str = "uncollected",
    candidate_run_attempt: str = "uncollected",
) -> dict[str, Any]:
    checks = {
        key: _pending_check()
        for key in (
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
    }
    return {
        "artifact_type": SUPPORT_TEMPLATE_TYPE,
        "template_mode": "not_reviewed_evidence",
        "candidate": _candidate_binding(
            candidate_commit=candidate_commit,
            build_identifier=build_identifier,
            candidate_repository=candidate_repository,
            candidate_run_id=candidate_run_id,
            candidate_run_attempt=candidate_run_attempt,
        ),
        "ownership": {
            "status": "pending",
            "primary_support_owner_label": "",
            "backup_support_owner_label": "",
            "privacy_owner_label": "",
            "security_escalation_label": "",
            "public_support_channel_label": "",
            "public_privacy_channel_label": "",
        },
        "operating_model": {
            "support_scope": _pending_check(),
            "intake": _pending_check(),
            "severity_routing": _pending_check(),
            "diagnostic_package_handling": _pending_check(),
            "data_subject_requests": _pending_check(),
            "retention": _pending_check(),
            "response_ownership": _pending_check(),
            "jurisdiction_guidance_label": "",
        },
        "release_rehearsal": {"status": "pending", "checks": checks},
        "review": {
            "status": "pending",
            "reviewer_label": "",
            "reviewed_at_utc": "",
        },
        "summary": {
            "support_privacy_ready": False,
            "public_support_launch_signoff": False,
            "release_signoff": False,
        },
        "claim_controls": {
            "template_is_reviewed_evidence": False,
            "paid_launch_claim_allowed": False,
            "release_signoff": False,
        },
        "must_not_be_recorded_as": [
            "support/privacy operations pass",
            "paid-launch pass",
            "release sign-off",
        ],
    }


def build_claims_launch_template(
    *,
    candidate_commit: str,
    build_identifier: str,
    candidate_repository: str = "uncollected",
    candidate_run_id: str = "uncollected",
    candidate_run_attempt: str = "uncollected",
) -> dict[str, Any]:
    return {
        "artifact_type": CLAIMS_TEMPLATE_TYPE,
        "template_mode": "not_reviewed_evidence",
        "candidate": _candidate_binding(
            candidate_commit=candidate_commit,
            build_identifier=build_identifier,
            candidate_repository=candidate_repository,
            candidate_run_id=candidate_run_id,
            candidate_run_attempt=candidate_run_attempt,
        ),
        "pricing": {
            "status": "pending",
            "approved_pricing_page_label": "",
            "tax_and_payment_terms_label": "",
        },
        "feature_matrix": {
            "status": "pending",
            "docs_pricing_review_label": "",
            "entitlement_code_review_label": "",
        },
        "entitlement_tests": {"status": "pending", "test_run_label": ""},
        "platform_preview_labels": {"status": "pending", "review_label": ""},
        "security_privacy_claims": {"status": "pending", "review_label": ""},
        "release_notes": {"status": "pending", "review_label": ""},
        "onboarding": {"status": "pending", "review_label": ""},
        "rollback_communication": {"status": "pending", "review_label": ""},
        "review": {
            "status": "pending",
            "reviewer_label": "",
            "reviewed_at_utc": "",
        },
        "summary": {
            "claims_ready": False,
            "public_launch_signoff": False,
            "release_signoff": False,
        },
        "claim_controls": {
            "template_is_reviewed_evidence": False,
            "paid_launch_claim_allowed": False,
            "release_signoff": False,
        },
        "repository_contracts": {
            "pricing_source": "docs/pricing.md",
            "business_pricing_pointer": "docs/business/pricing.md",
            "entitlements_source": "backend/app/commerce/entitlements.py",
            "usage_quota_source": "backend/app/commerce/usage.py",
        },
        "must_not_be_recorded_as": [
            "public claims approval",
            "paid-launch pass",
            "release sign-off",
        ],
    }


def build_commercial_operations_template(
    *,
    candidate_commit: str,
    build_identifier: str,
    candidate_repository: str = "uncollected",
    candidate_run_id: str = "uncollected",
    candidate_run_attempt: str = "uncollected",
) -> dict[str, Any]:
    return {
        "artifact_type": OPERATIONS_TEMPLATE_TYPE,
        "template_mode": "not_reviewed_evidence",
        "candidate": _candidate_binding(
            candidate_commit=candidate_commit,
            build_identifier=build_identifier,
            candidate_repository=candidate_repository,
            candidate_run_id=candidate_run_id,
            candidate_run_attempt=candidate_run_attempt,
        ),
        "operations": {"scope": "paid_public_launch"},
        "contracting": {
            "status": "pending",
            "entity_label": "",
            "public_business_address_or_exemption_label": "",
            "billing_descriptor_label": "",
            "legal_contact_label": "",
            "privacy_contact_label": "",
            "support_contact_label": "",
        },
        "tax": {
            "status": "pending",
            "tax_owner_label": "",
            "tax_registration_or_exemption_label": "",
            "tax_jurisdiction_matrix_label": "",
            "product_taxability_review_label": "",
            "invoice_tax_display_label": "",
            "remittance_accounting_runbook_label": "",
        },
        "payment_collection": {
            "status": "pending",
            "collection_model_label": "",
            "processor_or_manual_invoice_account_label": "",
            "checkout_or_invoice_flow_label": "",
            "receipt_invoice_sample_label": "",
            "reconciliation_runbook_label": "",
            "webhook_or_manual_settlement_label": "",
            "chargeback_runbook_label": "",
            "no_card_or_bank_secrets_in_repo_label": "",
        },
        "legal": {
            "status": "pending",
            "counsel_review_label": "",
            "legal_source_register_label": "",
            "legal_risk_memo_label": "",
            "eula_final_label": "",
            "privacy_policy_final_label": "",
            "refund_policy_final_label": "",
            "dpa_sla_applicability_label": "",
            "consumer_withdrawal_terms_label": "",
            "supported_jurisdictions_label": "",
            "public_contact_terms_label": "",
        },
        "support": {
            "status": "pending",
            "support_privacy_evidence_label": "",
            "monitored_support_channel_label": "",
            "owner_rota_label": "",
            "severity_sla_terms_label": "",
            "privacy_escalation_label": "",
            "diagnostic_retention_label": "",
            "customer_script_label": "",
        },
        "refunds": {
            "status": "pending",
            "refund_policy_label": "",
            "refund_request_intake_label": "",
            "refund_decision_matrix_label": "",
            "refund_to_license_revocation_label": "",
            "refund_receipt_or_credit_note_label": "",
            "chargeback_refund_collision_label": "",
            "refund_log_reconciliation_label": "",
        },
        "public_claims": {
            "status": "pending",
            "claims_launch_evidence_label": "",
            "claims_register_label": "",
            "pricing_page_label": "",
            "feature_matrix_entitlement_alignment_label": "",
            "security_privacy_claims_review_label": "",
            "prohibited_claims_label": "",
            "launch_rollback_copy_label": "",
        },
        "cross_evidence": {
            "status": "pending",
            "commercial_loop_evidence_label": "",
            "support_privacy_evidence_label": "",
            "claims_launch_evidence_label": "",
            "market_dashboard_row_update_label": "",
        },
        "review": {
            "status": "pending",
            "reviewer_label": "",
            "reviewed_at_utc": "",
        },
        "summary": {
            "commercial_operations_ready": False,
            "paid_public_launch_signoff": False,
            "release_signoff": False,
        },
        "claim_controls": {
            "template_is_reviewed_evidence": False,
            "paid_launch_claim_allowed": False,
            "release_signoff": False,
        },
        "repository_contracts": {
            "market_dashboard": "docs/business/market-readiness.md",
            "commercial_operations_runbook": "docs/business/commercial-operations.md",
            "payment_tax_runbook": "docs/business/payment-tax-operations.md",
            "support_refund_runbook": "docs/business/support-refund-operations.md",
            "public_claims_register": "docs/business/public-claims-register.md",
            "commercial_legal_checklist": "docs/legal/commercial-legal-approval-checklist.md",
            "legal_source_register": "docs/legal/legal-source-register.md",
            "commercial_legal_risk_memo": "docs/legal/commercial-legal-risk-memo.md",
            "legal_index": "docs/legal/README.md",
            "pricing_source": "docs/pricing.md",
            "refund_policy": "docs/legal/refund-policy.md",
        },
        "must_not_be_recorded_as": [
            "commercial operations pass",
            "paid-launch pass",
            "legal approval",
            "tax approval",
            "release sign-off",
        ],
    }


def write_templates(
    output_dir: Path,
    *,
    candidate_commit: str,
    build_identifier: str,
    candidate_repository: str = "uncollected",
    candidate_run_id: str = "uncollected",
    candidate_run_attempt: str = "uncollected",
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    support = build_support_privacy_template(
        candidate_commit=candidate_commit,
        build_identifier=build_identifier,
        candidate_repository=candidate_repository,
        candidate_run_id=candidate_run_id,
        candidate_run_attempt=candidate_run_attempt,
    )
    claims = build_claims_launch_template(
        candidate_commit=candidate_commit,
        build_identifier=build_identifier,
        candidate_repository=candidate_repository,
        candidate_run_id=candidate_run_id,
        candidate_run_attempt=candidate_run_attempt,
    )
    operations = build_commercial_operations_template(
        candidate_commit=candidate_commit,
        build_identifier=build_identifier,
        candidate_repository=candidate_repository,
        candidate_run_id=candidate_run_id,
        candidate_run_attempt=candidate_run_attempt,
    )
    support_path = output_dir / "support-privacy-operations-evidence.template.json"
    claims_path = output_dir / "claims-launch-evidence.template.json"
    operations_path = output_dir / "commercial-operations-evidence.template.json"
    markdown_path = output_dir / "paid-launch-evidence-templates.md"
    support_path.write_text(json.dumps(support, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    claims_path.write_text(json.dumps(claims, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    operations_path.write_text(json.dumps(operations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(
        "\n".join(
            [
                "# Paid Launch Evidence Templates",
                "",
                f"- Generated at UTC: {datetime.now(UTC).isoformat()}",
                f"- Support/privacy template: {support_path}",
                f"- Claims template: {claims_path}",
                f"- Commercial operations template: {operations_path}",
                "",
                "These files are templates only. They are not reviewed evidence, not a paid-launch pass, and not release sign-off.",
                "Copy completed, reviewed, signed evidence to the verifier paths only after real artifacts and reviewer labels exist:",
                "",
                "- build/support-privacy-operations-evidence-reviewed.json",
                "- build/claims-launch-evidence-reviewed.json",
                "- build/commercial-operations-evidence-reviewed.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "support_privacy_template": str(support_path),
        "claims_launch_template": str(claims_path),
        "commercial_operations_template": str(operations_path),
        "markdown": str(markdown_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=".tmp/paid-launch-evidence-templates")
    parser.add_argument(
        "--candidate-commit",
        default=os.getenv("LENGRVIS_RELEASE_CANDIDATE_COMMIT", "uncollected"),
    )
    parser.add_argument(
        "--build-identifier",
        default=os.getenv("LENGRVIS_RELEASE_BUILD_IDENTIFIER", "uncollected"),
    )
    parser.add_argument(
        "--candidate-repository",
        default=os.getenv("LENGRVIS_RELEASE_CANDIDATE_REPOSITORY", "uncollected"),
    )
    parser.add_argument(
        "--candidate-run-id",
        default=os.getenv("LENGRVIS_RELEASE_CANDIDATE_RUN_ID", "uncollected"),
    )
    parser.add_argument(
        "--candidate-run-attempt",
        default=os.getenv("LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT", "uncollected"),
    )
    args = parser.parse_args()

    paths = write_templates(
        Path(args.output_dir),
        candidate_commit=args.candidate_commit,
        build_identifier=args.build_identifier,
        candidate_repository=args.candidate_repository,
        candidate_run_id=args.candidate_run_id,
        candidate_run_attempt=args.candidate_run_attempt,
    )
    payload = {
        "ok": True,
        "mode": "template_only_not_reviewed_evidence",
        "claim_controls": {
            "paid_launch_claim_allowed": False,
            "release_signoff": False,
        },
        "outputs": paths,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
