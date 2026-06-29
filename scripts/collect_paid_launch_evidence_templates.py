#!/usr/bin/env python3
"""Create fail-closed paid-launch reviewed-evidence templates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUPPORT_TEMPLATE_TYPE = "support-privacy-operations-evidence-template"
CLAIMS_TEMPLATE_TYPE = "claims-launch-evidence-template"


def _pending_check(label: str = "") -> dict[str, str]:
    return {"status": "pending", "evidence_label": label}


def build_support_privacy_template(*, candidate_commit: str, build_identifier: str) -> dict[str, Any]:
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
        "candidate": {
            "commit": candidate_commit,
            "build_identifier": build_identifier,
        },
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


def build_claims_launch_template(*, candidate_commit: str, build_identifier: str) -> dict[str, Any]:
    return {
        "artifact_type": CLAIMS_TEMPLATE_TYPE,
        "template_mode": "not_reviewed_evidence",
        "candidate": {
            "commit": candidate_commit,
            "build_identifier": build_identifier,
        },
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


def write_templates(output_dir: Path, *, candidate_commit: str, build_identifier: str) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    support = build_support_privacy_template(
        candidate_commit=candidate_commit,
        build_identifier=build_identifier,
    )
    claims = build_claims_launch_template(
        candidate_commit=candidate_commit,
        build_identifier=build_identifier,
    )
    support_path = output_dir / "support-privacy-operations-evidence.template.json"
    claims_path = output_dir / "claims-launch-evidence.template.json"
    markdown_path = output_dir / "paid-launch-evidence-templates.md"
    support_path.write_text(json.dumps(support, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    claims_path.write_text(json.dumps(claims, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(
        "\n".join(
            [
                "# Paid Launch Evidence Templates",
                "",
                f"- Generated at UTC: {datetime.now(UTC).isoformat()}",
                f"- Support/privacy template: {support_path}",
                f"- Claims template: {claims_path}",
                "",
                "These files are templates only. They are not reviewed evidence, not a paid-launch pass, and not release sign-off.",
                "Copy completed, reviewed, signed evidence to the verifier paths only after real artifacts and reviewer labels exist:",
                "",
                "- build/support-privacy-operations-evidence-reviewed.json",
                "- build/claims-launch-evidence-reviewed.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "support_privacy_template": str(support_path),
        "claims_launch_template": str(claims_path),
        "markdown": str(markdown_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=".tmp/paid-launch-evidence-templates")
    parser.add_argument("--candidate-commit", default="uncollected")
    parser.add_argument("--build-identifier", default="uncollected")
    args = parser.parse_args()

    paths = write_templates(
        Path(args.output_dir),
        candidate_commit=args.candidate_commit,
        build_identifier=args.build_identifier,
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
