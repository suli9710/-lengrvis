from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_script(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), SCRIPTS_DIR / name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


distribution = _load_script("verify_distribution_release_evidence.py")
clean_machine = _load_script("verify_clean_machine_evidence.py")
commercial = _load_script("verify_commercial_loop_evidence.py")
result_quality = _load_script("verify_result_quality_reviewed_evidence.py")
diagnostics_reviewed = _load_script("verify_diagnostics_external_reviewed_evidence.py")
support_privacy = _load_script("verify_support_privacy_rehearsal_evidence.py")
claims_launch = _load_script("verify_launch_claims_reviewed_evidence.py")
commercial_operations = _load_script("verify_commercial_operations_evidence.py")
commercial_operations_seal = _load_script("seal_commercial_operations_evidence.py")
reviewed_release_seal = _load_script("seal_reviewed_release_evidence.py")
paid_launch_templates = _load_script("collect_paid_launch_evidence_templates.py")
evidence_contracts = _load_script("evidence_contracts.py")
candidate_binding_check = _load_script("verify_release_candidate_binding.py")
evidence_keypair = _load_script("generate_reviewed_evidence_keypair.py")
TEST_EVIDENCE_PRIVATE_KEY = "ed25519:" + base64.urlsafe_b64encode(
    sha256(b"release-evidence-contract-test-key").digest()
).decode("ascii").rstrip("=")
TEST_EVIDENCE_PUBLIC_KEY = evidence_contracts.evidence_public_key_text(
    evidence_contracts.load_evidence_private_key(TEST_EVIDENCE_PRIVATE_KEY)
)
STRICT_CANDIDATE_BINDING = {
    "commit": "a" * 40,
    "build_identifier": f"rc-12345-2-{'a' * 40}",
    "repository": "lengrvis/mavris",
    "ci_run_id": "12345",
    "ci_run_attempt": "2",
}


@pytest.fixture(autouse=True)
def _configure_reviewed_evidence_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    monkeypatch.delenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, raising=False)


@pytest.mark.parametrize(
    ("private_key", "message"),
    [
        ("", "is required"),
        ("not-an-ed25519-key", "ed25519: prefix"),
        ("ed25519:YQ", "invalid Ed25519 length"),
        (
            "ed25519:" + base64.b64encode(b"\xfb" * 32).decode("ascii").rstrip("="),
            "not valid base64url",
        ),
    ],
)
def test_reviewed_evidence_private_key_parser_rejects_invalid_values(private_key: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        evidence_contracts.load_evidence_private_key(private_key)


def test_reviewed_evidence_keypair_generator_writes_distinct_verifiable_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = tmp_path / "reviewer-private.key"
    public_path = tmp_path / "verifier-public.key"
    protect_calls: list[tuple[int, tuple[int, int, int], bool]] = []
    if sys.platform == "win32":
        protect_private = evidence_keypair._protect_windows_private_key_file

        def _observe_protection(path, *, descriptor, expected_identity, repair):
            assert evidence_keypair._file_identity_from_descriptor(descriptor) == expected_identity
            if not repair:
                assert path.stat().st_size == 0
            protect_calls.append((descriptor, expected_identity, repair))
            protect_private(
                path,
                descriptor=descriptor,
                expected_identity=expected_identity,
                repair=repair,
            )

        monkeypatch.setattr(
            evidence_keypair,
            "_protect_windows_private_key_file",
            _observe_protection,
        )

    fingerprint = evidence_keypair.write_keypair(
        private_key_path=private_path,
        public_key_path=public_path,
    )

    private_key = evidence_contracts.load_evidence_private_key(private_path.read_text(encoding="utf-8"))
    public_text = public_path.read_text(encoding="utf-8").strip()
    assert evidence_contracts.evidence_public_key_text(private_key) == public_text
    assert fingerprint == evidence_contracts.evidence_public_key_fingerprint(
        evidence_contracts.load_evidence_public_key(public_text)
    )
    if sys.platform == "win32":
        assert len(protect_calls) == 2
        assert protect_calls[0][:2] == protect_calls[1][:2]
        assert [call[2] for call in protect_calls] == [False, True]
        powershell = (
            evidence_keypair._trusted_windows_directory() / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        )
        acl_check = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "$acl=[IO.File]::GetAccessControl($env:LENGRVIS_TEST_PRIVATE_KEY_PATH);"
                    "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;"
                    "$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]));"
                    "$allow=@($rules|"
                    "Where-Object {$_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow});"
                    "$owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]);"
                    "$full=[Security.AccessControl.FileSystemRights]::FullControl;"
                    "$item=Get-Item -LiteralPath $env:LENGRVIS_TEST_PRIVATE_KEY_PATH -Force;"
                    "if(-not $acl.AreAccessRulesProtected -or $rules.Count -ne 1 -or "
                    "$allow.Count -ne 1 -or $owner.Value -ne $sid.Value -or "
                    "$allow[0].IdentityReference.Value -ne $sid.Value -or "
                    "($allow[0].FileSystemRights -band $full) -ne $full -or "
                    "($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){exit 1}"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "LENGRVIS_TEST_PRIVATE_KEY_PATH": str(private_path)},
        )
        assert acl_check.returncode == 0, acl_check.stderr
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        evidence_keypair.write_keypair(
            private_key_path=private_path,
            public_key_path=public_path,
        )


def test_reviewed_evidence_keypair_cleanup_does_not_delete_replaced_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reviewer-private.key"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        identity = evidence_keypair._file_identity_from_descriptor(descriptor)
    finally:
        os.close(descriptor)
    replacement = tmp_path / "replacement.key"
    replacement.write_text("replacement-must-survive\n", encoding="utf-8")
    os.replace(replacement, path)

    evidence_keypair._unlink_if_same_file(path, identity)

    assert path.read_text(encoding="utf-8") == "replacement-must-survive\n"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle conversion only")
def test_reviewed_evidence_keypair_handle_conversion_failure_leaves_secure_empty_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msvcrt

    path = tmp_path / "reviewer-private.key"

    def _fail_handle_conversion(_handle: int, _flags: int) -> int:
        raise OSError("injected CRT handle conversion failure")

    open_osfhandle = msvcrt.open_osfhandle
    monkeypatch.setattr(msvcrt, "open_osfhandle", _fail_handle_conversion)

    with pytest.raises(OSError, match="injected CRT handle conversion failure"):
        evidence_keypair._open_windows_private_key_file(path)
    monkeypatch.setattr(msvcrt, "open_osfhandle", open_osfhandle)

    assert path.is_file()
    assert path.stat().st_size == 0
    descriptor = os.open(path, os.O_RDONLY)
    try:
        identity = evidence_keypair._file_identity_from_descriptor(descriptor)
        evidence_keypair._protect_windows_private_key_file(
            path,
            descriptor=descriptor,
            expected_identity=identity,
            repair=False,
        )
    finally:
        os.close(descriptor)


def _distribution_sample() -> dict:
    return _signed(
        {
            "artifact_type": "distribution-release-evidence-reviewed",
            "candidate": {
                "commit": "abc123",
                "build_identifier": "ci-123",
                "artifact_label": "installer-redacted-label",
                "artifact_sha256": "a" * 64,
            },
            "signing": {
                "subject": "CN=redacted publisher",
                "thumbprint": "thumbprint-redacted-label",
                "status": "passed",
            },
            "checks": {
                "artifact_hash": "passed",
                "signature_verification": "passed",
                "upgrade": "passed",
                "rollback": "passed",
                "uninstall": "passed",
            },
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {"distribution_pass": True, "release_signoff": False},
        }
    )


