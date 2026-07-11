#!/usr/bin/env python3
"""Validate reviewed paid commercial operations evidence."""

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

ARTIFACT_TYPE = "commercial-operations-evidence-reviewed"
DEFAULT_EVIDENCE = "build/commercial-operations-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_COMMERCIAL_OPERATIONS_EVIDENCE_PATH"


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
    require_nonempty(payload, "operations.scope", errors)
    if get_path(payload, "operations.scope") != "paid_public_launch":
        errors.append("operations.scope must be 'paid_public_launch'")

    _require_contracting(payload, errors)
    _require_tax(payload, errors)
    _require_payment_collection(payload, errors)
    _require_legal(payload, errors)
    _require_support(payload, errors)
    _require_refunds(payload, errors)
    _require_public_claims(payload, errors)
    _require_cross_evidence(payload, errors)
    _require_repository_contracts(repo_root or Path.cwd(), errors)

    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.commercial_operations_ready", errors)
    require_false(payload, "summary.paid_public_launch_signoff", errors)
    require_false(payload, "summary.release_signoff", errors)
    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.release_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _require_contracting(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "contracting.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "contracting.entity_label",
            "contracting.public_business_address_or_exemption_label",
            "contracting.billing_descriptor_label",
            "contracting.legal_contact_label",
            "contracting.privacy_contact_label",
            "contracting.support_contact_label",
        ),
    )


def _require_tax(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "tax.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "tax.tax_owner_label",
            "tax.tax_registration_or_exemption_label",
            "tax.tax_jurisdiction_matrix_label",
            "tax.product_taxability_review_label",
            "tax.invoice_tax_display_label",
            "tax.remittance_accounting_runbook_label",
        ),
    )


def _require_payment_collection(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "payment_collection.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "payment_collection.collection_model_label",
            "payment_collection.processor_or_manual_invoice_account_label",
            "payment_collection.checkout_or_invoice_flow_label",
            "payment_collection.receipt_invoice_sample_label",
            "payment_collection.reconciliation_runbook_label",
            "payment_collection.webhook_or_manual_settlement_label",
            "payment_collection.chargeback_runbook_label",
            "payment_collection.no_card_or_bank_secrets_in_repo_label",
        ),
    )


def _require_legal(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "legal.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "legal.counsel_review_label",
            "legal.legal_source_register_label",
            "legal.legal_risk_memo_label",
            "legal.eula_final_label",
            "legal.privacy_policy_final_label",
            "legal.refund_policy_final_label",
            "legal.dpa_sla_applicability_label",
            "legal.consumer_withdrawal_terms_label",
            "legal.supported_jurisdictions_label",
            "legal.public_contact_terms_label",
        ),
    )


def _require_support(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "support.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "support.support_privacy_evidence_label",
            "support.monitored_support_channel_label",
            "support.owner_rota_label",
            "support.severity_sla_terms_label",
            "support.privacy_escalation_label",
            "support.diagnostic_retention_label",
            "support.customer_script_label",
        ),
    )


def _require_refunds(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "refunds.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "refunds.refund_policy_label",
            "refunds.refund_request_intake_label",
            "refunds.refund_decision_matrix_label",
            "refunds.refund_to_license_revocation_label",
            "refunds.refund_receipt_or_credit_note_label",
            "refunds.chargeback_refund_collision_label",
            "refunds.refund_log_reconciliation_label",
        ),
    )


def _require_public_claims(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "public_claims.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "public_claims.claims_launch_evidence_label",
            "public_claims.claims_register_label",
            "public_claims.pricing_page_label",
            "public_claims.feature_matrix_entitlement_alignment_label",
            "public_claims.security_privacy_claims_review_label",
            "public_claims.prohibited_claims_label",
            "public_claims.launch_rollback_copy_label",
        ),
    )


def _require_cross_evidence(payload: dict[str, Any], errors: list[str]) -> None:
    require_passed(payload, "cross_evidence.status", errors)
    _require_nonempty_paths(
        payload,
        errors,
        (
            "cross_evidence.commercial_loop_evidence_label",
            "cross_evidence.support_privacy_evidence_label",
            "cross_evidence.claims_launch_evidence_label",
            "cross_evidence.market_dashboard_row_update_label",
        ),
    )


