from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tls_test_material import write_lan_tls_material


def _start_app_text(project_root: Path) -> str:
    return (project_root / "scripts" / "start_app.ps1").read_text(encoding="utf-8")


def _setup_dev_text(project_root: Path) -> str:
    return (project_root / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")


def _portable_first_screen_smoke_text(project_root: Path) -> str:
    return (project_root / "scripts" / "portable_first_screen_smoke.ps1").read_text(encoding="utf-8")


def _mobile_lan_wss_preflight_text(project_root: Path) -> str:
    return (project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1").read_text(encoding="utf-8")


def _android_release_gate_text(project_root: Path) -> str:
    return (project_root / "scripts" / "verify_android_release_gate.ps1").read_text(encoding="utf-8")


def _release_evidence_packet_text(project_root: Path) -> str:
    return (project_root / "scripts" / "collect_release_evidence_packet.ps1").read_text(encoding="utf-8")


def _current_release_evidence_script_text(project_root: Path) -> str:
    return (
        project_root / "scripts" / "generate_current_release_evidence.ps1"
    ).read_text(encoding="utf-8")


def _current_release_evidence_doc_text(project_root: Path) -> str:
    return (
        project_root / "docs" / "release" / "current-release-evidence.md"
    ).read_text(encoding="utf-8")


def _ci_workflow_text(project_root: Path) -> str:
    return (project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _diagnostics_external_review_packet_text(project_root: Path) -> str:
    return (
        project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"
    ).read_text(encoding="utf-8")


def _local_model_clean_machine_evidence_template_text(project_root: Path) -> str:
    return (
        project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1"
    ).read_text(encoding="utf-8")


def _readme_text(project_root: Path) -> str:
    return (project_root / "README.md").read_text(encoding="utf-8")


def _release_gate_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "release-gate.md").read_text(encoding="utf-8")


def _package_json(project_root: Path) -> dict[str, object]:
    return json.loads((project_root / "package.json").read_text(encoding="utf-8"))


def _powershell_executable() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for release helper contract tests")
    return executable


def _run_release_safety(
    project_root: Path,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "LENGRVIS_ALLOW_MOCK_FALLBACK",
        "LENGRVIS_CONFIG_FILE",
        "LENGRVIS_ENV_FILE",
        "LENGRVIS_STRICT_STATE_MACHINE",
    ):
        env.pop(key, None)
    missing_config_root = tmp_path / "missing-runtime-config"
    env["LENGRVIS_CONFIG_FILE"] = str(missing_config_root / "config.yaml")
    env["LENGRVIS_ENV_FILE"] = str(missing_config_root / ".env")
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_release_safety.ps1"),
            "-Root",
            str(project_root),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _real_device_mobile_matrix_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "real-device-mobile-matrix.md").read_text(encoding="utf-8")


def _e2e_acceptance_matrix_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "e2e-acceptance-matrix.md").read_text(encoding="utf-8")


def _productization_issues_text(project_root: Path) -> str:
    return (project_root / "PRODUCTIZATION_ISSUES.md").read_text(encoding="utf-8")


def _agentic_product_evals_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "agentic-product-evals.md").read_text(encoding="utf-8")


def _parity_text(project_root: Path) -> str:
    return (project_root / "docs" / "LENGRVIS_PARITY.md").read_text(encoding="utf-8")


def _write_settings_local_model_smoke_artifacts(qa_root: Path) -> None:
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")