def _clean_machine_sample() -> dict:
    return _signed(
        {
            "artifact_type": "clean-machine-release-evidence-reviewed",
            "candidate": {
                "commit": "abc123",
                "build_identifier": "ci-123",
                "artifact_label": "portable-redacted-label",
                "artifact_sha256": "b" * 64,
            },
            "machine": {
                "profile_label_redacted": "clean-profile-redacted",
                "os_label_redacted": "windows-11-redacted",
            },
            "audit_anchor": {
                "anchor_label": "audit-anchor-redacted",
                "anchor_sha256": "c" * 64,
                "verify_audit_log": "passed",
            },
            "claims": {"privacy_mode_or_local_model": True},
            "checks": {
                "install": "passed",
                "launch": "passed",
                "backend_health": "passed",
                "first_read_only_task": "passed",
                "diagnostics_export": "passed",
                "uninstall_or_rollback": "passed",
                "screenshot_log_redaction_review": "passed",
            },
            "local_model": {
                "runtime": "ollama",
                "runtime_version": "runtime-version-redacted",
                "model": "model-redacted",
                "model_version": "model-version-redacted",
                "install": "passed",
                "start": "passed",
                "pull": "passed",
                "privacy_task_smoke": "passed",
            },
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "clean_machine_pass": True,
                "local_model_pass": True,
                "release_signoff": False,
            },
        }
    )


def _commercial_sample() -> dict:
    return _signed(
        {
            "artifact_type": "commercial-loop-evidence-reviewed",
            "candidate": {"commit": "abc123", "build_identifier": "ci-123"},
            "pilot": {"scope": "subscription_activation_free_plus_pro"},
            "contracting": {
                "status": "passed",
                "entity_label": "contracting-entity-redacted",
                "tax_treatment_label": "tax-treatment-redacted",
                "billing_descriptor_label": "billing-descriptor-redacted",
            },
            "legal": {
                "status": "passed",
                "eula_approval_label": "eula-approval-redacted",
                "privacy_policy_approval_label": "privacy-approval-redacted",
                "refund_policy_approval_label": "refund-approval-redacted",
                "supported_jurisdictions_label": "jurisdictions-redacted",
            },
            "payment_pilot": {
                "status": "passed",
                "processor_or_manual_invoice_label": "manual-invoice-redacted",
                "receipt_or_invoice_label": "receipt-redacted",
                "refund_rehearsal_label": "refund-rehearsal-redacted",
                "chargeback_runbook_label": "chargeback-runbook-redacted",
            },
            "subscription_activation": {
                "status": "passed",
                "activation_api_https_label": "activation-https-redacted",
                "reverse_proxy_label": "reverse-proxy-redacted",
                "activation_key_creation_label": "activation-key-created-redacted",
                "first_activation_label": "first-activation-redacted",
                "idempotent_repeat_activation_label": "repeat-activation-redacted",
                "device_limit_label": "device-limit-redacted",
                "strong_device_binding_label": "strong-device-binding-redacted",
                "renewal_refresh_label": "renewal-refresh-redacted",
                "cancel_period_end_label": "cancel-period-end-redacted",
                "refund_revocation_label": "refund-revocation-redacted",
                "expired_downgrade_label": "expired-downgrade-redacted",
                "rate_limit_label": "rate-limit-redacted",
                "activation_audit_log_label": "activation-audit-redacted",
                "operations_runbook_label": "activation-ops-redacted",
                "secret_redaction_label": "secret-redaction-redacted",
            },
            "license_issuer": {
                "status": "passed",
                "key_profile": "production",
                "public_key_fingerprint_label": "fingerprint-redacted",
                "private_key_custody_label": "custody-redacted",
                "issuance_log_label": "issuance-log-redacted",
                "revocation_manifest_freshness_label": "revocation-freshness-redacted",
                "issuance_rehearsal": "passed",
                "renewal_rehearsal": "passed",
                "replacement_rehearsal": "passed",
                "revocation_rehearsal": "passed",
            },
            "support_privacy": {
                "status": "passed",
                "support_channel_label": "support-channel-redacted",
                "privacy_request_runbook_label": "privacy-runbook-redacted",
                "diagnostic_handling_label": "diagnostics-handling-redacted",
            },
            "claims": {
                "status": "passed",
                "pricing_page_label": "pricing-review-redacted",
                "feature_matrix_label": "feature-matrix-redacted",
                "preview_labels_review_label": "preview-labels-redacted",
                "security_privacy_claims_review_label": "claims-review-redacted",
            },
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "subscription_activation_ready": True,
                "self_serve_checkout_enabled": False,
                "commercial_launch_signoff": False,
            },
        }
    )


def _result_quality_tasks() -> list[dict]:
    categories = {
        "file_search_or_organization_dry_run": 5,
        "document_summary_or_qa": 5,
        "system_diagnostics": 3,
        "browser_read_only_or_constrained": 3,
        "reversible_file_modification_with_approval": 5,
        "destructive_or_system_refusal_or_approval": 5,
        "mobile_approval_or_remote_supervision": 2,
        "local_model_privacy_mode": 2,
    }
    tasks: list[dict] = []
    index = 0
    for category, count in categories.items():
        for _ in range(count):
            index += 1
            tasks.append(
                {
                    "task_artifact_label": f"artifact-label-{index}",
                    "user_visible_result": f"visible-result-label-{index}",
                    "expected_outcome": "expected redacted outcome",
                    "actual_outcome": "actual redacted outcome",
                    "result_status": "passed",
                    "safety_status": "correct",
                    "rework_needed": "none",
                    "reviewer_label": "reviewer-redacted",
                    "reviewed_at_utc": "2026-06-27T12:00:00Z",
                    "category": category,
                    "notes": "redacted notes",
                }
            )
    return tasks


def _result_quality_sample() -> dict:
    tasks = _result_quality_tasks()
    return _signed(
        {
            "artifact_type": "result-quality-review-evidence-reviewed",
            "candidate": {
                "commit": "abc123",
                "build_identifier": "ci-123",
            },
            "tasks": tasks,
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "result_quality_pass": True,
                "reviewed_task_count": len(tasks),
                "success_rate": 1.0,
                "rewrite_rate": 0.0,
                "safety_false_negative_count": 0,
                "rc_signoff": False,
                "release_signoff": False,
            },
        }
    )


def _diagnostics_reviewed_sample() -> dict:
    return _signed(
        {
            "artifact_type": "diagnostics-external-review-evidence-reviewed",
            "candidate": {
                "commit": "abc123",
                "build_identifier": "ci-123",
                "diagnostics_package_label": "diagnostics-package-redacted",
            },
            "checks": {
                "actual_exported_package_opened": "passed",
                "logs_reviewed": "passed",
                "path_labels_reviewed": "passed",
                "task_traces_reviewed": "passed",
                "model_traces_reviewed": "passed",
                "device_identifiers_reviewed": "passed",
                "credentials_and_secrets_reviewed": "passed",
                "redaction_reviewed": "passed",
                "external_sharing_decision_recorded": "passed",
            },
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
                "decision": "support_only",
            },
            "summary": {
                "diagnostics_review_pass": True,
                "public_safe": False,
                "external_sharing_allowed": False,
                "rc_signoff": False,
                "release_signoff": False,
            },
        }
    )


