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
                "activation_key_creation_label": "activation-key-created-redacted",
                "first_activation_label": "first-activation-redacted",
                "idempotent_repeat_activation_label": "repeat-activation-redacted",
                "device_limit_label": "device-limit-redacted",
                "renewal_refresh_label": "renewal-refresh-redacted",
                "cancel_period_end_label": "cancel-period-end-redacted",
                "refund_revocation_label": "refund-revocation-redacted",
                "expired_downgrade_label": "expired-downgrade-redacted",
                "rate_limit_label": "rate-limit-redacted",
                "secret_redaction_label": "secret-redaction-redacted",
            },
            "license_issuer": {
                "status": "passed",
                "key_profile": "production",
                "public_key_fingerprint_label": "fingerprint-redacted",
                "private_key_custody_label": "custody-redacted",
                "issuance_log_label": "issuance-log-redacted",
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


def test_distribution_reviewed_sample_passes() -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    assert distribution.validate_payload(_distribution_sample()) == []
    errors, contract = distribution.validate_payload_with_contract(_distribution_sample())
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
    assert scripts["evidence:commercial-loop"] == "python scripts/verify_commercial_loop_evidence.py"
    assert scripts["activation:admin"] == "python scripts/activation_admin.py"
    assert scripts["market:readiness:paid"] == (
        "python scripts/check_market_readiness.py --dashboard docs/business/market-readiness.md --paid-launch"
    )
    assert "npm run evidence:commercial-loop" in scripts["release:check"]
    assert "npm run audit:deps" in scripts["release:check"]
    assert "npm run security:secrets" in scripts["release:check"]
    assert scripts["release:check"].index("npm run supply-chain:verify") < scripts["release:check"].index(
        "npm run audit:deps"
    )
    assert scripts["release:check"].index("npm run audit:deps") < scripts["release:check"].index(
        "npm run security:secrets"
    )
    assert scripts["release:check"].index("npm run security:secrets") < scripts["release:check"].index(
        "npm run security:extensions"
    )
    assert scripts["release:check"].index("npm run evidence:commercial-loop") < scripts["release:check"].index(
        "npm run market:readiness:strict"
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


def test_clean_machine_reviewed_sample_passes_with_local_model_required() -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    assert clean_machine.validate_payload(_clean_machine_sample(), require_local_model=True) == []
    errors, contract = clean_machine.validate_payload_with_contract(
        _clean_machine_sample(),
        require_local_model=True,
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


def test_distribution_skips_cross_check_when_dist_file_missing(tmp_path) -> None:
    import os

    os.environ["LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET"] = TEST_EVIDENCE_SECRET
    payload = _resign(_distribution_sample())
    payload["candidate"]["artifact_path"] = "dist/missing-artifact.exe"
    payload = _resign(payload)
    errors = distribution.validate_payload(payload, repo_root=tmp_path)
    assert not any("does not match SHA256" in error for error in errors)


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
