from __future__ import annotations

import hmac
import importlib.util
import json
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

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
support_privacy = _load_script("verify_support_privacy_rehearsal_evidence.py")
claims_launch = _load_script("verify_launch_claims_reviewed_evidence.py")
paid_launch_templates = _load_script("collect_paid_launch_evidence_templates.py")
evidence_contracts = _load_script("evidence_contracts.py")
TEST_EVIDENCE_SECRET = "test-release-evidence-secret"  # noqa: S105 - deterministic test signing key.


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
            "pilot": {"scope": "subscription_activation_free_pro_max"},
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


def _signed(payload: dict) -> dict:
    body = deepcopy(payload)
    body["evidence"] = {
        "payload_sha256": "",
        "signature": "",
        "signing_key_fingerprint": sha256(TEST_EVIDENCE_SECRET.encode()).hexdigest()[:16],
    }
    payload_hash = evidence_contracts.canonical_evidence_payload_hash(body)
    body["evidence"]["payload_sha256"] = payload_hash
    body["evidence"]["signature"] = hmac.new(TEST_EVIDENCE_SECRET.encode(), payload_hash.encode(), sha256).hexdigest()
    return body


def _resign(payload: dict) -> dict:
    body = deepcopy(payload)
    evidence = body.setdefault("evidence", {})
    evidence["signing_key_fingerprint"] = sha256(TEST_EVIDENCE_SECRET.encode()).hexdigest()[:16]
    payload_hash = evidence_contracts.canonical_evidence_payload_hash(body)
    evidence["payload_sha256"] = payload_hash
    evidence["signature"] = hmac.new(TEST_EVIDENCE_SECRET.encode(), payload_hash.encode(), sha256).hexdigest()
    return body


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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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
    assert scripts["evidence:clean-machine-verify"] == "python scripts/verify_clean_machine_evidence.py"
    assert scripts["evidence:result-quality-verify"] == "python scripts/verify_result_quality_reviewed_evidence.py"
    assert scripts["evidence:support-privacy-verify"] == "python scripts/verify_support_privacy_rehearsal_evidence.py"
    assert scripts["evidence:claims-launch-verify"] == "python scripts/verify_launch_claims_reviewed_evidence.py"
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    payload = _support_privacy_sample()
    payload["release_rehearsal"]["checks"].pop("diagnostic_package_deletion")
    payload["summary"]["release_signoff"] = True
    payload = _resign(payload)
    errors = support_privacy.validate_payload(payload)
    assert any("diagnostic_package_deletion" in error for error in errors)
    assert any("summary.release_signoff must be false" in error for error in errors)


def test_claims_launch_reviewed_sample_passes() -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    payload = _claims_launch_sample()
    payload["security_privacy_claims"]["status"] = "blocked"
    payload["summary"]["release_signoff"] = True
    payload = _resign(payload)
    errors = claims_launch.validate_payload(payload, repo_root=REPO_ROOT)
    assert any("security_privacy_claims.status" in error for error in errors)
    assert any("summary.release_signoff must be false" in error for error in errors)


def test_paid_launch_templates_are_actionable_but_not_reviewed_evidence(tmp_path) -> None:
    paths = paid_launch_templates.write_templates(
        tmp_path,
        candidate_commit="abc123",
        build_identifier="ci-123",
    )
    support_payload = json.loads(Path(paths["support_privacy_template"]).read_text(encoding="utf-8"))
    claims_payload = json.loads(Path(paths["claims_launch_template"]).read_text(encoding="utf-8"))

    assert support_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert claims_payload["claim_controls"]["paid_launch_claim_allowed"] is False
    assert any("paid-launch pass" in item for item in support_payload["must_not_be_recorded_as"])
    assert any("paid-launch pass" in item for item in claims_payload["must_not_be_recorded_as"])
    assert any("artifact_type" in error for error in support_privacy.validate_payload(support_payload))
    assert any("artifact_type" in error for error in claims_launch.validate_payload(claims_payload, repo_root=REPO_ROOT))


def test_result_quality_rejects_template_short_run_and_safety_false_negative() -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "dist/missing-artifact.exe"
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("must point to an existing on-disk artifact" in error for error in errors)


def test_distribution_requires_artifact_path_for_cross_check(tmp_path) -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    errors = distribution.validate_payload(_distribution_sample(), repo_root=tmp_path)
    assert any("candidate.artifact_path is required" in error for error in errors)


def test_distribution_rejects_known_unsafe_evidence_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET", "ci-release-evidence-hmac-secret")
    payload = _with_dist_artifact(
        _distribution_sample(),
        tmp_path,
        rel_path="dist/backend.exe",
        contents=b"unsafe-secret-artifact",
    )
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("known unsafe development/CI value" in error for error in errors)


def test_distribution_rejects_artifact_path_outside_dist(tmp_path) -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "build/evidence.json"
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert any("must be a repo-relative path under dist/" in error for error in errors)


def test_clean_machine_cross_checks_dist_artifact_sha256_when_path_present(tmp_path) -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
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