def _mobile_lan_wss_preflight_summary() -> dict:
    return {
        "result": "ready_for_manual_real_device_collection_only",
        "generated_at_utc": "2026-06-08T00:00:00.0000000Z",
        "real_device_evidence_status": "uncollected_fail_closed",
        "real_device_evidence_collected": False,
        "no_phone_preflight_claim": "not_real_device_pass",
        "backend": {
            "host_redacted": "[redacted-host]",
            "public_base_url_redacted": "https://[redacted-host]:9443",
            "websocket_approvals_url_redacted": "wss://[redacted-host]:9443/ws/mobile/approvals",
            "websocket_remote_screen_url_redacted": "wss://[redacted-host]:9443/ws/remote/screen",
            "websocket_remote_input_url_redacted": "wss://[redacted-host]:9443/ws/remote/input",
        },
        "lan_tls": {
            "enabled": True,
            "tls_material_valid": True,
            "tls_host_valid": True,
        },
        "qr_payload_shape": {
            "transport_security_status": "https_ready_preflight",
            "transport_security_tls_ready": True,
            "websocket_approvals_url_redacted": "wss://[redacted-host]:9443/ws/mobile/approvals",
            "websocket_remote_screen_url_redacted": "wss://[redacted-host]:9443/ws/remote/screen",
            "websocket_remote_input_url_redacted": "wss://[redacted-host]:9443/ws/remote/input",
        },
        "manual_real_device_evidence_template": {
            "template_status": "manual_real_device_evidence_required",
            "real_device_result": "uncollected",
            "real_device_evidence_status": "uncollected_fail_closed",
            "real_device_evidence_collected": False,
            "no_phone_preflight_claim": "not_real_device_pass",
            "claim_controls": {
                "real_device_pass_claim_allowed": False,
                "preflight_ready_is_pass": False,
            },
            "may_be_recorded_as": "preflight/config evidence only",
            "must_not_be_recorded_as": "real-device pass evidence",
            "artifact_collection_rules": {
                "review_required_before_pass_claim": True,
            },
            "fields": {
                "camera_qr_path_evidence": "uncollected",
                "actual_device_https_wss_evidence": "uncollected",
                "approval_wss_evidence": "uncollected",
                "approval_artifact_review": "uncollected",
                "remote_screen_wss_evidence": "uncollected",
                "remote_screen_artifact_review": "uncollected",
                "remote_input_wss_evidence": "uncollected",
                "remote_input_artifact_review": "uncollected",
                "certificate_trust_evidence": "uncollected",
                "remote_input_grant_revoke_evidence": "uncollected",
                "remote_input_grant_expiry_evidence": "uncollected",
                "grant_revoke_expiry_artifact_review": "uncollected",
                "artifact_redaction_review": "uncollected",
            },
            "real_device_collection_checklist": {
                name: {
                    "status": "uncollected",
                    "overclaim_guard": "preflight/config evidence only",
                    "reviewer_check": "review redacted artifacts before pass claim",
                }
                for name in (
                    "camera_qr",
                    "actual_https_wss",
                    "approval_wss",
                    "remote_screen_wss",
                    "remote_input_wss",
                    "certificate_trust",
                    "remote_input_grant_revoke_expiry",
                    "screenshot_log_review",
                )
            },
        },
        "issues": [],
        "warnings": [],
    }


def _android_release_gate_summary(
    *,
    status: str = "preflight_ready_not_release",
    release_ready: bool = False,
    preflight_only: bool = True,
    installable_claim_allowed: bool = False,
    remote_claim_allowed: bool = False,
    artifact_provided: bool = False,
    artifact_label: str = "",
    artifact_bytes: int = 0,
    installable_apk: bool = False,
    apk_zip_header_valid: bool = False,
    artifact_gate_evaluated: bool = False,
    artifact_gate_passed: bool = False,
    real_device_gate_evaluated: bool = False,
    real_device_gate_passed: bool = False,
    real_device_evidence_label: str = "",
    source_config_passed: bool = True,
    must_not_claim: list[str] | None = None,
) -> dict:
    if must_not_claim is None:
        must_not_claim = [
            "installable Android app release pass",
            "real-device Android remote-control pass",
            "LAN HTTPS/WSS mobile pass",
            "release-candidate mobile signoff",
        ]

    return {
        "artifact_type": "android-release-gate-summary",
        "generated_by": "scripts/verify_android_release_gate.ps1",
        "generated_at_utc": "2026-06-09T00:00:00.0000000Z",
        "status": status,
        "release_ready": release_ready,
        "preflight_only": preflight_only,
        "source_config": {
            "passed": source_config_passed,
            "issues": [],
        },
        "android_artifact": {
            "provided": artifact_provided,
            "label": artifact_label,
            "sha256": "0" * 64 if artifact_provided else "",
            "bytes": artifact_bytes,
            "installable_apk": installable_apk,
            "apk_zip_header_valid": apk_zip_header_valid,
        },
        "artifact_gate": {
            "evaluated": artifact_gate_evaluated,
            "passed": artifact_gate_passed,
            "issues": [],
        },
        "real_device_gate": {
            "evaluated": real_device_gate_evaluated,
            "passed": real_device_gate_passed,
            "evidence_label": real_device_evidence_label,
            "issues": [],
        },
        "warnings": [],
        "claim_controls": {
            "installable_android_app_claim_allowed": installable_claim_allowed,
            "real_device_remote_control_claim_allowed": remote_claim_allowed,
            "expo_preview_is_not_release": True,
            "requires_reviewed_apk_install_evidence": True,
            "requires_reviewed_https_wss_remote_control_evidence": True,
        },
        "must_not_claim": must_not_claim,
        "next_steps": [
            "Build the EAS preview APK and pass it with -ArtifactPath.",
            "Collect reviewed HTTPS/WSS remote-control evidence.",
        ],
    }