def _support_privacy_sample() -> dict:
    rehearsal_checks = {
        key: {"status": "passed", "evidence_label": f"{key}-redacted"}
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
    return _signed(
        {
            "artifact_type": "support-privacy-operations-evidence-reviewed",
            "candidate": {"commit": "abc123", "build_identifier": "ci-123"},
            "ownership": {
                "status": "passed",
                "primary_support_owner_label": "support-primary-redacted",
                "backup_support_owner_label": "support-backup-redacted",
                "privacy_owner_label": "privacy-owner-redacted",
                "security_escalation_label": "security-process-redacted",
                "public_support_channel_label": "support-channel-redacted",
                "public_privacy_channel_label": "privacy-channel-redacted",
            },
            "operating_model": {
                "support_scope": {"status": "passed", "label": "support-scope-redacted"},
                "intake": {"status": "passed", "label": "intake-redacted"},
                "severity_routing": {"status": "passed", "label": "severity-redacted"},
                "diagnostic_package_handling": {"status": "passed", "label": "diagnostics-redacted"},
                "data_subject_requests": {"status": "passed", "label": "dsr-redacted"},
                "retention": {"status": "passed", "label": "retention-redacted"},
                "response_ownership": {"status": "passed", "label": "response-owner-redacted"},
                "jurisdiction_guidance_label": "jurisdiction-guidance-redacted",
            },
            "release_rehearsal": {"status": "passed", "checks": rehearsal_checks},
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "support_privacy_ready": True,
                "public_support_launch_signoff": False,
                "release_signoff": False,
            },
        }
    )


def _claims_launch_sample() -> dict:
    return _signed(
        {
            "artifact_type": "claims-launch-evidence-reviewed",
            "candidate": {"commit": "abc123", "build_identifier": "ci-123"},
            "pricing": {
                "status": "passed",
                "approved_pricing_page_label": "pricing-page-redacted",
                "tax_and_payment_terms_label": "tax-payment-redacted",
            },
            "feature_matrix": {
                "status": "passed",
                "docs_pricing_review_label": "docs-pricing-review-redacted",
                "entitlement_code_review_label": "entitlement-code-review-redacted",
            },
            "entitlement_tests": {"status": "passed", "test_run_label": "entitlement-tests-redacted"},
            "platform_preview_labels": {"status": "passed", "review_label": "preview-labels-redacted"},
            "security_privacy_claims": {"status": "passed", "review_label": "claims-review-redacted"},
            "release_notes": {"status": "passed", "review_label": "release-notes-redacted"},
            "onboarding": {"status": "passed", "review_label": "onboarding-redacted"},
            "rollback_communication": {"status": "passed", "review_label": "rollback-redacted"},
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "claims_ready": True,
                "public_launch_signoff": False,
                "release_signoff": False,
            },
        }
    )


def _commercial_operations_sample() -> dict:
    return _signed(
        {
            "artifact_type": "commercial-operations-evidence-reviewed",
            "candidate": {"commit": "abc123", "build_identifier": "ci-123"},
            "operations": {"scope": "paid_public_launch"},
            "contracting": {
                "status": "passed",
                "entity_label": "contracting-entity-redacted",
                "public_business_address_or_exemption_label": "address-exemption-redacted",
                "billing_descriptor_label": "billing-descriptor-redacted",
                "legal_contact_label": "legal-contact-redacted",
                "privacy_contact_label": "privacy-contact-redacted",
                "support_contact_label": "support-contact-redacted",
            },
            "tax": {
                "status": "passed",
                "tax_owner_label": "tax-owner-redacted",
                "tax_registration_or_exemption_label": "tax-registration-redacted",
                "tax_jurisdiction_matrix_label": "tax-jurisdiction-redacted",
                "product_taxability_review_label": "product-taxability-redacted",
                "invoice_tax_display_label": "invoice-tax-redacted",
                "remittance_accounting_runbook_label": "remittance-runbook-redacted",
            },
            "payment_collection": {
                "status": "passed",
                "collection_model_label": "collection-model-redacted",
                "processor_or_manual_invoice_account_label": "processor-account-redacted",
                "checkout_or_invoice_flow_label": "checkout-flow-redacted",
                "receipt_invoice_sample_label": "receipt-sample-redacted",
                "reconciliation_runbook_label": "reconciliation-runbook-redacted",
                "webhook_or_manual_settlement_label": "settlement-redacted",
                "chargeback_runbook_label": "chargeback-redacted",
                "no_card_or_bank_secrets_in_repo_label": "no-payment-secrets-redacted",
            },
            "legal": {
                "status": "passed",
                "counsel_review_label": "counsel-review-redacted",
                "legal_source_register_label": "legal-source-register-redacted",
                "legal_risk_memo_label": "legal-review-memo-redacted",
                "eula_final_label": "eula-final-redacted",
                "privacy_policy_final_label": "privacy-final-redacted",
                "refund_policy_final_label": "refund-final-redacted",
                "dpa_sla_applicability_label": "dpa-sla-redacted",
                "consumer_withdrawal_terms_label": "withdrawal-terms-redacted",
                "supported_jurisdictions_label": "jurisdictions-redacted",
                "public_contact_terms_label": "public-contact-terms-redacted",
            },
            "support": {
                "status": "passed",
                "support_privacy_evidence_label": "support-privacy-evidence-redacted",
                "monitored_support_channel_label": "support-channel-redacted",
                "owner_rota_label": "owner-rota-redacted",
                "severity_sla_terms_label": "severity-sla-redacted",
                "privacy_escalation_label": "privacy-escalation-redacted",
                "diagnostic_retention_label": "diagnostic-retention-redacted",
                "customer_script_label": "customer-script-redacted",
            },
            "refunds": {
                "status": "passed",
                "refund_policy_label": "refund-policy-redacted",
                "refund_request_intake_label": "refund-intake-redacted",
                "refund_decision_matrix_label": "refund-matrix-redacted",
                "refund_to_license_revocation_label": "refund-revocation-redacted",
                "refund_receipt_or_credit_note_label": "refund-receipt-redacted",
                "chargeback_refund_collision_label": "chargeback-collision-redacted",
                "refund_log_reconciliation_label": "refund-reconciliation-redacted",
            },
            "public_claims": {
                "status": "passed",
                "claims_launch_evidence_label": "claims-evidence-redacted",
                "claims_register_label": "claims-register-redacted",
                "pricing_page_label": "pricing-page-redacted",
                "feature_matrix_entitlement_alignment_label": "feature-alignment-redacted",
                "security_privacy_claims_review_label": "security-privacy-review-redacted",
                "prohibited_claims_label": "prohibited-claims-redacted",
                "launch_rollback_copy_label": "launch-rollback-copy-redacted",
            },
            "cross_evidence": {
                "status": "passed",
                "commercial_loop_evidence_label": "commercial-loop-evidence-redacted",
                "support_privacy_evidence_label": "support-privacy-evidence-redacted",
                "claims_launch_evidence_label": "claims-launch-evidence-redacted",
                "market_dashboard_row_update_label": "market-dashboard-redacted",
            },
            "review": {
                "status": "passed",
                "reviewer_label": "reviewer-redacted",
                "reviewed_at_utc": "2026-06-27T12:00:00Z",
            },
            "summary": {
                "commercial_operations_ready": True,
                "paid_public_launch_signoff": False,
                "release_signoff": False,
            },
        }
    )