def _require_repository_contracts(repo_root: Path, errors: list[str]) -> None:
    root = repo_root.resolve()
    required = (
        root / "docs" / "business" / "market-readiness.md",
        root / "docs" / "business" / "commercial-operations.md",
        root / "docs" / "business" / "payment-tax-operations.md",
        root / "docs" / "business" / "support-refund-operations.md",
        root / "docs" / "business" / "public-claims-register.md",
        root / "docs" / "business" / "support-privacy-operations.md",
        root / "docs" / "business" / "license-operations.md",
        root / "docs" / "legal" / "commercial-legal-approval-checklist.md",
        root / "docs" / "legal" / "legal-source-register.md",
        root / "docs" / "legal" / "commercial-legal-risk-memo.md",
        root / "docs" / "legal" / "README.md",
        root / "docs" / "legal" / "eula.md",
        root / "docs" / "legal" / "privacy-policy.md",
        root / "docs" / "legal" / "refund-policy.md",
        root / "docs" / "legal" / "data-processing-addendum.md",
        root / "docs" / "legal" / "sla.md",
        root / "docs" / "pricing.md",
    )
    for path in required:
        if not path.exists():
            errors.append(f"required commercial operations source is missing: {path.relative_to(root)}")
            return

    market_text = (root / "docs" / "business" / "market-readiness.md").read_text(encoding="utf-8")
    operations_text = (root / "docs" / "business" / "commercial-operations.md").read_text(encoding="utf-8")
    payment_tax_text = (root / "docs" / "business" / "payment-tax-operations.md").read_text(encoding="utf-8")
    support_refund_text = (root / "docs" / "business" / "support-refund-operations.md").read_text(encoding="utf-8")
    claims_register_text = (root / "docs" / "business" / "public-claims-register.md").read_text(encoding="utf-8")
    legal_checklist_text = (
        root / "docs" / "legal" / "commercial-legal-approval-checklist.md"
    ).read_text(encoding="utf-8")
    legal_source_text = (root / "docs" / "legal" / "legal-source-register.md").read_text(encoding="utf-8")
    legal_memo_text = (root / "docs" / "legal" / "commercial-legal-risk-memo.md").read_text(encoding="utf-8")
    legal_index_text = (root / "docs" / "legal" / "README.md").read_text(encoding="utf-8")
    refund_text = (root / "docs" / "legal" / "refund-policy.md").read_text(encoding="utf-8")
    pricing_text = (root / "docs" / "pricing.md").read_text(encoding="utf-8")

    for marker in (
        "npm run evidence:commercial-operations-verify",
        "MR-P0-001",
        "MR-P0-002",
        "MR-P0-003",
        "MR-P0-005",
        "MR-P0-006",
    ):
        if marker not in market_text:
            errors.append(f"market readiness dashboard is missing commercial operations marker: {marker}")
    if "commercial-operations-evidence-reviewed" not in operations_text:
        errors.append("docs/business/commercial-operations.md must describe reviewed operations evidence")
    for marker in (
        "收款",
        "税务",
        "对账",
        "拒付",
        "不得记录支付秘密",
    ):
        if marker not in payment_tax_text:
            errors.append(f"payment/tax operations runbook is missing marker: {marker}")
    for marker in (
        "账单 case 分类",
        "退款决策矩阵",
        "退款到许可证吊销",
        "已发起拒付",
    ):
        if marker not in support_refund_text:
            errors.append(f"support/refund operations runbook is missing marker: {marker}")
    for marker in ("允许的内部 Claims", "禁止 Claims", "审查记录", "claims_register_label"):
        if marker not in claims_register_text:
            errors.append(f"public claims register is missing marker: {marker}")
    for marker in (
        "counsel approval",
        "EULA",
        "隐私政策",
        "退款政策",
        "支持销售法域",
    ):
        if marker not in legal_checklist_text:
            errors.append(f"commercial legal approval checklist is missing marker: {marker}")
    for marker in (
        "全国人民代表大会",
        "EUR-Lex",
        "FTC Negative Option Rule",
        "CCPA/CPRA",
        "legal.legal_source_register_label",
    ):
        if marker not in legal_source_text:
            errors.append(f"legal source register is missing marker: {marker}")
    for marker in (
        "阻断性法律发现",
        "LGL-P0-001",
        "条款同意",
        "隐私告知与权利",
        "法务 Go/No-Go",
    ):
        if marker not in legal_memo_text:
            errors.append(f"commercial legal risk memo is missing marker: {marker}")
    if "fail-closed" not in legal_index_text or "商业收款与公开付费发布" not in legal_index_text:
        errors.append("docs/legal/README.md must keep fail-closed legal/commercial launch language")
    for marker in ("退款", "吊销", "支付系统尚未上线"):
        if marker not in refund_text:
            errors.append(f"docs/legal/refund-policy.md is missing refund operations marker: {marker}")
    if "不构成公开报价或购买要约" not in pricing_text:
        errors.append("docs/pricing.md must keep the non-public-offer disclaimer")


def _require_nonempty_paths(payload: dict[str, Any], errors: list[str], paths: tuple[str, ...]) -> None:
    for path in paths:
        require_nonempty(payload, path, errors)


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