def _write_android_release_gate_summary(root: Path, summary: dict) -> None:
    run_root = root / "run-20260609-000000-000"
    run_root.mkdir(parents=True)
    (run_root / "android-release-gate.redacted.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )


def _run_release_evidence_packet_with_android_gate(
    project_root: Path,
    tmp_path: Path,
    evidence_root: Path,
    android_root: Path,
) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_release_evidence_packet.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
            "-MobilePreflightEvidenceRoot",
            str(tmp_path / "empty-mobile-preflight"),
            "-AndroidReleaseGateEvidenceRoot",
            str(android_root),
            "-AndroidRealDeviceEvidenceRoot",
            str(tmp_path / "empty-android-real-device-template"),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-RcHandoffEvidenceRoot",
            str(tmp_path / "empty-rc-handoff-template"),
            "-QaEvidenceRoot",
            str(qa_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )


def _android_real_device_evidence(artifact_sha256: str) -> dict:
    return {
        "artifact_type": "android-real-device-remote-control-evidence",
        "review_status": "reviewed_passed",
        "real_device_result": "passed",
        "review": {
            "status": "reviewed_passed",
            "reviewer_label": "qa-reviewer-redacted",
            "reviewed_at_utc": "2026-06-09T00:00:00Z",
            "redaction_reviewed": True,
            "evidence_artifacts_reviewed": True,
        },
        "device": {
            "kind": "android_emulator",
            "profile_label_redacted": "pixel-qa-profile",
        },
        "transport": {
            "https_origin_redacted": "https://[redacted-host]:9443",
            "approval_wss_origin_redacted": "wss://[redacted-host]:9443/ws/mobile/approvals",
            "remote_screen_wss_origin_redacted": "wss://[redacted-host]:9443/ws/remote/screen",
            "remote_input_wss_origin_redacted": "wss://[redacted-host]:9443/ws/remote/input",
        },
        "certificate": {
            "trust_path_label_redacted": "android-user-ca-redacted",
        },
        "evidence_artifacts_redacted": [
            "android-remote-control-review.redacted.png",
        ],
        "app": {
            "artifact_sha256": artifact_sha256,
            "artifact_label_redacted": "lengrvis-preview.apk",
            "build_profile": "preview",
            "eas_build_label_redacted": "eas-preview-build-redacted",
        },
        "claim_controls": {
            "apk_installed": True,
            "camera_qr_pairing_verified": True,
            "https_api_reachability_verified": True,
            "https_wss_verified": True,
            "certificate_trust_verified": True,
            "approval_wss_verified": True,
            "remote_screen_verified": True,
            "remote_input_verified": True,
            "revoke_expiry_verified": True,
            "artifact_redaction_reviewed": True,
            "real_device_pass_claim_allowed": True,
        },
        "checks": {
            "apk_installed": "passed",
            "camera_qr_pairing": "passed",
            "https_api_reachability": "passed",
            "certificate_trust_path": "passed",
            "approval_wss": "passed",
            "remote_screen_wss": "passed",
            "remote_input_wss": "passed",
            "click_input_approval": "passed",
            "text_input_approval": "passed",
            "key_pagedown_approval": "passed",
            "mobile_end_control_readonly": "passed",
            "desktop_revoke_readonly": "passed",
            "grant_expiry_readonly": "passed",
            "background_or_lockscreen_privacy": "passed",
            "artifact_redaction_review": "passed",
        },
        "redaction": {
            "tokens_absent": True,
            "pairing_codes_absent": True,
            "raw_hosts_absent": True,
            "raw_device_ids_absent": True,
            "raw_grant_ids_absent": True,
            "private_paths_absent": True,
        },
    }


def _write_portable_status_log(root: Path, text: str) -> Path:
    run_root = root / "run-20260608-154045-41396-6013e259"
    run_root.mkdir(parents=True)
    status_log = run_root / "portable.status.log"
    status_log.write_text(text, encoding="utf-8")
    return status_log

def _android_real_device_evidence_placeholder():
    pass