def _signed(payload: dict) -> dict:
    return evidence_contracts.seal_evidence_payload_signature(
        payload,
        private_key_text=TEST_EVIDENCE_PRIVATE_KEY,
    )


def _resign(payload: dict) -> dict:
    return evidence_contracts.seal_evidence_payload_signature(
        payload,
        private_key_text=TEST_EVIDENCE_PRIVATE_KEY,
    )


def _with_strict_candidate_binding(payload: dict) -> dict:
    body = deepcopy(payload)
    body["candidate"].update(STRICT_CANDIDATE_BINDING)
    return _resign(body)


def _strict_candidate_binding():
    return evidence_contracts.CandidateBinding(**STRICT_CANDIDATE_BINDING)


def _with_dist_artifact(
    payload: dict,
    tmp_path: Path,
    *,
    rel_path: str,
    contents: bytes,
) -> dict:
    artifact = tmp_path / rel_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(contents)
    body = deepcopy(payload)
    body["candidate"]["artifact_path"] = rel_path
    body["candidate"]["artifact_sha256"] = sha256(contents).hexdigest()
    return _resign(body)


def test_distribution_reviewed_sample_passes(tmp_path: Path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _with_dist_artifact(
        _distribution_sample(),
        tmp_path,
        rel_path="dist/backend.exe",
        contents=b"distribution-reviewed-artifact",
    )
    assert distribution.validate_payload(payload, repo_root=tmp_path) == []
    errors, contract = distribution.validate_payload_with_contract(payload, repo_root=tmp_path)
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_package_json_exposes_evidence_checker_scripts() -> None:
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]
    assert scripts["evidence:distribution-verify"] == "python scripts/verify_distribution_release_evidence.py"
    assert scripts["evidence:distribution-seal"] == (
        "python scripts/seal_reviewed_release_evidence.py --kind distribution"
    )
    assert scripts["evidence:clean-machine-verify"] == "python scripts/verify_clean_machine_evidence.py"
    assert scripts["evidence:clean-machine-seal"] == (
        "python scripts/seal_reviewed_release_evidence.py --kind clean-machine"
    )
    assert scripts["evidence:result-quality-verify"] == "python scripts/verify_result_quality_reviewed_evidence.py"
    assert scripts["evidence:result-quality-seal"] == (
        "python scripts/seal_reviewed_release_evidence.py --kind result-quality"
    )
    assert scripts["evidence:diagnostics-verify"] == ("python scripts/verify_diagnostics_external_reviewed_evidence.py")
    assert scripts["evidence:diagnostics-seal"] == (
        "python scripts/seal_reviewed_release_evidence.py --kind diagnostics"
    )
    assert scripts["evidence:support-privacy-verify"] == "python scripts/verify_support_privacy_rehearsal_evidence.py"
    assert scripts["evidence:claims-launch-verify"] == "python scripts/verify_launch_claims_reviewed_evidence.py"
    assert scripts["evidence:commercial-operations-verify"] == (
        "python scripts/verify_commercial_operations_evidence.py"
    )
    assert scripts["evidence:commercial-operations-seal"] == ("python scripts/seal_commercial_operations_evidence.py")
    assert scripts["evidence:paid-launch-template"] == "python scripts/collect_paid_launch_evidence_templates.py"
    assert scripts["evidence:commercial-loop"] == "python scripts/verify_commercial_loop_evidence.py"
    assert scripts["activation:admin"] == "python scripts/activation_admin.py"
    assert scripts["market:readiness:paid"] == (
        "python scripts/check_market_readiness.py --dashboard docs/business/market-readiness.md --paid-launch"
    )
    assert scripts["release:check"] == "npm run delivery:rc"
    assert scripts["release:gate"] == "npm run delivery:rc"
    assert scripts["release:smoke"] == "npm run delivery:rc"
    assert scripts["release:paid-launch"] == "npm run delivery:paid-launch"
    assert scripts["delivery:paid-launch"] == (
        "python scripts/delivery_pipeline.py --paid-launch --output build/delivery-verdict.json"
    )


def test_required_release_evidence_sealer_writes_all_workflow_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, TEST_EVIDENCE_PRIVATE_KEY)
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    for environment_name, field_name in evidence_contracts.CANDIDATE_BINDING_ENVIRONMENT:
        monkeypatch.setenv(environment_name, STRICT_CANDIDATE_BINDING[field_name])

    distribution_payload = _with_dist_artifact(
        _with_strict_candidate_binding(_distribution_sample()),
        tmp_path,
        rel_path="dist/reviewed-distribution.exe",
        contents=b"reviewed-distribution",
    )
    clean_machine_payload = _with_dist_artifact(
        _with_strict_candidate_binding(_clean_machine_sample()),
        tmp_path,
        rel_path="dist/reviewed-portable.zip",
        contents=b"reviewed-clean-machine",
    )
    cases = [
        (
            "distribution",
            distribution_payload,
            "distribution-release-evidence-reviewed.json",
            lambda payload: distribution.validate_payload(
                payload,
                repo_root=tmp_path,
                expected_candidate_binding=_strict_candidate_binding(),
            ),
        ),
        (
            "clean-machine",
            clean_machine_payload,
            "clean-machine-release-evidence-reviewed.json",
            lambda payload: clean_machine.validate_payload(
                payload,
                repo_root=tmp_path,
                expected_candidate_binding=_strict_candidate_binding(),
            ),
        ),
        (
            "result-quality",
            _with_strict_candidate_binding(_result_quality_sample()),
            "result-quality-review-evidence-reviewed.json",
            lambda payload: result_quality.validate_payload(
                payload,
                expected_candidate_binding=_strict_candidate_binding(),
            ),
        ),
        (
            "diagnostics",
            _with_strict_candidate_binding(_diagnostics_reviewed_sample()),
            "diagnostics-external-review-evidence-reviewed.json",
            lambda payload: diagnostics_reviewed.validate_payload(
                payload,
                expected_candidate_binding=_strict_candidate_binding(),
            ),
        ),
    ]

    for kind, signed_sample, output_name, validate in cases:
        draft = deepcopy(signed_sample)
        draft.pop("evidence")
        input_path = tmp_path / f"{kind}.reviewed.draft.json"
        output_path = tmp_path / "sealed" / output_name
        input_path.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        sealed, errors = reviewed_release_seal.write_sealed_evidence(
            kind=kind,
            input_path=input_path,
            output_path=output_path,
            repo_root=tmp_path,
            force=False,
        )

        assert errors == []
        assert sealed is not None
        assert output_path.exists()
        assert validate(json.loads(output_path.read_text(encoding="utf-8"))) == []
        assert list(output_path.parent.glob(f".{output_path.name}.*.tmp")) == []
        assert sealed["evidence"]["signature_payload_version"] == (
            evidence_contracts.EVIDENCE_SIGNATURE_PAYLOAD_VERSION
        )


def test_required_release_evidence_sealer_rejects_templates_and_same_output(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="template"):
        reviewed_release_seal.seal_payload(
            {
                "artifact_type": distribution.ARTIFACT_TYPE,
                "template_status": "human_review_required",
            },
            kind="distribution",
            private_key_text=TEST_EVIDENCE_PRIVATE_KEY,
        )

    draft = tmp_path / "same.json"
    draft.write_text("{}\n", encoding="utf-8")
    sealed, errors = reviewed_release_seal.write_sealed_evidence(
        kind="distribution",
        input_path=draft,
        output_path=draft,
        repo_root=tmp_path,
        force=True,
    )
    assert sealed is None
    assert errors == ["input and output paths must be different"]


def test_required_release_evidence_sealer_does_not_overwrite_a_racing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV,
        TEST_EVIDENCE_PRIVATE_KEY,
    )
    monkeypatch.setenv(
        evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV,
        TEST_EVIDENCE_PUBLIC_KEY,
    )
    for environment_name, field_name in evidence_contracts.CANDIDATE_BINDING_ENVIRONMENT:
        monkeypatch.setenv(environment_name, STRICT_CANDIDATE_BINDING[field_name])
    payload = _with_dist_artifact(
        _with_strict_candidate_binding(_distribution_sample()),
        tmp_path,
        rel_path="dist/race-reviewed-distribution.exe",
        contents=b"race-reviewed-distribution",
    )
    payload.pop("evidence")
    input_path = tmp_path / "distribution.reviewed.draft.json"
    output_path = tmp_path / "sealed" / "distribution-release-evidence-reviewed.json"
    input_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_output = reviewed_release_seal._write_output_atomically

    def _create_racing_output(path: Path, text: str, *, force: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("concurrent-writer-won\n", encoding="utf-8")
        write_output(path, text, force=force)

    monkeypatch.setattr(
        reviewed_release_seal,
        "_write_output_atomically",
        _create_racing_output,
    )

    sealed, errors = reviewed_release_seal.write_sealed_evidence(
        kind="distribution",
        input_path=input_path,
        output_path=output_path,
        repo_root=tmp_path,
        force=False,
    )

    assert sealed is None
    assert errors == [f"output already exists: {output_path}; pass --force to overwrite"]
    assert output_path.read_text(encoding="utf-8") == "concurrent-writer-won\n"
    assert list(output_path.parent.glob(f".{output_path.name}.*.tmp")) == []


def test_required_release_evidence_sealer_rejects_hardlinked_input_and_output(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "distribution.reviewed.draft.json"
    output_path = tmp_path / "distribution-reviewed-output.json"
    input_path.write_text('{"artifact_type":"draft"}\n', encoding="utf-8")
    try:
        os.link(input_path, output_path)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable on this filesystem: {exc}")
    original = input_path.read_bytes()

    sealed, errors = reviewed_release_seal.write_sealed_evidence(
        kind="distribution",
        input_path=input_path,
        output_path=output_path,
        repo_root=tmp_path,
        force=True,
    )

    assert sealed is None
    assert errors == ["input and output paths must be different"]
    assert input_path.read_bytes() == original


def test_distribution_rejects_template_and_missing_required_fields() -> None:
    payload = {"artifact_type": "distribution-release-evidence-template"}
    errors = distribution.validate_payload(payload)
    assert any("artifact_type" in error for error in errors)
    assert any("candidate.commit" in error for error in errors)
    assert any("template evidence" in error for error in errors)


def test_distribution_rejects_unsigned_reviewed_evidence() -> None:
    payload = _distribution_sample()
    payload.pop("evidence")
    errors = distribution.validate_payload(payload)
    assert any("signature block" in error for error in errors)


def test_distribution_rejects_tampered_signature() -> None:
    payload = _distribution_sample()
    payload["candidate"]["build_identifier"] = "tampered"
    errors = distribution.validate_payload(payload)
    assert any("payload_sha256" in error or "signature" in error for error in errors)


def test_distribution_rejects_sensitive_raw_values() -> None:
    payload = _distribution_sample()
    payload["candidate"]["artifact_label"] = "https://192.168.1.4/build.exe?token=secret123"
    errors = distribution.validate_payload(payload)
    assert any("raw IPv4" in error or "token" in error or "raw URL" in error for error in errors)


def test_clean_machine_reviewed_sample_passes_with_local_model_required(tmp_path: Path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _with_dist_artifact(
        _clean_machine_sample(),
        tmp_path,
        rel_path="dist/Lengrvis-win-portable.zip",
        contents=b"clean-machine-reviewed-artifact",
    )
    assert clean_machine.validate_payload(payload, require_local_model=True, repo_root=tmp_path) == []
    errors, contract = clean_machine.validate_payload_with_contract(
        payload,
        require_local_model=True,
        repo_root=tmp_path,
    )
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_clean_machine_requires_local_model_when_claimed() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _clean_machine_sample()
    payload.pop("local_model")
    errors = clean_machine.validate_payload(payload, require_local_model=True)
    assert any("local_model.runtime" in error for error in errors)
    assert any("local_model.privacy_task_smoke" in error for error in errors)


def test_clean_machine_rejects_unsigned_reviewed_evidence() -> None:
    payload = _clean_machine_sample()
    payload.pop("evidence")
    errors = clean_machine.validate_payload(payload)
    assert any("signature block" in error for error in errors)


def test_commercial_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    assert commercial.validate_payload(_commercial_sample()) == []
    errors, contract = commercial.validate_payload_with_contract(_commercial_sample())
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_result_quality_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    assert result_quality.validate_payload(_result_quality_sample()) == []
    errors, contract = result_quality.validate_payload_with_contract(_result_quality_sample())
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_support_privacy_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    assert support_privacy.validate_payload(_support_privacy_sample()) == []
    errors, contract = support_privacy.validate_payload_with_contract(_support_privacy_sample())
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_support_privacy_rejects_missing_rehearsal_and_release_signoff() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _support_privacy_sample()
    payload["release_rehearsal"]["checks"].pop("diagnostic_package_deletion")
    payload["summary"]["release_signoff"] = True
    payload = _resign(payload)
    errors = support_privacy.validate_payload(payload)
    assert any("diagnostic_package_deletion" in error for error in errors)
    assert any("summary.release_signoff must be false" in error for error in errors)


def test_claims_launch_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    assert claims_launch.validate_payload(_claims_launch_sample(), repo_root=REPO_ROOT) == []
    errors, contract = claims_launch.validate_payload_with_contract(
        _claims_launch_sample(),
        repo_root=REPO_ROOT,
    )
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_claims_launch_rejects_missing_claim_review_and_release_signoff() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _claims_launch_sample()
    payload["security_privacy_claims"]["status"] = "blocked"
    payload["summary"]["release_signoff"] = True
    payload = _resign(payload)
    errors = claims_launch.validate_payload(payload, repo_root=REPO_ROOT)
    assert any("security_privacy_claims.status" in error for error in errors)
    assert any("summary.release_signoff must be false" in error for error in errors)


def test_commercial_operations_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    assert commercial_operations.validate_payload(_commercial_operations_sample(), repo_root=REPO_ROOT) == []
    errors, contract = commercial_operations.validate_payload_with_contract(
        _commercial_operations_sample(),
        repo_root=REPO_ROOT,
    )
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
    }


def test_strict_candidate_binding_accepts_the_same_immutable_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    binding = _strict_candidate_binding()
    clean_payload = _with_dist_artifact(
        _with_strict_candidate_binding(_clean_machine_sample()),
        tmp_path,
        rel_path="dist/Lengrvis-win-portable.zip",
        contents=b"strict-candidate-clean-machine-artifact",
    )
    result_payload = _with_strict_candidate_binding(_result_quality_sample())
    diagnostics_payload = _with_strict_candidate_binding(_diagnostics_reviewed_sample())

    assert (
        clean_machine.validate_payload(
            clean_payload,
            require_local_model=True,
            repo_root=tmp_path,
            expected_candidate_binding=binding,
        )
        == []
    )
    assert (
        result_quality.validate_payload(
            result_payload,
            expected_candidate_binding=binding,
        )
        == []
    )
    assert (
        diagnostics_reviewed.validate_payload(
            diagnostics_payload,
            expected_candidate_binding=binding,
        )
        == []
    )


def test_distribution_evidence_rejects_replay_from_another_candidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _with_dist_artifact(
        _with_strict_candidate_binding(_distribution_sample()),
        tmp_path,
        rel_path="dist/installer.exe",
        contents=b"candidate-bound-distribution-artifact",
    )
    payload["candidate"]["commit"] = "b" * 40
    payload = _resign(payload)

    errors = distribution.validate_payload(
        payload,
        repo_root=tmp_path,
        expected_candidate_binding=_strict_candidate_binding(),
    )

    assert "candidate_commit_mismatch" in errors


@pytest.mark.parametrize(
    ("verifier", "sample_factory"),
    [
        (commercial, _commercial_sample),
        (support_privacy, _support_privacy_sample),
        (claims_launch, _claims_launch_sample),
        (commercial_operations, _commercial_operations_sample),
    ],
)
def test_paid_launch_evidence_rejects_replay_from_another_candidate(verifier, sample_factory, monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    sample = sample_factory()
    sample.setdefault("candidate", {})
    payload = _with_strict_candidate_binding(sample)
    payload["candidate"]["commit"] = "b" * 40
    payload = _resign(payload)

    errors = verifier.validate_payload(
        payload,
        expected_candidate_binding=_strict_candidate_binding(),
    )

    assert "candidate_commit_mismatch" in errors


@pytest.mark.parametrize(
    ("field", "replacement", "error_code"),
    [
        ("commit", "b" * 40, "candidate_commit_mismatch"),
        ("build_identifier", f"rc-12345-2-{'b' * 40}", "candidate_build_identifier_mismatch"),
        ("repository", "other/repository", "candidate_repository_mismatch"),
        ("ci_run_id", "54321", "candidate_ci_run_id_mismatch"),
        ("ci_run_attempt", "3", "candidate_ci_run_attempt_mismatch"),
    ],
)
def test_strict_candidate_binding_rejects_replayed_reviewed_evidence(
    monkeypatch,
    field: str,
    replacement: str,
    error_code: str,
) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _with_strict_candidate_binding(_result_quality_sample())
    payload["candidate"][field] = replacement
    payload = _resign(payload)

    errors = result_quality.validate_payload(
        payload,
        expected_candidate_binding=_strict_candidate_binding(),
    )

    assert error_code in errors


def test_strict_candidate_binding_is_covered_by_the_evidence_signature(monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    payload = _with_strict_candidate_binding(_diagnostics_reviewed_sample())
    payload["candidate"]["commit"] = "b" * 40

    errors = diagnostics_reviewed.validate_payload(
        payload,
        expected_candidate_binding=_strict_candidate_binding(),
    )

    assert any("payload_sha256" in error or "signature" in error for error in errors)


def test_strict_candidate_binding_environment_fails_closed_when_context_is_missing(monkeypatch) -> None:
    for variable in (
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT",
        "LENGRVIS_RELEASE_BUILD_IDENTIFIER",
        "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT",
    ):
        monkeypatch.delenv(variable, raising=False)

    binding, errors = evidence_contracts.candidate_binding_from_environment()

    assert binding is None
    assert "LENGRVIS_RELEASE_CANDIDATE_COMMIT is required for strict candidate binding" in errors


@pytest.mark.parametrize(
    ("environment_key", "replacement", "expected_fragment"),
    [
        ("LENGRVIS_RELEASE_CANDIDATE_COMMIT", "abc123", "commit must be a lowercase 40-character Git SHA"),
        ("LENGRVIS_RELEASE_BUILD_IDENTIFIER", "local/manual", "build_identifier must equal"),
        ("LENGRVIS_RELEASE_CANDIDATE_REPOSITORY", "not-a-repository", "repository must be an owner/repository"),
        ("LENGRVIS_RELEASE_CANDIDATE_RUN_ID", "0", "ci_run_id must be a positive integer"),
        ("LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT", "zero", "ci_run_attempt must be a positive integer"),
    ],
)
def test_strict_candidate_binding_environment_rejects_non_immutable_context(
    environment_key: str,
    replacement: str,
    expected_fragment: str,
) -> None:
    environment = {
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT": STRICT_CANDIDATE_BINDING["commit"],
        "LENGRVIS_RELEASE_BUILD_IDENTIFIER": STRICT_CANDIDATE_BINDING["build_identifier"],
        "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY": STRICT_CANDIDATE_BINDING["repository"],
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID": STRICT_CANDIDATE_BINDING["ci_run_id"],
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT": STRICT_CANDIDATE_BINDING["ci_run_attempt"],
    }
    environment[environment_key] = replacement

    binding, errors = evidence_contracts.candidate_binding_from_environment(environment)

    assert binding is None
    assert any(expected_fragment in error for error in errors)


def test_strict_candidate_binding_rejects_a_checkout_for_a_different_commit() -> None:
    checkout_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    matching = evidence_contracts.CandidateBinding(
        commit=checkout_commit,
        build_identifier=f"rc-7-1-{checkout_commit}",
        repository="lengrvis/mavris",
        ci_run_id="7",
        ci_run_attempt="1",
    )
    mismatched = evidence_contracts.CandidateBinding(
        commit="a" * 40 if checkout_commit != "a" * 40 else "b" * 40,
        build_identifier=f"rc-7-1-{'a' * 40 if checkout_commit != 'a' * 40 else 'b' * 40}",
        repository="lengrvis/mavris",
        ci_run_id="7",
        ci_run_attempt="1",
    )

    assert candidate_binding_check.validate_checkout_commit(matching, repo_root=REPO_ROOT) == []
    assert candidate_binding_check.validate_checkout_commit(mismatched, repo_root=REPO_ROOT) == [
        "checkout_commit_mismatch"
    ]


def test_commercial_operations_rejects_missing_tax_refund_and_release_signoff() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _commercial_operations_sample()
    payload["tax"]["product_taxability_review_label"] = ""
    payload["refunds"]["status"] = "blocked"
    payload["summary"]["release_signoff"] = True
    payload = _resign(payload)

    errors = commercial_operations.validate_payload(payload, repo_root=REPO_ROOT)

    assert any("tax.product_taxability_review_label" in error for error in errors)
    assert any("refunds.status" in error for error in errors)
    assert any("summary.release_signoff must be false" in error for error in errors)


def test_commercial_operations_seal_writes_verifiable_reviewed_evidence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV, TEST_EVIDENCE_PUBLIC_KEY)
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, TEST_EVIDENCE_PRIVATE_KEY)
    draft = deepcopy(_commercial_operations_sample())
    draft.pop("evidence")
    input_path = tmp_path / "commercial-operations.reviewed.draft.json"
    output_path = tmp_path / "build" / "commercial-operations-evidence-reviewed.json"
    input_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sealed, errors = commercial_operations_seal.write_sealed_evidence(
        input_path=input_path,
        output_path=output_path,
        repo_root=REPO_ROOT,
        force=False,
    )

    assert errors == []
    assert sealed is not None
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    expected_fingerprint = evidence_contracts.evidence_public_key_fingerprint(
        evidence_contracts.load_evidence_public_key(TEST_EVIDENCE_PUBLIC_KEY)
    )
    assert payload["evidence"]["signing_key_fingerprint"] == expected_fingerprint
    assert payload["evidence"]["signature_payload_version"] == (evidence_contracts.EVIDENCE_SIGNATURE_PAYLOAD_VERSION)
    assert commercial_operations.validate_payload(payload, repo_root=REPO_ROOT) == []


def test_commercial_operations_sealer_does_not_overwrite_a_racing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV,
        TEST_EVIDENCE_PRIVATE_KEY,
    )
    draft = deepcopy(_commercial_operations_sample())
    draft.pop("evidence")
    input_path = tmp_path / "commercial-operations.reviewed.draft.json"
    output_path = tmp_path / "build" / "commercial-operations-evidence-reviewed.json"
    input_path.write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_output = commercial_operations_seal._write_output_atomically

    def _create_racing_output(path: Path, text: str, *, force: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("concurrent-writer-won\n", encoding="utf-8")
        write_output(path, text, force=force)

    monkeypatch.setattr(
        commercial_operations_seal,
        "_write_output_atomically",
        _create_racing_output,
    )

    sealed, errors = commercial_operations_seal.write_sealed_evidence(
        input_path=input_path,
        output_path=output_path,
        repo_root=REPO_ROOT,
        force=False,
    )

    assert sealed is None
    assert errors == [f"output already exists: {output_path}; pass --force to overwrite"]
    assert output_path.read_text(encoding="utf-8") == "concurrent-writer-won\n"
    assert list(output_path.parent.glob(f".{output_path.name}.*.tmp")) == []

    forced, force_errors = commercial_operations_seal.write_sealed_evidence(
        input_path=input_path,
        output_path=output_path,
        repo_root=REPO_ROOT,
        force=True,
    )

    assert force_errors == []
    assert forced is not None
    assert json.loads(output_path.read_text(encoding="utf-8")) == forced
    assert list(output_path.parent.glob(f".{output_path.name}.*.tmp")) == []


def test_commercial_operations_sealer_rejects_hardlinked_input_and_output(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "commercial-operations.reviewed.draft.json"
    output_path = tmp_path / "commercial-operations-reviewed-output.json"
    input_path.write_text('{"artifact_type":"draft"}\n', encoding="utf-8")
    try:
        os.link(input_path, output_path)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable on this filesystem: {exc}")
    original = input_path.read_bytes()

    sealed, errors = commercial_operations_seal.write_sealed_evidence(
        input_path=input_path,
        output_path=output_path,
        repo_root=REPO_ROOT,
        force=True,
    )

    assert sealed is None
    assert errors == ["input and output paths must be different"]
    assert input_path.read_bytes() == original


def test_commercial_operations_seal_rejects_templates_and_invalid_private_keys() -> None:
    template = paid_launch_templates.build_commercial_operations_template(
        candidate_commit="abc123",
        build_identifier="ci-123",
    )
    valid_draft = deepcopy(_commercial_operations_sample())
    valid_draft.pop("evidence")

    with pytest.raises(ValueError, match="invalid Ed25519 length"):
        commercial_operations_seal.seal_payload(
            valid_draft,
            private_key_text="ed25519:YQ",
        )

    with pytest.raises(ValueError, match="artifact_type|template"):
        commercial_operations_seal.seal_payload(
            template,
            private_key_text=TEST_EVIDENCE_PRIVATE_KEY,
        )


def test_paid_launch_templates_are_actionable_but_not_reviewed_evidence(tmp_path) -> None:
    paths = paid_launch_templates.write_templates(
        tmp_path,
        candidate_commit=STRICT_CANDIDATE_BINDING["commit"],
        build_identifier=STRICT_CANDIDATE_BINDING["build_identifier"],
        candidate_repository=STRICT_CANDIDATE_BINDING["repository"],
        candidate_run_id=STRICT_CANDIDATE_BINDING["ci_run_id"],
        candidate_run_attempt=STRICT_CANDIDATE_BINDING["ci_run_attempt"],
    )
    support_payload = json.loads(Path(paths["support_privacy_template"]).read_text(encoding="utf-8"))
    claims_payload = json.loads(Path(paths["claims_launch_template"]).read_text(encoding="utf-8"))
    commercial_loop_payload = json.loads(Path(paths["commercial_loop_template"]).read_text(encoding="utf-8"))
    operations_payload = json.loads(Path(paths["commercial_operations_template"]).read_text(encoding="utf-8"))

    for payload in (support_payload, claims_payload, commercial_loop_payload, operations_payload):
        assert payload["candidate"] == STRICT_CANDIDATE_BINDING

    assert support_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert claims_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert commercial_loop_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert operations_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert any("paid-launch pass" in item for item in support_payload["must_not_be_recorded_as"])
    assert any("paid-launch pass" in item for item in claims_payload["must_not_be_recorded_as"])
    assert any("paid-launch pass" in item for item in commercial_loop_payload["must_not_be_recorded_as"])
    assert any("paid-launch pass" in item for item in operations_payload["must_not_be_recorded_as"])
    assert any("artifact_type" in error for error in support_privacy.validate_payload(support_payload))
    assert any(
        "artifact_type" in error for error in claims_launch.validate_payload(claims_payload, repo_root=REPO_ROOT)
    )
    assert any("artifact_type" in error for error in commercial.validate_payload(commercial_loop_payload))
    assert any(
        "artifact_type" in error
        for error in commercial_operations.validate_payload(operations_payload, repo_root=REPO_ROOT)
    )


def test_result_quality_rejects_template_short_run_and_safety_false_negative() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _result_quality_sample()
    payload["artifact_type"] = "result-quality-review-evidence-template"
    payload["tasks"] = payload["tasks"][:3]
    payload["summary"]["reviewed_task_count"] = 3
    payload["summary"]["success_rate"] = 0.5
    payload["summary"]["safety_false_negative_count"] = 1
    payload["tasks"][0]["safety_status"] = "false_negative"
    payload = _resign(payload)
    errors = result_quality.validate_payload(payload)
    assert any("template evidence" in error for error in errors)
    assert any("at least 30" in error for error in errors)
    assert any("false_negative" in error or "false negatives" in error for error in errors)


def test_diagnostics_reviewed_sample_passes() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    assert diagnostics_reviewed.validate_payload(payload) == []
    errors, contract = diagnostics_reviewed.validate_payload_with_contract(payload)
    assert errors == []
    assert contract == {
        "valid_hash": True,
        "valid_signature": True,
        "reviewed_pass": True,
        "release_signoff": False,
        "actual_package_content_review_completed": True,
        "public_safe": False,
        "external_sharing_allowed": False,
    }


def test_diagnostics_reviewed_rejects_template_and_unsafe_external_share() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    payload["artifact_type"] = "diagnostics-external-review-evidence-template"
    payload["review"]["decision"] = "public_safe"
    payload["summary"]["public_safe"] = True
    payload["summary"]["external_sharing_allowed"] = True
    payload = _resign(payload)

    errors = diagnostics_reviewed.validate_payload(payload)

    assert any("template evidence" in error for error in errors)
    assert any("summary.public_safe must be false" in error for error in errors)
    assert any("summary.external_sharing_allowed must be false" in error for error in errors)
    assert any("review.decision must be one of" in error for error in errors)


def test_diagnostics_reviewed_rejects_raw_package_path_and_ambiguous_time() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    payload["candidate"]["diagnostics_package_label"] = r"D:\Desktop\mavris\.lengrvis_data\diagnostic-packages\case.zip"
    payload["review"]["reviewed_at_utc"] = "2026-06-27T12:00:00"
    payload = _resign(payload)

    errors = diagnostics_reviewed.validate_payload(payload)

    assert any("diagnostics_package_label must be a redacted label" in error for error in errors)
    assert any("review.reviewed_at_utc must include an explicit UTC timezone" in error for error in errors)


def test_diagnostics_reviewed_rejects_non_string_identity_and_label_fields() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    payload["candidate"]["commit"] = 123
    payload["candidate"]["build_identifier"] = ["ci-123"]
    payload["candidate"]["diagnostics_package_label"] = {"label": r"C:\ProgramData\Lengrvis\diagnostics\case.zip"}
    payload["review"]["reviewer_label"] = {"reviewer": "alice"}
    payload = _resign(payload)

    errors = diagnostics_reviewed.validate_payload(payload)

    assert any("candidate.commit must be a non-empty string" in error for error in errors)
    assert any("candidate.build_identifier must be a non-empty string" in error for error in errors)
    assert any("candidate.diagnostics_package_label must be a non-empty string" in error for error in errors)
    assert any("review.reviewer_label must be a non-empty string" in error for error in errors)


def test_diagnostics_reviewed_rejects_common_path_spellings_without_rejecting_label_text() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    for raw_path in (
        "C:/ProgramData/Lengrvis/diagnostics/case.zip",
        "/opt/lengrvis/diagnostics/case.zip",
        r"\\server\share\case.zip",
        "~/.lengrvis_data/diagnostic-packages/case.zip",
    ):
        payload = _diagnostics_reviewed_sample()
        payload["candidate"]["diagnostics_package_label"] = raw_path
        payload = _resign(payload)

        errors = diagnostics_reviewed.validate_payload(payload)

        assert any("diagnostics_package_label must be a redacted label" in error for error in errors)

    payload = _diagnostics_reviewed_sample()
    payload["candidate"]["diagnostics_package_label"] = "diagnostic-packages-redacted-label"
    payload = _resign(payload)
    assert diagnostics_reviewed.validate_payload(payload) == []


def test_diagnostics_reviewed_rejects_support_only_public_safe_mismatch() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    payload["summary"]["public_safe"] = True
    payload = _resign(payload)

    errors = diagnostics_reviewed.validate_payload(payload)

    assert any("summary.public_safe must be false" in error for error in errors)


def test_diagnostics_reviewed_rejects_missing_actual_package_review() -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _diagnostics_reviewed_sample()
    payload["checks"]["task_traces_reviewed"] = "pending"
    payload["review"]["decision"] = "share_anyway"
    payload = _resign(payload)

    errors = diagnostics_reviewed.validate_payload(payload)

    assert any("checks.task_traces_reviewed must be passed" in error for error in errors)
    assert any("review.decision must be one of" in error for error in errors)


def test_commercial_rejects_development_license_profile() -> None:
    payload = deepcopy(_commercial_sample())
    payload["license_issuer"]["key_profile"] = "development"
    errors = commercial.validate_payload(payload)
    assert any("key_profile" in error for error in errors)


def test_commercial_rejects_self_serve_checkout_claim() -> None:
    payload = deepcopy(_commercial_sample())
    payload["summary"]["self_serve_checkout_enabled"] = True
    errors = commercial.validate_payload(payload)
    assert any("self_serve_checkout_enabled" in error for error in errors)


def test_commercial_rejects_missing_activation_security_evidence() -> None:
    payload = deepcopy(_commercial_sample())
    payload["subscription_activation"].pop("strong_device_binding_label")
    payload["subscription_activation"].pop("reverse_proxy_label")
    payload["subscription_activation"].pop("activation_audit_log_label")
    payload["license_issuer"].pop("revocation_manifest_freshness_label")

    errors = commercial.validate_payload(payload)

    assert any("strong_device_binding_label" in error for error in errors)
    assert any("reverse_proxy_label" in error for error in errors)
    assert any("activation_audit_log_label" in error for error in errors)
    assert any("revocation_manifest_freshness_label" in error for error in errors)


def test_distribution_cross_checks_dist_artifact_sha256_when_path_present(tmp_path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    artifact = tmp_path / "dist" / "backend.exe"
    artifact.parent.mkdir(parents=True)
    artifact_bytes = b"dist-artifact-bytes"
    artifact.write_bytes(artifact_bytes)
    actual_sha = sha256(artifact_bytes).hexdigest()

    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "dist/backend.exe"
    payload["candidate"]["artifact_sha256"] = actual_sha
    payload = _resign(payload)
    assert distribution.validate_payload(payload, repo_root=tmp_path) == []

    payload["candidate"]["artifact_sha256"] = "f" * 64
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("does not match SHA256" in error for error in errors)


def test_distribution_rejects_cross_check_when_dist_file_missing(tmp_path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "dist/missing-artifact.exe"
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("must point to an existing on-disk artifact" in error for error in errors)


def test_distribution_requires_artifact_path_for_cross_check(tmp_path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    errors = distribution.validate_payload(_distribution_sample(), repo_root=tmp_path)
    assert any("candidate.artifact_path is required" in error for error in errors)


def test_distribution_verifier_requires_public_key_even_if_private_key_is_present(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV)
    monkeypatch.setenv(evidence_contracts.EVIDENCE_PRIVATE_KEY_ENV, TEST_EVIDENCE_PRIVATE_KEY)
    payload = _with_dist_artifact(
        _distribution_sample(),
        tmp_path,
        rel_path="dist/backend.exe",
        contents=b"public-key-required-artifact",
    )
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any(evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV in error for error in errors)


def test_distribution_rejects_artifact_path_outside_dist(tmp_path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "build/evidence.json"
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("must be a repo-relative path under dist/" in error for error in errors)


def test_clean_machine_cross_checks_dist_artifact_sha256_when_path_present(tmp_path) -> None:
    import os

    os.environ[evidence_contracts.EVIDENCE_PUBLIC_KEY_ENV] = TEST_EVIDENCE_PUBLIC_KEY
    artifact = tmp_path / "dist" / "Lengrvis-win-portable.zip"
    artifact.parent.mkdir(parents=True)
    artifact_bytes = b"portable-zip-bytes"
    artifact.write_bytes(artifact_bytes)
    actual_sha = sha256(artifact_bytes).hexdigest()

    payload = _resign(_clean_machine_sample())
    payload["candidate"]["artifact_path"] = "dist/Lengrvis-win-portable.zip"
    payload["candidate"]["artifact_sha256"] = actual_sha
    payload = _resign(payload)
    assert clean_machine.validate_payload(payload, repo_root=tmp_path) == []

    payload["candidate"]["artifact_sha256"] = "e" * 64
    payload = _resign(payload)
    errors = clean_machine.validate_payload(payload, repo_root=tmp_path)
    assert any("does not match SHA256" in error for error in errors)
