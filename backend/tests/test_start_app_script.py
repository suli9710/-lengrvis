from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tls_test_material import write_lan_tls_material

from app.commerce.licensing import sign_license, sign_revocation_manifest

_RELEASE_PRIVATE_KEY_BYTES = bytes(range(1, 33))
_RELEASE_PRIVATE_KEY = base64.urlsafe_b64encode(_RELEASE_PRIVATE_KEY_BYTES).rstrip(b"=").decode("ascii")
_RELEASE_PUBLIC_KEY = "ed25519:" + base64.urlsafe_b64encode(
    Ed25519PrivateKey.from_private_bytes(_RELEASE_PRIVATE_KEY_BYTES)
    .public_key()
    .public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).rstrip(b"=").decode("ascii")


def _start_app_text(project_root: Path) -> str:
    return (project_root / "scripts" / "start_app.ps1").read_text(encoding="utf-8")


def _setup_dev_text(project_root: Path) -> str:
    return (project_root / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")


def _portable_first_screen_smoke_text(project_root: Path) -> str:
    return (project_root / "scripts" / "portable_first_screen_smoke.ps1").read_text(encoding="utf-8")


def _build_portable_text(project_root: Path) -> str:
    return (project_root / "scripts" / "build_portable.ps1").read_text(encoding="utf-8")


def _mobile_lan_wss_preflight_text(project_root: Path) -> str:
    return (project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1").read_text(encoding="utf-8")


def _android_release_gate_text(project_root: Path) -> str:
    return (project_root / "scripts" / "verify_android_release_gate.ps1").read_text(encoding="utf-8")


def _release_evidence_packet_text(project_root: Path) -> str:
    return (project_root / "scripts" / "collect_release_evidence_packet.ps1").read_text(encoding="utf-8")


def _current_release_evidence_script_text(project_root: Path) -> str:
    return (project_root / "scripts" / "generate_current_release_evidence.ps1").read_text(encoding="utf-8")


def _current_release_evidence_doc_text(project_root: Path) -> str:
    return (project_root / "docs" / "release" / "current-release-evidence.md").read_text(encoding="utf-8")


def _ci_workflow_text(project_root: Path) -> str:
    return (project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _diagnostics_external_review_packet_text(project_root: Path) -> str:
    return (project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1").read_text(encoding="utf-8")


def _local_model_clean_machine_evidence_template_text(project_root: Path) -> str:
    return (project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1").read_text(
        encoding="utf-8"
    )


def _readme_text(project_root: Path) -> str:
    return (project_root / "README.md").read_text(encoding="utf-8")


def _release_gate_text(project_root: Path) -> str:
    return (project_root / "docs" / "qa" / "release-gate.md").read_text(encoding="utf-8")


def _package_json(project_root: Path) -> dict[str, object]:
    return json.loads((project_root / "package.json").read_text(encoding="utf-8"))


def _release_license_token(**extra: object) -> str:
    payload: dict[str, object] = {
        "schema": 1,
        "license_id": "lic_release_safety",
        "issuer": "Lengrvis Sales",
        "subject": "release-safety-redacted",
        "plan": "pro",
        "issued_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    return sign_license(payload, _RELEASE_PRIVATE_KEY)


def _release_revocations_token(*, generated_at: datetime | None = None) -> str:
    return sign_revocation_manifest(
        {
            "schema": 1,
            "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
            "issuer": "Lengrvis Sales",
            "revoked": [{"license_id": "lic_other_redacted", "reason": "admin"}],
        },
        _RELEASE_PRIVATE_KEY,
    )


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
        "LENGRVIS_CLOUD_QUOTA_ENFORCED",
        "LENGRVIS_CLOUD_QUOTA_MAX_CALLS",
        "LENGRVIS_CLOUD_QUOTA_MAX_COST_USD",
        "LENGRVIS_CLOUD_QUOTA_MAX_TOKENS",
        "LENGRVIS_CLOUD_QUOTA_WINDOW_HOURS",
        "LENGRVIS_CONFIG_FILE",
        "LENGRVIS_COMMERCIAL_RELEASE",
        "LENGRVIS_ENV_FILE",
        "LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP",
        "LENGRVIS_ACTIVATION_AUDIT_EVIDENCE",
        "LENGRVIS_ACTIVATION_BASE_URL",
        "LENGRVIS_ACTIVATION_OPERATIONS_EVIDENCE",
        "LENGRVIS_ACTIVATION_RATE_LIMIT_EVIDENCE",
        "LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF",
        "LENGRVIS_ACTIVATION_REVERSE_PROXY_EVIDENCE",
        "LENGRVIS_LICENSE_KEY",
        "LENGRVIS_LICENSE_REVOCATION_MAX_AGE_SECONDS",
        "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE",
        "LENGRVIS_ACTIVATION_SIGNING_PASSPHRASE_FILE",
        "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY",
        "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY_FILE",
        "LENGRVIS_LICENSE_PRIVATE_KEY",
        "LENGRVIS_LICENSE_PUBLIC_KEY",
        "LENGRVIS_LICENSE_REVOCATIONS",
        "LENGRVIS_LICENSE_SIGNING_KEY",
        "LENGRVIS_PLAN",
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


def _run_release_evidence_packet_for_portable_status_log(
    project_root: Path,
    tmp_path: Path,
    status_lines: list[str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(portable_root, "\n".join(status_lines))
    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"

    result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    return result, evidence_root


def _preflight_clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "LENGRVIS_BACKEND_HOST",
        "LENGRVIS_BACKEND_PORT",
        "LENGRVIS_LAN_PUBLIC_BASE_URL",
        "LENGRVIS_LAN_TLS_ENABLED",
        "LENGRVIS_LAN_TLS_CERT_FILE",
        "LENGRVIS_LAN_TLS_KEY_FILE",
    ):
        env.pop(key, None)
    return env


def _launcher_cmd_text(project_root: Path) -> str:
    return (project_root / "Start-Lengrvis.cmd").read_text(encoding="utf-8")


def _debug_cmd_text(project_root: Path) -> str:
    return (project_root / "Start-Lengrvis-Debug.cmd").read_text(encoding="utf-8")


def _markdown_section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    return text[start:] if next_heading == -1 else text[start:next_heading]


def test_start_app_defaults_lengrvis_env_once(project_root: Path) -> None:
    text = _start_app_text(project_root)
    assignment_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("$env:LENGRVIS_ENV =")]

    assert "elseif ($env:LENGRVIS_ENV)" not in text
    assert assignment_lines == ['$env:LENGRVIS_ENV = "development"']


def test_start_app_never_installs_dependencies(project_root: Path) -> None:
    text = _start_app_text(project_root)

    assert "& $Npm --prefix $DesktopDir install" not in text
    assert "& $npm --prefix $DesktopDir install" not in text
    assert "& $Python -m pip install" not in text
    assert "& $python -m pip install" not in text
    assert "正式启动不会现场运行 npm install" in text


def test_setup_dev_owns_dependency_install(project_root: Path) -> None:
    text = _setup_dev_text(project_root)

    assert "& $python -m pip install -U pip" in text
    assert "& $python -m pip install -r $requirementsPath" in text
    assert "& $npm --prefix $DesktopDir ci" in text
    assert "& $npm --prefix $DesktopDir install" in text


def test_build_portable_initializes_electron_runtime_when_missing(project_root: Path) -> None:
    text = _build_portable_text(project_root)

    assert "function Initialize-ElectronRuntime" in text
    assert "npm --prefix desktop exec electron -- --version" in text
    assert "Run npm --prefix desktop install first" not in text
    assert text.index("Initialize-ElectronRuntime -ElectronDist $ElectronDist") < text.index(
        "if (-not (Test-Path $ElectronDist))"
    )


def test_start_app_does_not_stop_workspace_owned_full_backend(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Stop-FullBackendIfWorkspaceOwned")
    function_end = text.index("function Stop-WorkspaceProcessOnPort", function_start)
    function_body = text[function_start:function_end]

    assert "Stop-Process" not in function_body
    assert "Stop-VerifiedListenProcess" not in function_body
    assert "为避免误关用户手动启动的服务" in function_body
    assert '.Contains("backend.main:full_app")' not in function_body


def test_start_app_main_backend_reuses_or_blocks_existing_listener(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Start-Backend")
    function_end = text.index("function Start-DesktopShell", function_start)
    function_body = text[function_start:function_end]

    assert (
        "elseif ((Test-WorkspaceProcess $commandLine) -or (Test-UvicornLengrvisBackend $commandLine))"
        not in function_body
    )
    assert "Stop-VerifiedListenProcess -Port $BackendPort -Process $existing" not in function_body
    assert "if (Test-Health)" in function_body
    assert "为避免误关用户手动启动的服务" in function_body


def test_start_app_can_auto_generate_lan_tls_for_phone_pairing(project_root: Path) -> None:
    text = _start_app_text(project_root)

    assert "[switch]$AutoLanTls" in text
    assert "LENGRVIS_LAN_TLS_AUTO" in text
    assert "[string]$LanPublicBaseUrl" in text
    assert "function Test-LoopbackLaunchHost" in text
    assert "app.security.lan_tls" in text
    assert "LENGRVIS_LAN_PUBLIC_BASE_URL" in text
    assert "$BackendUrl = $publicBaseUrl" in text
    assert "${BackendScheme}://127.0.0.1`:$BackendPort/api/health" in text
    assert "$env:LENGRVIS_LAN_TLS_CERT_FILE" in text
    assert "$env:LENGRVIS_LAN_TLS_KEY_FILE" in text


def test_install_service_auto_enables_lan_tls_for_non_loopback(project_root: Path) -> None:
    text = (project_root / "scripts" / "install_service.ps1").read_text(encoding="utf-8")

    assert "[switch]$AutoLanTls" in text
    assert "[string]$LanPublicBaseUrl" in text
    assert "$effectiveAutoLanTls" in text
    assert "Test-LoopbackHost $BackendHost" in text
    assert "--auto-lan-tls" in text
    assert "--lan-public-base-url" in text
    assert "LENGRVIS_LAN_TLS_AUTO" in text


def test_start_app_does_not_stop_port_discovered_processes(project_root: Path) -> None:
    text = _start_app_text(project_root)
    helper_start = text.index("function Stop-VerifiedListenProcess")
    helper_end = text.index("function Test-PackagedLengrvisBackend", helper_start)
    helper_body = text[helper_start:helper_end]

    assert "$current = Get-ListenProcess $Port" in helper_body
    assert "$currentPid -ne $processId" in helper_body
    assert "Stop-Process -Id $processId" in helper_body

    for function_name, next_function_name in [
        ("Stop-FullBackendIfWorkspaceOwned", "Stop-WorkspaceProcessOnPort"),
        ("Stop-WorkspaceProcessOnPort", "Stop-WorkspaceListenerOnPort"),
        ("Stop-WorkspaceListenerOnPort", "Ensure-NodeDependencies"),
    ]:
        function_start = text.index(f"function {function_name}")
        function_end = text.index(f"function {next_function_name}", function_start)
        function_body = text[function_start:function_end]
        assert "Stop-VerifiedListenProcess" not in function_body
        assert "Stop-Process -Id $" not in function_body

    backend_start = text.index("function Start-Backend")
    backend_end = text.index("function Start-Frontend", backend_start)
    backend_body = text[backend_start:backend_end]
    assert "Stop-VerifiedListenProcess -Port $BackendPort -Process $existing" not in backend_body
    assert "Stop-Process -Id $existing.ProcessId" not in backend_body

    frontend_start = text.index("function Start-Frontend")
    frontend_end = text.index("function Get-RunningDesktopProcess", frontend_start)
    frontend_body = text[frontend_start:frontend_end]
    assert "Stop-VerifiedListenProcess -Port $FrontendPort -Process $existing" not in frontend_body
    assert "Stop-Process -Id $existing.ProcessId" not in frontend_body


def test_start_app_frontend_reuses_only_lengrvis_frontend_listener(project_root: Path) -> None:
    text = _start_app_text(project_root)
    helper_start = text.index("function Test-LengrvisFrontendProcess")
    helper_end = text.index("function Stop-FullBackendIfWorkspaceOwned", helper_start)
    helper_body = text[helper_start:helper_end]

    assert "Test-WorkspaceProcess $CommandLine" in helper_body
    assert "\\desktop\\node_modules\\" in helper_body
    assert "vite" in helper_body

    frontend_start = text.index("function Start-Frontend")
    frontend_end = text.index("function Get-RunningDesktopProcess", frontend_start)
    frontend_body = text[frontend_start:frontend_end]

    assert "if (Test-LengrvisFrontendProcess $commandLine)" in frontend_body
    assert frontend_body.index("if (Test-LengrvisFrontendProcess $commandLine)") < frontend_body.index(
        "Invoke-WebRequest -Uri $FrontendUrl"
    )
    assert "界面服务端口 $FrontendPort 已被占用，但无法复用" in frontend_body


def test_debug_launcher_prints_redacted_summary_not_raw_logs(project_root: Path) -> None:
    text = _debug_cmd_text(project_root)

    assert "-PrintRecentLogs" in text
    assert "\ntype " not in text.lower()


def test_user_launch_docs_point_to_settings_and_debug_not_env_config(project_root: Path) -> None:
    text = _readme_text(project_root)
    quick_start = _markdown_section(text, "## 安装与快速开始")
    user_entry = _markdown_section(text, "## 配置、隐私与诊断")

    assert ".env" not in quick_start
    assert "config.yaml" not in quick_start
    assert "设置" in user_entry
    assert "普通用户不需要手动编辑 `.env` 或 `config.yaml`" in user_entry
    assert "Start-Lengrvis-Debug.cmd" in user_entry
    assert "导出诊断包" in user_entry
    assert "开发者可选真实 AI 配置" in text


def test_launchers_warn_non_developers_not_to_edit_env_or_config(project_root: Path) -> None:
    launcher_text = _launcher_cmd_text(project_root)
    debug_text = _debug_cmd_text(project_root)
    start_app_text = _start_app_text(project_root)

    assert ".env" in launcher_text
    assert "config.yaml" in launcher_text
    assert ".env" in debug_text
    assert "config.yaml" in debug_text
    assert ".env" in start_app_text
    assert "config.yaml" in start_app_text
    assert "Start-Lengrvis-Debug.cmd" in launcher_text
    assert "-PrintRecentLogs" in debug_text
    assert "Write-NextStep $failureMessage" in start_app_text


def test_root_evidence_scripts_are_discoverable_and_non_signoff(project_root: Path) -> None:
    package_json = _package_json(project_root)
    scripts = package_json["scripts"]
    assert isinstance(scripts, dict)

    expected_helpers = {
        "evidence:current-release": "generate_current_release_evidence.ps1",
        "evidence:release": "collect_release_evidence_packet.ps1",
        "evidence:rc-handoff": "collect_rc_handoff_template.ps1",
        "evidence:result-quality-review": "collect_result_quality_review_packet.ps1",
        "evidence:mobile-lan-wss": "verify_mobile_lan_wss_preflight.ps1",
        "evidence:local-model-template": "collect_local_model_clean_machine_evidence_template.ps1",
        "evidence:diagnostics-review": "collect_diagnostics_external_review_packet.ps1",
        "evidence:distribution-template": "collect_distribution_release_evidence_template.ps1",
    }

    for script_name, helper_name in expected_helpers.items():
        assert scripts[script_name] == (f"powershell -ExecutionPolicy Bypass -File ./scripts/{helper_name}")
        assert "pass" not in script_name
        assert "signoff" not in script_name
        assert "ready" not in script_name

        command = scripts[script_name].lower()
        assert "install" not in command
        assert "ollama pull" not in command
        assert "signoff" not in command
        assert "public_safe=true" not in command


def test_current_release_evidence_is_single_ci_generated_summary(project_root: Path) -> None:
    package_json = _package_json(project_root)
    ci = _ci_workflow_text(project_root)
    script = _current_release_evidence_script_text(project_root)
    evidence = _current_release_evidence_doc_text(project_root)
    release_gate = _release_gate_text(project_root)

    scripts = package_json["scripts"]
    assert scripts["evidence:current-release"] == (
        "powershell -ExecutionPolicy Bypass -File ./scripts/generate_current_release_evidence.ps1"
    )

    assert "release-evidence:" in ci
    assert "needs: [hygiene, backend, real-llm-quality, desktop, mobile, supply-chain, extension-security]" in ci
    assert "release_evidence_status=skipped" in ci
    assert "steps.real-llm-skip.outputs.release_evidence_status" in ci
    assert "if: always()" in ci
    assert "RELEASE_EVIDENCE_NEEDS_JSON: ${{ toJson(needs) }}" in ci
    assert "npm run evidence:current-release" in ci
    assert "name: current-release-evidence" in ci
    assert "path: docs/release/current-release-evidence.md" in ci

    for marker in (
        "docs\\release\\current-release-evidence.md",
        "Commit SHA:",
        "Date (UTC):",
        "## Machine Environment",
        "## Execution Commands",
        "## All Test Results",
        "Failed Items",
        "Exemptions",
        "Manual Acceptance Items",
        "Artifact Links",
        "## Owner Signature",
        "RELEASE_EVIDENCE_WAIVERS",
        "RELEASE_EVIDENCE_MANUAL_ACCEPTANCE",
        "RELEASE_OWNER_SIGNATURE",
        "PENDING_RELEASE_OWNER_SIGNATURE",
    ):
        assert marker in script

    for marker in (
        "# Current Release Evidence",
        "Commit SHA:",
        "Date (UTC):",
        "## Machine Environment",
        "## Execution Commands",
        "## All Test Results",
        "## Failed Items",
        "## Exemptions",
        "## Manual Acceptance Items",
        "## Artifact Links",
        "## Owner Signature",
    ):
        assert marker in evidence

    assert "CI also writes the single current release evidence summary" in release_gate
    assert "`docs/release/current-release-evidence.md`" in release_gate
    assert r".\scripts\generate_current_release_evidence.ps1" in release_gate
    assert "it is still not release sign-off, not RC sign-off, and not a pass" in release_gate


def test_current_release_evidence_ci_success_still_requires_manual_signature(
    project_root: Path,
    tmp_path: Path,
) -> None:
    needs = {
        gate: {"result": "success"}
        for gate in (
            "hygiene",
            "backend",
            "real-llm-quality",
            "desktop",
            "mobile",
            "supply-chain",
            "extension-security",
        )
    }
    output_path = tmp_path / "current-release-evidence.md"

    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "generate_current_release_evidence.ps1"),
            "-Root",
            str(project_root),
            "-OutputPath",
            str(output_path),
            "-CommitSha",
            "abc123",
            "-GeneratedAtUtc",
            "2026-06-20T00:00:00.0000000Z",
            "-NeedsJson",
            json.dumps(needs),
            "-ReleaseOwner",
            "release-owner",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    text = output_path.read_text(encoding="utf-8-sig")
    assert "- CI status: machine_gates_passed" in text
    assert "- Manual sign-off status: manual_signoff_pending" in text
    assert "- Owner signature: PENDING_RELEASE_OWNER_SIGNATURE" in text
    assert "It is not release sign-off" in text
    assert "| Supply chain lock + SBOM |" in text
    assert "| IPC + Skill/MCP + settings security gate |" in text


def test_current_release_evidence_accepts_explicit_manual_signoff_status(
    project_root: Path,
    tmp_path: Path,
) -> None:
    needs = {
        gate: {"result": "success"}
        for gate in (
            "hygiene",
            "backend",
            "real-llm-quality",
            "desktop",
            "mobile",
            "supply-chain",
            "extension-security",
        )
    }
    output_path = tmp_path / "current-release-evidence.md"

    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "generate_current_release_evidence.ps1"),
            "-Root",
            str(project_root),
            "-OutputPath",
            str(output_path),
            "-CommitSha",
            "abc123",
            "-GeneratedAtUtc",
            "2026-07-01T00:00:00Z",
            "-NeedsJson",
            json.dumps(needs),
            "-ReleaseOwner",
            "release-owner",
            "-OwnerSignature",
            "release-owner-accepted-rc",
            "-ManualSignoffStatus",
            "rc_signoff_recorded",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    text = output_path.read_text(encoding="utf-8-sig")
    assert "- CI status: machine_gates_passed" in text
    assert "- Manual sign-off status: rc_signoff_recorded" in text
    assert "- Owner signature: release-owner-accepted-rc" in text
    assert "Skill/MCP release-profile supply-chain controls" in text


def test_current_release_evidence_requires_every_ci_gate_success(
    project_root: Path,
    tmp_path: Path,
) -> None:
    needs = {
        gate: {"result": "success"}
        for gate in (
            "hygiene",
            "backend",
            "real-llm-quality",
            "desktop",
            "mobile",
            "supply-chain",
            "extension-security",
        )
    }
    needs["backend"] = {"result": "failure"}
    output_path = tmp_path / "current-release-evidence.md"

    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "generate_current_release_evidence.ps1"),
            "-Root",
            str(project_root),
            "-OutputPath",
            str(output_path),
            "-CommitSha",
            "abc123",
            "-GeneratedAtUtc",
            "2026-06-20T00:00:00.0000000Z",
            "-NeedsJson",
            json.dumps(needs),
            "-ReleaseOwner",
            "release-owner",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    text = output_path.read_text(encoding="utf-8-sig")
    assert "- CI status: machine_gates_failed_or_incomplete" in text
    assert "- Backend pytest + golden task gate: failure" in text
    assert "machine_gates_passed" not in text
    assert (
        "| Backend pytest + golden task gate | Backend pytest suite and golden task regression gate | failure |" in text
    )


def test_current_release_evidence_records_real_llm_secret_skip_as_incomplete(
    project_root: Path,
    tmp_path: Path,
) -> None:
    needs = {
        gate: {"result": "success"}
        for gate in (
            "hygiene",
            "backend",
            "real-llm-quality",
            "desktop",
            "mobile",
            "supply-chain",
            "extension-security",
        )
    }
    needs["real-llm-quality"] = {
        "result": "failure",
        "outputs": {"release_evidence_status": "skipped"},
    }
    output_path = tmp_path / "current-release-evidence.md"

    result = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "generate_current_release_evidence.ps1"),
            "-Root",
            str(project_root),
            "-OutputPath",
            str(output_path),
            "-CommitSha",
            "abc123",
            "-GeneratedAtUtc",
            "2026-06-20T00:00:00.0000000Z",
            "-NeedsJson",
            json.dumps(needs),
            "-ReleaseOwner",
            "release-owner",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    text = output_path.read_text(encoding="utf-8-sig")
    assert "- CI status: machine_gates_failed_or_incomplete" in text
    assert "- Real LLM quality gate: skipped" in text
    assert (
        "| Real LLM quality gate | Real-provider quality gate; skipped or missing credentials block release evidence | skipped |"
        in text
    )
    assert "machine_gates_passed" not in text


def test_release_safety_fails_closed_without_strict_state_machine(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(project_root, tmp_path)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Release safety verification failed:" in output
    assert "strict_state_machine=true" in output
    assert "source: default" in output
    assert "Release safety verification passed" not in output


def test_release_safety_passes_when_strict_enabled_and_mock_fallback_disabled(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {"LENGRVIS_STRICT_STATE_MACHINE": "true"},
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Release safety verification passed: allow_mock_fallback=false and strict_state_machine=true." in output


def test_release_safety_blocks_mock_fallback_even_with_strict_state_machine(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_ALLOW_MOCK_FALLBACK": "true",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Release/production builds must not enable LENGRVIS_ALLOW_MOCK_FALLBACK=true" in output
    assert "source: env:LENGRVIS_ALLOW_MOCK_FALLBACK" in output
    assert "strict_state_machine=true" not in output


def test_release_safety_requires_public_key_for_commercial_release(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Paid/commercial release profiles require LENGRVIS_LICENSE_PUBLIC_KEY" in output


def test_release_safety_requires_commercial_mode_for_paid_plan(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_PLAN": "pro",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Paid plan release profiles must set LENGRVIS_COMMERCIAL_RELEASE=true" in output


def test_release_safety_accepts_valid_public_key_for_commercial_release(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Commercial license secrets are offline-only" in output


def test_release_safety_rejects_commercial_quota_disable(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_CLOUD_QUOTA_ENFORCED": "false",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial release profiles must not set LENGRVIS_CLOUD_QUOTA_ENFORCED=false" in output


def test_release_safety_rejects_commercial_quota_overrides(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_CLOUD_QUOTA_MAX_TOKENS": "999999999",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial release profiles must not use LENGRVIS_CLOUD_QUOTA_* limit overrides" in output
    assert "LENGRVIS_CLOUD_QUOTA_MAX_TOKENS" in output


def test_release_safety_rejects_commercial_insecure_activation_http(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP": "true",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial release profiles must not set LENGRVIS_ACTIVATION_ALLOW_INSECURE_HTTP=true" in output


def test_release_safety_rejects_commercial_http_activation_base_url(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_ACTIVATION_BASE_URL": "http://activation.example",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial release profiles must use an HTTPS LENGRVIS_ACTIVATION_BASE_URL" in output


def test_release_safety_rejects_commercial_activation_without_deployment_evidence(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_ACTIVATION_BASE_URL": "https://activation.example",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF=true" in output
    assert "LENGRVIS_ACTIVATION_REVERSE_PROXY_EVIDENCE" in output
    assert "LENGRVIS_ACTIVATION_RATE_LIMIT_EVIDENCE" in output
    assert "LENGRVIS_ACTIVATION_AUDIT_EVIDENCE" in output
    assert "LENGRVIS_ACTIVATION_OPERATIONS_EVIDENCE" in output


def test_release_safety_accepts_commercial_activation_deployment_evidence(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": "ed25519:ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ",
            "LENGRVIS_ACTIVATION_BASE_URL": "https://activation.example",
            "LENGRVIS_ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF": "true",
            "LENGRVIS_ACTIVATION_REVERSE_PROXY_EVIDENCE": "reverse-proxy-redacted",
            "LENGRVIS_ACTIVATION_RATE_LIMIT_EVIDENCE": "rate-limit-redacted",
            "LENGRVIS_ACTIVATION_AUDIT_EVIDENCE": "audit-redacted",
            "LENGRVIS_ACTIVATION_OPERATIONS_EVIDENCE": "ops-redacted",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output


def test_release_safety_rejects_commercial_offline_license_without_revocations(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": _RELEASE_PUBLIC_KEY,
            "LENGRVIS_LICENSE_KEY": _release_license_token(),
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial offline paid license profiles require LENGRVIS_LICENSE_REVOCATIONS" in output


def test_release_safety_rejects_stale_commercial_revocations(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_COMMERCIAL_RELEASE": "true",
            "LENGRVIS_LICENSE_PUBLIC_KEY": _RELEASE_PUBLIC_KEY,
            "LENGRVIS_LICENSE_KEY": _release_license_token(),
            "LENGRVIS_LICENSE_REVOCATIONS": _release_revocations_token(
                generated_at=datetime.now(UTC) - timedelta(days=2)
            ),
            "LENGRVIS_LICENSE_REVOCATION_MAX_AGE_SECONDS": "3600",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Commercial revocation manifests are stale" in output


def test_release_safety_rejects_runtime_license_private_key(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_LICENSE_PRIVATE_KEY": "must-not-ship",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Release runtime must not contain LENGRVIS_LICENSE_PRIVATE_KEY" in output


def test_release_safety_rejects_runtime_activation_private_key(
    project_root: Path,
    tmp_path: Path,
) -> None:
    result = _run_release_safety(
        project_root,
        tmp_path,
        {
            "LENGRVIS_STRICT_STATE_MACHINE": "true",
            "LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY": "must-not-ship",
        },
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Release runtime must not contain LENGRVIS_ACTIVATION_SIGNING_PRIVATE_KEY" in output


def test_release_safety_default_strict_state_machine_is_documented_as_fail_closed(
    project_root: Path,
) -> None:
    config_py = (project_root / "backend" / "app" / "config.py").read_text(encoding="utf-8")
    example_config = (project_root / "config.example.yaml").read_text(encoding="utf-8")
    release_gate = _release_gate_text(project_root)

    assert "strict_state_machine: bool = False" in config_py
    assert "strict_state_machine: false" in example_config
    assert "release:safety` is expected to fail" in release_gate
    assert "Treat that as the release gate doing its job" in release_gate


def test_windows_signed_build_pipeline_has_fail_closed_config_gate(project_root: Path) -> None:
    desktop_package = json.loads((project_root / "desktop" / "package.json").read_text(encoding="utf-8"))
    scripts = desktop_package["scripts"]
    signed_config_path = project_root / "desktop" / "electron-builder.signed.js"
    signed_config = signed_config_path.read_text(encoding="utf-8")
    unsigned_config = (project_root / "desktop" / "electron-builder.yml").read_text(encoding="utf-8")
    verify_script = (project_root / "desktop" / "scripts" / "verify-signed-build-config.cjs").read_text(
        encoding="utf-8"
    )
    release_version_script = (project_root / "desktop" / "scripts" / "verify-release-version.cjs").read_text(
        encoding="utf-8"
    )

    assert scripts["verify:signed-build-config"] == "node scripts/verify-signed-build-config.cjs"
    assert (
        scripts["verify:signed-build-config:structure"]
        == "node scripts/verify-signed-build-config.cjs --structure-only"
    )
    assert scripts["verify:signed-build-config:mac"] == "node scripts/verify-signed-build-config.cjs mac"
    assert scripts["verify:macos-release-signatures"] == "node scripts/verify-macos-release-signatures.cjs"
    assert scripts["verify:linux-release-integrity"] == "node scripts/verify-linux-release-integrity.cjs"
    assert "verify:signed-build-config && npm run verify:backend-signature" in scripts["dist:signed"]
    assert "verify:signed-build-config && npm run verify:backend-signature" in scripts["dist:publish"]
    assert "verify:release-version -- --require-tag" in scripts["dist:publish"]
    assert "verify:signed-build-config:mac" in scripts["dist:mac:signed"]
    assert "verify:macos-release-signatures" in scripts["dist:mac:signed"]
    assert "verify:linux-release-integrity -- --write" in scripts["dist:linux"]
    assert "electron-builder.signed.js" in scripts["dist:signed"]
    assert "electron-builder.signed.js --publish always" in scripts["dist:publish"]
    assert "verify:signed-build-config" not in scripts["dist"]
    assert "electron-builder.yml" in scripts["dist"]
    assert not (project_root / "desktop" / "electron-builder.signed.yml").exists()

    assert "REPLACE_" not in signed_config
    assert "GITHUB_SHA" in release_version_script
    assert "does not match checked-out HEAD" in release_version_script
    assert "git\", [\"rev-parse\", \"HEAD\"]" in release_version_script
    assert "git\", [\"rev-list\", \"-n\", \"1\", tag]" in release_version_script
    assert "endpoint: process.env.AZURE_TRUSTED_SIGNING_ENDPOINT" in signed_config
    assert "codeSigningAccountName: process.env.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME" in signed_config
    assert "certificateProfileName: process.env.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME" in signed_config
    assert "azureSignOptions" in signed_config
    assert "publisherName" in signed_config
    assert "publisherName: [publisherName]" in signed_config
    assert "verifyUpdateCodeSignature: true" in signed_config
    assert "hardenedRuntime: true" in signed_config
    assert "notarize: macNotarizeOptions()" in signed_config
    assert "APPLE_TEAM_ID" in signed_config
    assert (project_root / "desktop" / "build" / "entitlements.mac.plist").exists()
    assert "REPLACE_" not in unsigned_config
    assert "未设置时跳过签名" in unsigned_config
    assert "仅限内部分发" in unsigned_config
    assert "electron-builder.signed.js" in unsigned_config

    for env_name in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TRUSTED_SIGNING_ENDPOINT",
        "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
        "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
        "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME",
    ):
        assert env_name in signed_config
        assert env_name in verify_script
    assert "AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT" in verify_script
    assert "REPLACE_" in verify_script
    assert "Signed Windows distribution configuration is incomplete" in verify_script
    assert "--structure-only" in verify_script
    assert "verify the backend binary signature before packaging" in verify_script
    assert "win.azureSignOptions.publisherName" in verify_script
    assert "win.publisherName[0]" in verify_script
    assert "mac.notarize" in verify_script
    assert "APPLE_APP_SPECIFIC_PASSWORD" in verify_script


def test_dependency_lock_verifier_checks_all_npm_transitive_sources(project_root: Path) -> None:
    verifier = (project_root / "scripts" / "verify_dependency_locks.ps1").read_text(encoding="utf-8")

    assert "Object.entries(lock.packages || {})" in verifier
    assert "packageEntry.integrity" in verifier
    assert "is missing SRI integrity" in verifier
    assert 'new Set(["registry.npmjs.org"])' in verifier
    assert 'url.protocol !== "https:"' in verifier
    assert 'resolved.startsWith("file:")' in verifier
    assert 'resolved.startsWith("git+")' in verifier
    assert 'resolved.startsWith("http:")' in verifier


def test_windows_release_signature_verification_covers_portable_artifacts(
    project_root: Path,
) -> None:
    verify_script = (project_root / "desktop" / "scripts" / "verify-windows-release-signatures.cjs").read_text(
        encoding="utf-8"
    )
    smoke_script = (project_root / "desktop" / "scripts" / "windows-release-signatures-smoke.cjs").read_text(
        encoding="utf-8"
    )
    root_package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    delivery_pipeline = (project_root / "scripts" / "delivery_pipeline.py").read_text(encoding="utf-8")
    normalized = verify_script.replace("\\", "/")

    assert "Lengrvis-win-portable" in normalized
    assert "x64-self-extracting.exe" in normalized
    assert "Lengrvis.exe" in normalized
    assert "portableBackendExe" in normalized
    assert "Get-AuthenticodeSignature" in verify_script
    assert 'status !== "Valid"' in verify_script
    assert "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME" in verify_script
    assert "AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT" in verify_script
    assert "TimeStamperCertificate" in verify_script
    assert root_package["scripts"]["release:check"] == "npm run delivery:rc"
    assert "delivery_pipeline.py --strict" in root_package["scripts"]["delivery:rc"]
    assert "verify:windows-release-signatures" in delivery_pipeline
    assert "Lengrvis-win-portable" in smoke_script
    assert "x64-self-extracting.exe" in smoke_script
    assert "TimestampSubject" in smoke_script


def test_windows_signed_build_config_gate_rejects_missing_release_env(
    project_root: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for desktop signed build config checks")

    env = os.environ.copy()
    for env_name in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TRUSTED_SIGNING_ENDPOINT",
        "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
        "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
        "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME",
    ):
        env.pop(env_name, None)

    result = subprocess.run(
        [
            node,
            str(project_root / "desktop" / "scripts" / "verify-signed-build-config.cjs"),
        ],
        cwd=project_root / "desktop",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Signed Windows distribution configuration is incomplete:" in output
    assert "Missing non-placeholder environment variable: AZURE_TRUSTED_SIGNING_ENDPOINT" in output
    assert "Missing non-placeholder environment variable: AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT" in output
    assert "Missing non-placeholder environment variable: AZURE_TRUSTED_SIGNING_PUBLISHER_NAME" in output
    assert "Unsigned local builds must use `npm --prefix desktop run dist:unsigned`" in output
    assert "Signed Windows distribution configuration verified" not in output


def test_windows_signed_build_config_structure_check_does_not_require_secrets(
    project_root: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for desktop signed build config checks")

    env = os.environ.copy()
    for env_name in (
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TRUSTED_SIGNING_ENDPOINT",
        "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
        "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
        "AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT",
        "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME",
    ):
        env.pop(env_name, None)

    result = subprocess.run(
        [
            node,
            str(project_root / "desktop" / "scripts" / "verify-signed-build-config.cjs"),
            "--structure-only",
        ],
        cwd=project_root / "desktop",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Signed Windows distribution configuration verified" in output
    assert "AZURE_CLIENT_SECRET" not in output


def test_readme_and_release_gate_expose_evidence_aliases_without_overclaim(
    project_root: Path,
) -> None:
    readme = _readme_text(project_root)
    release_gate = _release_gate_text(project_root)

    aliases = (
        "npm run evidence:current-release",
        "npm run evidence:release",
        "npm run evidence:rc-handoff",
        "npm run evidence:result-quality-review",
        "npm run evidence:mobile-lan-wss",
        "npm run evidence:local-model-template",
        "npm run evidence:diagnostics-review",
        "npm run evidence:distribution-template",
    )
    for alias in aliases:
        assert alias in readme
        assert alias in release_gate

    raw_helpers = (
        r".\scripts\generate_current_release_evidence.ps1",
        r".\scripts\collect_release_evidence_packet.ps1",
        r".\scripts\collect_rc_handoff_template.ps1",
        r".\scripts\collect_result_quality_review_packet.ps1",
        r".\scripts\verify_mobile_lan_wss_preflight.ps1",
        r".\scripts\collect_local_model_clean_machine_evidence_template.ps1",
        r".\scripts\collect_diagnostics_external_review_packet.ps1",
        r".\scripts\collect_distribution_release_evidence_template.ps1",
    )
    for helper in raw_helpers:
        assert helper in release_gate

    assert "证据 helper 新手入口" in readme
    assert "输出只能作为 evidence/template/preflight/inventory 记录" in readme
    assert "不是 clean-machine pass" in readme
    assert "real-device pass" in readme
    assert "`public_safe=true`" in readme
    assert "completed task-result signoff" in readme
    assert "signed-installer pass" in readme
    assert "upgrade/rollback pass" in readme

    assert "newcomer-friendly entrypoints" in release_gate
    assert "only produce evidence/template/preflight artifacts" in release_gate
    assert "not clean-machine passes" in release_gate
    assert "real-device passes" in release_gate
    assert "public-safe/signoff" in release_gate
    assert "completed task-result signoff" in release_gate
    assert "Raw PowerShell equivalent:" in release_gate
    assert "not true local model install/start/pull evidence" in release_gate
    assert "not release-candidate sign-off" in release_gate
    assert "public_safe=false" in release_gate
    assert "not as a pass" in release_gate
    assert "signed-installer pass" in release_gate
    assert "upgrade pass" in release_gate
    assert "rollback pass" in release_gate


def test_desktop_copy_exposes_settings_and_diagnostics_as_user_entrypoints(project_root: Path) -> None:
    settings_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SettingsPanel.tsx").read_text(
        encoding="utf-8"
    )
    system_info_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SystemInfoPanel.tsx").read_text(
        encoding="utf-8"
    )

    assert "普通用户的统一配置入口" in settings_text
    assert ".env" in settings_text
    assert "config.yaml" in settings_text
    assert "diagnostic-export-button" in system_info_text
    assert "导出诊断包" in system_info_text
    assert "刷新本机状态" in system_info_text


def test_portable_first_screen_smoke_proves_read_only_diagnostics(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert '$diagnosticsUrl = "$backendUrl/api/system/diagnostics"' in text
    assert "function Test-ReadOnlyDiagnostics" in text
    assert "X-Lengrvis-Desktop-Token" in text
    assert "LENGRVIS_DESKTOP_API_TOKEN = $desktopApiToken" in text
    assert "Invoke-WebRequest -Uri $DiagnosticsUrl -UseBasicParsing -TimeoutSec 5 -Method Get -Headers $headers" in text
    assert 'diagnosticScope -ne "local_only"' in text
    assert "local_paths.data_dir did not use the smoke temp data dir" in text
    assert "local_paths.database was outside the smoke temp data dir" in text
    assert "product_metrics.schema_version was missing" in text
    assert "Redact-SmokeText $Message" in text
    assert "-DesktopApiToken $desktopApiToken" in text
    assert "$diagnosticsObserved = $false" in text
    assert "if ($diagnosticsProbe.Ok -and $windowObserved)" in text
    assert "backend read-only diagnostics passed; waiting for portable window handle" in text
    assert "read-only diagnostics passed; no portable window handle yet" in text
    assert "did not prove both a visible portable window and backend diagnostics" in text
    assert "window pid=$windowProcessId" in text
    assert 'passReason = "backend answered $healthUrl"' not in text
    assert 'passReason = "window appeared' not in text


def test_portable_first_screen_smoke_attempts_renderer_dom_read_only_task_evidence(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "$remoteDebuggingPort = Get-FreeTcpPort" in text
    assert "--remote-debugging-address=127.0.0.1" in text
    assert "--remote-debugging-port=$remoteDebuggingPort" in text
    assert "[switch]$AllowBackendOnlyPass" in text
    assert "function Exit-SmokeUnsupported" in text
    assert "function Test-RendererDomEvidence" in text
    assert "function Invoke-PortableRendererDomAutomation" in text
    assert "chromium.connectOverCDP" in text
    assert 'page.locator("button").filter({ hasText: systemCheckPattern })' in text
    assert "portable renderer DOM read-only task evidence passed" in text
    assert "launcher/window/backend diagnostics pass remains limited" in text
    assert "renderer DOM evidence unavailable in strict portable smoke" in text
    assert "rerun with -AllowBackendOnlyPass only for legacy launcher/window/backend diagnostics evidence" in text
    assert "renderer DOM evidence status: $rendererEvidenceStatus" in text


def test_portable_first_screen_smoke_forbids_renderer_export_and_write_side_effects(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "const allowedReadOnlyGetEndpoints = new Set([" in text
    assert '"/health"' in text
    assert '"/api/health"' in text
    assert '"/api/tasks"' in text
    assert '"/api/settings/llm/health"' in text
    assert '"/api/settings/llm/cost-summary"' in text
    assert '"/api/system/info"' in text
    assert '"/api/system/diagnostics"' in text
    assert '"/api/system/processes"' in text
    assert '"/api/system/startup-items"' in text
    assert '"/api/apps"' in text
    assert "function isApiEndpoint(endpoint)" in text
    assert "function isDisallowedReadOnlyApiCall(call)" in text
    assert 'if (method !== "GET") return true;' in text
    assert "return !allowedReadOnlyGetEndpoints.has(endpoint);" in text
    assert "let observeReadOnlyClick = false;" in text
    assert "if (!observeReadOnlyClick) return;" in text
    assert "window.__portableSmokeBridgeCalls.length = 0" in text
    assert "observeReadOnlyClick = true;" in text
    assert 'wrap("system.exportDiagnosticsPackage"' in text
    assert 'wrap("runs.start"' in text
    assert "read-only GUI evidence cannot rely on web fallback requests" in text
    assert "natural-language GUI evidence cannot rely on web fallback state" in text
    assert "read-only renderer API call outside allowlist after system-check click" in text
    assert "system-check click did not invoke /api/system/diagnostics through the packaged renderer" in text
    assert "observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy" in text
    assert "lengrvis-api-request" in text
    assert 'const forbiddenWritePrefixes = ["/api/runs", "/api/chat", "/api/tasks"];' not in text
    assert "function Test-NoPortableWriteSideEffects" in text
    assert 'Url = "$BackendUrl/api/tasks"' in text
    assert 'Url = "$BackendUrl/api/runs"' in text
    assert 'Url = "$BackendUrl/api/chat/messages"' in text
    assert 'Join-Path $ExpectedDataDir "diagnostic-packages"' in text
    assert "no chat/run/task writes and no diagnostics export package after GUI click" in text
    assert 'Invoke-WebRequest -Uri "$backendUrl/api/system/diagnostics/export"' not in text


def test_portable_first_screen_smoke_attempts_natural_language_read_only_task_evidence(project_root: Path) -> None:
    text = _portable_first_screen_smoke_text(project_root)

    assert "function Invoke-PortableNaturalLanguageDomAutomation" in text
    assert 'const naturalLanguagePrompt = "\\u5e2e\\u6211\\u68c0\\u67e5\\u8fd9\\u53f0\\u7535\\u8111";' in text
    assert "function Test-PortableNaturalLanguageTaskEvidence" in text
    assert ".office-command-dock textarea" in text
    assert 'const expectedPostEndpoints = new Set(["/api/chat", "/api/runs"]);' in text
    assert "function waitForRendererBackendConnection(page, deadline)" in text
    assert "window.lengrvis?.backend?.getStatus" in text
    assert 'endpoint: "/api/health"' in text
    assert 'const refreshButtonSelector = \'button[aria-label="\\\\u5237\\\\u65b0"]' in text
    assert "function waitForCommandDockReady(page, input, deadline)" in text
    assert "packaged command dock send remained disabled after renderer/backend readiness wait" in text
    assert "backend task/run evidence will be verified separately" in text
    assert "lengrvis-api-request" in text
    assert "function Get-SmokeCollectionItems" in text
    assert "function Get-SmokeRecordIds" in text
    assert "BaselineTaskIds" in text
    assert "BaselineRunIds" in text
    assert "Write-Output -NoEnumerate $set" in text
    assert "$baselineTaskIdSet = New-SmokeStringSet -Values $BaselineTaskIds" in text
    assert "$baselineRunIdSet = New-SmokeStringSet -Values $BaselineRunIds" in text
    assert "$taskId -and -not $baselineTaskIdSet.Contains($taskId)" in text
    assert "$runId -and -not $baselineRunIdSet.Contains($runId)" in text
    assert "could not capture natural-language backend baseline before packaged prompt submission" in text
    assert (
        "backend evidence observed after renderer bridge submission attempt, but no packaged /api/chat or /api/runs POST was observed; keeping natural-language evidence unsupported"
        in text
    )
    assert "inferNaturalLanguagePostFromBackend" not in text
    assert "inferred: true" not in text
    assert "$messages.Count -gt 0 -or" not in text
    assert (
        "natural-language command dock displayed clear visible safe failure before submit; no packaged task submission was possible"
        in text
    )
    assert "visible safe failure is not accepted as natural-language task evidence" in text
    assert "natural-language visible safe failure side-effect check failed" in text
    assert "function Get-CompletionEvidenceSummary" in text
    assert "function Get-ResultQualitySummary" in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/tasks/$taskId/explain"' in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/tasks/$runTaskId/explain"' in text
    assert (
        "completion_evidence.level=$level result_verified=$resultVerifiedText completion_evidence.signoff=$signoffText"
        in text
    )
    assert "result_quality.state=$state" in text
    assert "result_quality.result_verified=$resultVerifiedText" in text
    assert "result_quality.can_treat_as_done=$canTreatAsDoneText" in text
    assert "result_quality.needs_review=$needsReviewText" in text
    assert "result_quality.missing_checks=$missingChecksText" in text
    assert "result_quality.signoff=$signoffText" in text
    assert "quality_signoff=not_collected" in text
    assert "$completionEvidence = Get-CompletionEvidenceSummary $taskExplainPayload" in text
    assert "$completionEvidence = Get-CompletionEvidenceSummary $runTaskExplainPayload" in text
    assert "$resultQuality = Get-ResultQualitySummary $taskExplainPayload" in text
    assert "$resultQuality = Get-ResultQualitySummary $runTaskExplainPayload" in text
    assert "$completionEvidence.Signoff -or $resultQuality.Signoff" in text
    assert "portable smoke cannot verify human result-quality sign-off" in text
    assert "portable renderer DOM natural-language read-only task evidence passed" in text
    assert "natural-language renderer DOM evidence failed" in text
    assert "read-only entry evidence remains valid but must not be counted as natural-language task evidence" in text
    assert "natural-language prompt created read-only/system diagnostics task" in text
    assert (
        "natural-language prompt produced clear visible safe failure copy in the packaged command dock, but no /api/chat or /api/runs POST was observed"
        in text
    )
    assert "safeFailureTask=$taskId status=$taskStatus" in text
    assert "safeFailureRun=$runId phase=$runPhase" in text
    assert "safeFailureChatWithoutTaskOrRun=true" in text
    assert "natural-language prompt did not expose concrete read-only/system diagnostics task or run evidence" in text
    assert "$highRiskPattern" in text
    assert "trash|rollback|uninstall" not in text
    assert "delete|remove" not in text
    assert "natural-language result proven by visible safe failure copy" not in text
    assert "natural-language prompt returned clear safe failure copy without creating task/run records" not in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/timeline"' in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/runs/$runId/progress"' in text
    assert r"/^\/api\/settings" in text
    assert r"/^\/api\/files" in text
    assert r"/^\/api\/apps" in text


def test_portable_docs_do_not_overclaim_gui_task_automation(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)
    parity = _parity_text(project_root)

    assert "Only the explicit renderer DOM evidence line counts as packaged GUI-task automation" in release_gate
    assert (
        "Any POST/PUT/PATCH/DELETE, unknown API mutation, diagnostics export, or settings/files/apps mutation during the read-only click fails the smoke"
        in release_gate
    )
    assert (
        "that pass requires a packaged renderer `/api/chat` or `/api/runs` POST plus backend read-only/system diagnostics task or run evidence"
        in release_gate
    )
    assert (
        "Visible safe-failure copy is still useful safety evidence when paired with zero side effects, but it is not accepted as natural-language task evidence"
        in release_gate
    )
    assert "This is submission/task-evidence coverage, not release-candidate completion sign-off" in release_gate
    assert "observes `/api/chat` or `/api/runs` and a related task/run" in release_gate
    assert (
        "If CDP or the packaged renderer cannot be automated, the strict script exits 2 with `[unsupported]`"
        in release_gate
    )
    assert "packaged renderer DOM automation to click the read-only" in parity
    assert "observed packaged renderer `POST /api/runs`" in parity
    assert (
        "Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence"
        in parity
    )
    assert "does not prove clean-machine release-candidate install" in parity
    assert "full natural-language agent task completion loop" in parity
    assert "separate manual release evidence" in parity


def test_portable_docs_route_completed_result_claims_through_completion_evidence(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)
    matrix = _e2e_acceptance_matrix_text(project_root)
    agentic_evals = _agentic_product_evals_text(project_root)

    for text in (release_gate, matrix, agentic_evals):
        assert "completion_evidence" in text
        assert "completed_result" in text
        assert "result_verified=true" in text

    assert (
        "`submission`, `task_created`, and `visible_progress` levels are not completed-result evidence" in release_gate
    )
    assert "`completion_evidence.signoff` remains false" in release_gate
    assert "Product/API explain evidence should use `completion_evidence`" in matrix
    assert "`completed_result` with `result_verified=true` is still not RC sign-off" in matrix
    assert "that remains result evidence, not result quality or release sign-off" in agentic_evals
    assert "`completion_evidence.signoff` remains false" in agentic_evals


def test_portable_docs_reference_latest_natural_language_evidence(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)
    matrix = _e2e_acceptance_matrix_text(project_root)
    productization = _productization_issues_text(project_root)
    agentic_evals = _agentic_product_evals_text(project_root)
    combined = "\n".join([release_gate, matrix, productization, agentic_evals])

    latest_run = r".tmp\portable-first-screen-smoke\run-20260608-154045-41396-6013e259"
    stale_runs = [
        "run-20260608-141325-18256-1520d784",
        "run-20260608-123849-34760-bc8d1829",
    ]

    for text in (release_gate, matrix, productization, agentic_evals):
        assert latest_run in text
        assert "POST /api/runs" in text
        assert "read-only/system diagnostics task evidence" in text

    for stale_run in stale_runs:
        assert stale_run not in combined

    assert "send stayed disabled" not in combined
    assert "visible safe-failure plus zero-write safety evidence" not in combined


def test_mobile_lan_wss_preflight_script_is_non_destructive_and_redacted(project_root: Path) -> None:
    text = _mobile_lan_wss_preflight_text(project_root)

    assert "LENGRVIS_BACKEND_HOST" in text
    assert "LENGRVIS_BACKEND_PORT" in text
    assert "LENGRVIS_LAN_PUBLIC_BASE_URL" in text
    assert "LENGRVIS_LAN_TLS_ENABLED" in text
    assert "LENGRVIS_LAN_TLS_CERT_FILE" in text
    assert "LENGRVIS_LAN_TLS_KEY_FILE" in text
    assert "lengrvis.mobile_pairing.qr" in text
    assert "lengrvis.mobile_pairing" in text
    assert "websocket_approvals_url_redacted" in text
    assert "websocket_remote_screen_url_redacted" in text
    assert "websocket_remote_input_url_redacted" in text
    assert "evidence-summary.redacted.json" in text
    assert "real-device-evidence-checklist.redacted.md" in text
    assert "redacted_evidence_summary_path" in text
    assert "redacted_evidence_checklist_path" in text
    assert "manual_real_device_evidence_template" in text
    assert "manual_real_device_evidence_required" in text
    assert "real_device_collection_checklist" in text
    assert "artifact_collection_rules" in text
    assert "operator_collection_order" in text
    assert "real_device_pass_claim_allowed" in text
    assert "real_device_result" in text
    assert "uncollected" in text
    assert "blocked_reason_redacted" in text
    assert "must_not_be_recorded_as" in text
    assert "remote_screen_wss_origin_redacted" in text
    assert "remote_input_grant_revoke_evidence" in text
    assert "remote_input_grant_expiry_evidence" in text
    assert "Token-bearing mobile LAN flows require HTTPS and WSS" in text
    assert "Non-loopback HTTP/ws is blocked-path evidence only" in text
    assert "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection" in text
    assert "must not be recorded as real-device pass evidence" in text
    assert "Manual real-device evidence remains uncollected" in text
    assert "Never paste token-bearing URLs" in text
    assert "raw LAN IPs, hostnames, device names" in text
    assert "0.0.0.0 is a bind address" in text
    assert "loopback-only" in text
    assert "exit 1" in text

    assert "Start-Process" not in text
    assert "Invoke-WebRequest" not in text
    assert "Invoke-RestMethod" not in text
    assert "Import-Certificate" not in text
    assert "certutil" not in text.lower()
    assert "Set-Content -LiteralPath $summaryPath" in text
    assert "Set-Content -LiteralPath $checklistPath" in text


def test_release_gate_recommends_mobile_lan_wss_preflight_without_overclaim(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)

    assert r".\scripts\verify_mobile_lan_wss_preflight.ps1" in release_gate
    assert r".tmp\mobile-lan-wss-preflight\...\evidence-summary.redacted.json" in release_gate
    assert "backend host/public URL/cert environment" in release_gate
    assert "certificate host coverage for the advertised origin" in release_gate
    assert "certificate host mismatch" in release_gate
    assert "backend host/public URL/cert env" in release_gate
    assert "QR payload shape" in release_gate
    assert "HTTPS/WSS requirement wording" in release_gate
    assert "without using a phone, emulator, camera, QR scanner, or real WSS connection" in release_gate
    assert "It must not be recorded as a real-device pass" in release_gate
    assert "manual_real_device_evidence_template" in release_gate
    assert "real_device_result=uncollected" in release_gate
    assert "must_not_be_recorded_as=real-device pass evidence" in release_gate
    assert "claim_controls.real_device_pass_claim_allowed=false" in release_gate
    assert "real_device_collection_checklist" in release_gate
    assert "grant revoke/expiry" in release_gate
    assert "screenshot/log review" in release_gate
    assert "blocked_reason_redacted" in release_gate
    assert (
        "does not replace a real phone/emulator camera/QR path, actual WSS connection, or explicit Android/emulator certificate trust evidence"
        in release_gate
    )
    assert "Mobile LAN/WSS prerequisite preflight" in release_gate
    assert "it is not real-device pass evidence" in release_gate
    assert "mobile real-device redacted template" in release_gate


def test_mobile_remote_release_docs_keep_counts_command_bound(project_root: Path) -> None:
    docs = {
        "README.md": _readme_text(project_root),
        "docs/LENGRVIS_PARITY.md": _parity_text(project_root),
        "docs/qa/release-gate.md": _release_gate_text(project_root),
        "docs/qa/e2e-acceptance-matrix.md": _e2e_acceptance_matrix_text(project_root),
        "docs/qa/real-device-mobile-matrix.md": _real_device_mobile_matrix_text(project_root),
        "docs/qa/agentic-product-evals.md": _agentic_product_evals_text(project_root),
        "PRODUCTIZATION_ISSUES.md": _productization_issues_text(project_root),
        "docs/qa/backend-test-runtime.md": (project_root / "docs" / "qa" / "backend-test-runtime.md").read_text(
            encoding="utf-8"
        ),
        "docs/qa/remote-session-lan-tls-gate.md": (
            project_root / "docs" / "qa" / "remote-session-lan-tls-gate.md"
        ).read_text(encoding="utf-8"),
    }

    stale_scheduler_count_phrases = (
        "scheduler/preflight targeted run `9 passed`",
        "scheduler/preflight targeted checks at `9 passed`",
        "scheduler/preflight `9 passed`",
    )
    unbound_count_warnings = (
        "do not cite an unbound `9 passed`",
        "不要把未绑定命令的 `9 passed`",
        "scheduler/preflight counts need an exact command/log before citation",
        "scheduler/preflight 计数只有在附 exact command/log 时才可引用",
        "Scheduler/preflight checks must carry their exact command/log before any count is cited",
        "Scheduler/preflight counts require exact command/log evidence before they are cited",
    )

    for doc_path, text in docs.items():
        assert "120 passed" not in text, doc_path
        assert "52 passed" not in text, doc_path
        assert "123 passed" not in text, doc_path
        assert "131 passed" not in text, doc_path
        if doc_path == "README.md":
            assert "132 passed" not in text, doc_path
            assert "README 不再维护手写的“最近一次测试结果”" in text
            assert "docs/release/current-release-evidence.md" in text
        elif "132 passed" in text:
            assert "2026-06-09" in text, doc_path
            assert "backend" in text.casefold(), doc_path
            assert "targeted" in text.casefold(), doc_path
        for phrase in stale_scheduler_count_phrases:
            assert phrase not in text, doc_path
        if "`9 passed`" in text:
            assert any(warning in text for warning in unbound_count_warnings), doc_path

    local_model_docs = {
        "PRODUCTIZATION_ISSUES.md": docs["PRODUCTIZATION_ISSUES.md"],
        "docs/LENGRVIS_PARITY.md": docs["docs/LENGRVIS_PARITY.md"],
        "docs/qa/release-gate.md": docs["docs/qa/release-gate.md"],
        "docs/qa/e2e-acceptance-matrix.md": docs["docs/qa/e2e-acceptance-matrix.md"],
        "docs/qa/agentic-product-evals.md": docs["docs/qa/agentic-product-evals.md"],
    }
    assert "53 passed" not in docs["README.md"]
    assert "Ollama 后端测试结果以 current release evidence" in docs["README.md"]
    for doc_path, text in local_model_docs.items():
        assert "53 passed" in text, doc_path


def test_evidence_alias_names_and_docs_do_not_imply_pass_or_signoff(project_root: Path) -> None:
    package = _package_json(project_root)
    scripts = package["scripts"]
    expected_aliases = {
        "evidence:current-release",
        "evidence:release",
        "evidence:release-packet",
        "evidence:rc-handoff",
        "evidence:rc-handoff-template",
        "evidence:result-quality-review",
        "evidence:result-quality-verify",
        "evidence:mobile-lan-wss",
        "evidence:android-real-device-template",
        "evidence:local-model-template",
        "evidence:diagnostics-review",
        "evidence:distribution-template",
        "evidence:paid-launch-template",
        "evidence:distribution-verify",
        "evidence:clean-machine-verify",
        "evidence:support-privacy-verify",
        "evidence:claims-launch-verify",
        "evidence:commercial-operations-verify",
        "evidence:commercial-operations-seal",
        "evidence:commercial-loop",
    }
    forbidden_name_pattern = re.compile(
        r"pass|passed|signoff|sign-off|signed-off|approved|approval|public-safe|ready",
        re.IGNORECASE,
    )

    evidence_aliases = {name for name in scripts if name.startswith("evidence:")}
    assert evidence_aliases == expected_aliases
    for alias in evidence_aliases:
        assert not forbidden_name_pattern.search(alias)

    docs_to_check = [
        project_root / "README.md",
        project_root / "docs" / "LENGRVIS_PARITY.md",
        project_root / "docs" / "qa" / "agentic-product-evals.md",
        project_root / "docs" / "qa" / "e2e-acceptance-matrix.md",
        project_root / "docs" / "qa" / "real-device-mobile-matrix.md",
        project_root / "docs" / "qa" / "release-gate.md",
    ]
    alias_mention_pattern = re.compile(r"npm run (evidence:[a-z0-9:-]+)", re.IGNORECASE)
    no_overclaim_phrases = [
        "not as a pass",
        "not a pass",
        "not clean-machine passes",
        "not real-device passes",
        "not real-device pass",
        "not release-candidate sign-off",
        "not completed task-result sign-off",
        "not true local model",
        "not public-safe/signoff",
        "handoff template",
        "template only",
        "contract summary only",
        "cannot replace",
        "does not replace",
        "fail-closed",
        "release_readiness_blockers",
        "public_safe=false",
        "not public-safe",
        "not release sign-off",
        "涓嶆槸绛炬敹",
        "涓嶆槸 clean-machine pass",
        "涓嶆槸 real-device pass",
        "涓嶆槸 `public_safe=true`",
        "涓嶆槸 public-safe/signoff",
        "不允许宣称",
    ]

    for doc_path in docs_to_check:
        text = doc_path.read_text(encoding="utf-8")
        for match in alias_mention_pattern.finditer(text):
            alias = match.group(1)
            context = text[max(0, match.start() - 700) : match.end() + 700].lower()
            assert alias in expected_aliases
            assert any(phrase in context for phrase in no_overclaim_phrases), (
                f"{doc_path} mentions npm run {alias} without nearby no-overclaim wording"
            )


def test_release_evidence_packet_script_is_read_only_and_redacted(project_root: Path) -> None:
    text = _release_evidence_packet_text(project_root)

    assert "release-evidence-packet.redacted.json" in text
    assert "release-evidence-packet.redacted.md" in text
    assert "mobile_lan_wss_preflight" in text
    assert "PortableFirstScreenEvidenceRoot" in text
    assert "portable_first_screen_smoke" in text
    assert "portable.status.log" in text
    assert "latest_redacted_status_log" in text
    assert "not completed task-result sign-off" in text
    assert "packet_is_pass = $false" in text
    assert "agent_task_completion_signoff = $false" in text
    assert "result_quality_signoff = $false" in text
    assert "evidence_count_is_not_acceptance_count = $true" in text
    assert "source_artifacts_read_for_summary = $true" in text
    assert "secrets_or_tokens_emitted = $false" in text
    assert "ollama_local_model_contracts" in text
    assert "LocalModelCleanMachineEvidenceRoot" in text
    assert "local-model-clean-machine-evidence.redacted.json" in text
    assert "local_model_clean_machine_template" in text
    assert "latest_redacted_clean_machine_template" in text
    assert "latest local-model clean-machine helper artifact failed fail-closed validation" in text
    assert "diagnostics_external_review" in text
    assert "DiagnosticsReviewEvidenceRoot" in text
    assert "diagnostics-external-review.redacted.json" in text
    assert "latest_redacted_review_packet" in text
    assert "result_quality_review" in text
    assert "ResultQualityReviewEvidenceRoot" in text
    assert "result-quality-review.redacted.json" in text
    assert "NOT_RESULT_QUALITY_SIGNOFF" in text
    assert "result_quality_claim_blocked = $true" in text
    assert "separate_human_signoff_required = $true" in text
    assert "summary.review_fields_complete does not match missing/issue/status state" in text
    assert "summary.external_sharing_blocked is not true" in text
    assert "summary.separate_human_content_review_required is not true" in text
    assert "claim_controls.external_sharing_blocked is not true" in text
    assert "claim_controls.separate_human_content_review_required is not true" in text
    assert "latest result-quality review helper artifact failed fail-closed validation" in text
    assert "RcHandoffEvidenceRoot" in text
    assert "rc-handoff-template.redacted.json" in text
    assert "rc_handoff_template" in text
    assert "latest_redacted_handoff_template" in text
    assert "NOT_RELEASE_CANDIDATE_SIGNOFF" in text
    assert "summary.release_candidate_signoff is not false" in text
    assert "summary.gate_commands_run_by_this_helper is not false" in text
    assert "signoff_controls.must_not_tag_publish_or_announce is not true" in text
    assert "latest RC handoff helper artifact failed fail-closed validation" in text
    assert "settings_local_model_smoke" in text
    assert "mobile_remote_input_active_grant_contract" in text
    assert "assertRemoteInputApprovalMatchesSession" in text
    assert "remoteInputApprovalMatchesActiveGrant" in text
    assert "client-side remote-input binding failures must not reach the smoke server" in text
    assert "static source contract markers in mobile UI/client/smoke sources" in text
    assert 'latest_execution_status = "not_run_by_this_packet"' in text
    assert "not evidence that the smoke command was executed by this packet" in text
    assert "not proof of a live desktop-to-mobile remote input session" in text
    assert "not backend TestClient, desktop smoke, packaged, or clean-machine evidence by itself" in text
    assert "not_signoff=source/client contract only, not live device/WSS" in text
    assert "contract_count" in text
    assert "expected_public_safe = $false" in text
    assert "clean_machine_signoff = $false" in text
    assert "local_model_install_pass = $false" in text
    assert "local_model_start_pass = $false" in text
    assert "local_model_pull_pass = $false" in text
    assert "real_device_signoff = $false" in text
    assert "release_candidate_signoff = $false" in text
    assert "release_readiness_blockers" in text
    assert "release_readiness_blocker_count" in text
    assert "release_ready = $false" in text
    assert "claimable_release_signoff = $false" in text
    assert "missing_real_device_artifacts" in text
    assert "missing_result_quality_signoff" in text
    assert "manual_content_review_required" in text
    assert 'must_not_claim = "release-candidate pass"' in text
    assert "rc_handoff_requirements" in text
    assert 'status = "manual_rc_handoff_required"' in text
    assert "packet_is_rc_signoff = $false" in text
    assert "candidate commit or build id" in text
    assert "exact release gate commands and full exit status" in text
    assert "manual P1 checks with owner and timestamp" in text
    assert "waivers with owner, reason, expiry condition, and follow-up task" in text
    assert "Use this packet as a redacted checklist only" in text
    assert "## Release Readiness Blockers" in text
    assert "blocker_count=$($packet.summary.release_readiness_blocker_count)" in text
    assert "not_clean_machine_or_signoff" in text
    assert "not real-device pass evidence" in text
    assert "not clean-machine local model install evidence" in text
    assert "not external public-safety approval" in text
    assert "not a human content review sign-off" in text
    assert "not natural-language result-quality sign-off" in text
    assert "not packaged Settings evidence" in text
    assert "workspace-relative paths or file labels only" in text
    assert "starts_product_processes = $false" in text
    assert "performs_network_requests = $false" in text
    assert "changes_backend_product_logic = $false" in text
    assert "changes_desktop_ui = $false" in text
    assert "changes_mobile_app = $false" in text
    assert "natural_language_completion_evidence" in text
    assert "completion_evidence\\.level" in text
    assert (
        '$naturalLanguageCompletionLevel -eq "completed_result" -and $naturalLanguageResultVerified -and -not $naturalLanguageSignoff'
        in text
    )
    assert "natural-language pass line reports result_verified without completed_result level" in text
    assert "natural-language pass line must not report completion_evidence signoff" in text

    assert "Start-Process" not in text
    assert "Stop-Process" not in text
    assert "Invoke-WebRequest" not in text
    assert "Invoke-RestMethod" not in text
    assert "Import-Certificate" not in text
    assert "certutil" not in text.lower()
    assert "Copy-Item" not in text
    assert "Move-Item" not in text
    assert "Remove-Item" not in text
    assert "Set-Content -LiteralPath $jsonPath" in text
    assert "Set-Content -LiteralPath $markdownPath" in text


def test_android_release_gate_preflight_is_not_release_pass(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "android-release-gate"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_android_release_gate.ps1"),
            "-Root",
            str(project_root),
            "-PreflightOnly",
            "-OutputRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "preflight_ready_not_release" in output
    assert "not an installable APK pass or real-device remote-control pass" in output
    packet = json.loads(next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(encoding="utf-8-sig"))
    assert packet["status"] == "preflight_ready_not_release"
    assert packet["release_ready"] is False
    assert packet["preflight_only"] is True
    assert packet["source_config"]["passed"] is True
    assert packet["android_artifact"]["provided"] is False
    assert packet["android_artifact"]["installable_apk"] is False
    assert packet["artifact_gate"]["evaluated"] is False
    assert packet["artifact_gate"]["passed"] is False
    assert packet["real_device_gate"]["evaluated"] is False
    assert packet["real_device_gate"]["passed"] is False
    assert packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert "installable Android app release pass" in packet["must_not_claim"]


def test_android_release_gate_redacts_missing_private_paths(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "android-release-gate"
    private_dir = tmp_path / "private-token-secret"
    missing_apk = private_dir / "missing.apk"
    missing_evidence = private_dir / "missing-evidence.json"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_android_release_gate.ps1"),
            "-Root",
            str(project_root),
            "-ArtifactPath",
            str(missing_apk),
            "-RealDeviceEvidencePath",
            str(missing_evidence),
            "-OutputRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    packet_text = next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("android-release-gate.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((output, packet_text, markdown_text))
    assert str(tmp_path) not in combined
    assert "private-token-secret" not in combined
    assert "missing.apk" in combined
    assert "missing-evidence.json" in combined

    packet = json.loads(packet_text)
    assert packet["status"] == "blocked"
    assert packet["release_ready"] is False
    assert packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    issue_messages = "\n".join(
        issue["message"] for section in ("artifact_gate", "real_device_gate") for issue in packet[section]["issues"]
    )
    assert "missing.apk" in issue_messages
    assert "missing-evidence.json" in issue_messages


def test_android_release_gate_rejects_fake_apk_even_with_reviewed_evidence(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    fake_apk = tmp_path / "lengrvis-preview.apk"
    fake_apk.write_bytes(b"not-a-zip-apk" * 100_000)
    evidence_json = tmp_path / "android-real-device-evidence.redacted.json"
    evidence_json.write_text(
        json.dumps(_android_real_device_evidence(hashlib.sha256(fake_apk.read_bytes()).hexdigest())),
        encoding="utf-8",
    )
    evidence_root = tmp_path / "android-release-gate"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_android_release_gate.ps1"),
            "-Root",
            str(project_root),
            "-ArtifactPath",
            str(fake_apk),
            "-RealDeviceEvidencePath",
            str(evidence_json),
            "-OutputRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    packet = json.loads(next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(encoding="utf-8-sig"))
    assert packet["status"] == "blocked"
    assert packet["release_ready"] is False
    assert packet["android_artifact"]["provided"] is True
    assert packet["android_artifact"]["installable_apk"] is False
    assert packet["android_artifact"]["apk_zip_header_valid"] is False
    assert packet["artifact_gate"]["passed"] is False
    assert packet["real_device_gate"]["passed"] is True
    assert packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert any(issue["code"] == "artifact_not_apk_zip" for issue in packet["artifact_gate"]["issues"])


def test_release_evidence_packet_outputs_redacted_json_and_markdown(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-20260608-000000-000"
    mobile_run.mkdir(parents=True)
    (mobile_run / "evidence-summary.redacted.json").write_text(
        json.dumps(_mobile_lan_wss_preflight_summary()),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")

    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-AndroidReleaseGateEvidenceRoot",
            str(tmp_path / "empty-android-release-gate"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-clean-machine-evidence"),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review-evidence"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review-evidence"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Release evidence packet summary" in output
    assert "Ollama/local-model contracts" in output
    assert "public_safe=False" in output
    assert str(tmp_path) not in output

    json_outputs = list(evidence_root.rglob("release-evidence-packet.redacted.json"))
    markdown_outputs = list(evidence_root.rglob("release-evidence-packet.redacted.md"))
    assert len(json_outputs) == 1
    assert len(markdown_outputs) == 1

    packet_text = json_outputs[0].read_text(encoding="utf-8-sig")
    markdown_text = markdown_outputs[0].read_text(encoding="utf-8-sig")
    assert str(tmp_path) not in packet_text
    assert str(tmp_path) not in markdown_text

    packet = json.loads(packet_text)
    expected_ollama_contracts = sum(
        len(
            re.findall(
                r"^(?:async\s+def|def)\s+test_", (project_root / path).read_text(encoding="utf-8"), flags=re.MULTILINE
            )
        )
        for path in (
            "backend/tests/test_ollama_service.py",
            "backend/tests/test_ollama_install_endpoint.py",
        )
    )

    assert packet["summary"]["clean_machine_signoff"] is False
    assert packet["summary"]["packet_is_pass"] is False
    assert packet["summary"]["evidence_count_is_not_acceptance_count"] is True
    assert packet["summary"]["automated_evidence_items"] == 12
    assert packet["summary"]["indexed_evidence_buckets"] == 12
    assert packet["summary"]["agent_task_completion_signoff"] is False
    assert packet["summary"]["result_quality_signoff"] is False
    assert packet["summary"]["portable_natural_language_scope"] == "submission_plus_read_only_routing_evidence_only"
    assert packet["summary"]["local_model_install_pass"] is False
    assert packet["summary"]["local_model_start_pass"] is False
    assert packet["summary"]["local_model_pull_pass"] is False
    assert packet["summary"]["local_model_task_smoke_pass"] is False
    assert packet["summary"]["template_is_clean_machine_pass"] is False
    assert packet["summary"]["dev_smoke_is_clean_machine_pass"] is False
    assert packet["summary"]["real_device_signoff"] is False
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["diagnostics_public_safe"] is False
    assert packet["summary"]["release_ready"] is False
    assert packet["summary"]["claimable_release_signoff"] is False
    assert packet["summary"]["release_readiness_blocker_count"] == 6
    assert packet["summary"]["packet_status"] == "redacted_partial_evidence_summary"
    assert "Packet role: evidence index only; packet_is_pass=false" in markdown_text
    assert "Release readiness: release_ready=False; claimable_release_signoff=False; blocker_count=6" in markdown_text
    rc_handoff = packet["rc_handoff_requirements"]
    assert rc_handoff["status"] == "manual_rc_handoff_required"
    assert rc_handoff["release_candidate_signoff"] is False
    assert rc_handoff["packet_is_rc_signoff"] is False
    assert "candidate commit or build id" in rc_handoff["required_before_rc_signoff"]
    assert "exact release gate commands and full exit status" in rc_handoff["required_before_rc_signoff"]
    assert "strict-state-machine source used for the release gate" in rc_handoff["missing_by_default"]
    assert "manual P1 checks" in rc_handoff["missing_by_default"]
    assert "release-candidate pass" in rc_handoff["must_not_be_recorded_as"]
    assert "Use this packet as a redacted checklist only" in rc_handoff["beginner_instruction"]
    assert "## RC Handoff Requirements" in markdown_text
    assert "manual_rc_handoff_required" in markdown_text
    assert "release_candidate_signoff=False" in markdown_text
    assert "packet_is_rc_signoff=False" in markdown_text
    rc_template = packet["evidence"]["rc_handoff_template"]
    assert rc_template["status"] == "manual_rc_handoff_contract_present"
    assert rc_template["latest_redacted_handoff_template"]["found"] is False
    assert rc_template["latest_redacted_handoff_template"]["handoff_status"] == ("not_collected_by_this_packet")
    assert rc_template["latest_redacted_handoff_template"]["release_candidate_signoff"] is False
    assert rc_template["latest_redacted_handoff_template"]["claim_allowed"] is False
    assert rc_template["expected_marker"] == "NOT_RELEASE_CANDIDATE_SIGNOFF"
    assert rc_template["expected_gate_commands_run_by_this_helper"] is False
    assert "not permission to tag, publish, announce, or ship" in rc_template["not_signoff"]
    assert "RC handoff template: found=False" in markdown_text
    blockers = {item["id"]: item for item in packet["release_readiness_blockers"]}
    assert set(blockers) == {
        "clean_machine_local_model",
        "mobile_real_device_lan_wss",
        "android_installable_remote_control",
        "natural_language_result_quality",
        "diagnostics_external_public_safety",
        "release_candidate_handoff",
    }
    for blocker in blockers.values():
        assert blocker["claim_allowed"] is False
        assert blocker["required_evidence"]
        assert blocker["beginner_next_step"]
        assert blocker["must_not_claim"]
    assert blockers["mobile_real_device_lan_wss"]["status"] == "missing_real_device_artifacts"
    assert blockers["android_installable_remote_control"]["status"] == "missing_apk_or_real_device_gate"
    assert blockers["android_installable_remote_control"]["claim_allowed"] is False
    assert blockers["android_installable_remote_control"]["must_not_claim"] == (
        "installable Android app or real-device Android remote-control pass"
    )
    assert blockers["natural_language_result_quality"]["status"] == "missing_result_quality_signoff"
    assert blockers["release_candidate_handoff"]["must_not_claim"] == "release-candidate pass"
    assert "## Release Readiness Blockers" in markdown_text
    assert "mobile_real_device_lan_wss: status=missing_real_device_artifacts" in markdown_text
    active_grant_contract = packet["evidence"]["mobile_remote_input_active_grant_contract"]
    assert active_grant_contract["status"] == "fail_closed_source_contract_present"
    assert (
        active_grant_contract["automated_scope"] == "static source contract markers in mobile UI/client/smoke sources"
    )
    assert active_grant_contract["verify_command"] == "npm --prefix mobile run smoke:remote-input-grant"
    assert active_grant_contract["latest_execution_status"] == "not_run_by_this_packet"
    assert "not evidence that the smoke command was executed by this packet" in active_grant_contract["not_signoff"]
    assert "not proof of a live desktop-to-mobile remote input session" in active_grant_contract["not_signoff"]
    assert (
        "not backend TestClient, desktop smoke, packaged, or clean-machine evidence by itself"
        in active_grant_contract["not_signoff"]
    )
    assert "Mobile remote-input active-grant contract: fail_closed_source_contract_present" in markdown_text
    assert "latest_execution=not_run_by_this_packet" in markdown_text
    assert "not_signoff=source/client contract only, not live device/WSS" in markdown_text
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["result"]
        == "ready_for_manual_real_device_collection_only"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["real_device_evidence_status"]
        == "uncollected_fail_closed"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["real_device_evidence_collected"]
        is False
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["no_phone_preflight_claim"]
        == "not_real_device_pass"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["backend"]["public_base_url_redacted"]
        == "https://[redacted-host]:9443"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["backend"][
            "websocket_remote_screen_url_redacted"
        ]
        == "wss://[redacted-host]:9443/ws/remote/screen"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"][
            "transport_security_status"
        ]
        == "https_ready_preflight"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"][
            "websocket_approvals_url_redacted"
        ]
        == "wss://[redacted-host]:9443/ws/mobile/approvals"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"][
            "websocket_remote_screen_url_redacted"
        ]
        == "wss://[redacted-host]:9443/ws/remote/screen"
    )
    assert (
        packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"][
            "websocket_remote_input_url_redacted"
        ]
        == "wss://[redacted-host]:9443/ws/remote/input"
    )
    mobile_template = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"][
        "manual_real_device_evidence_template"
    ]
    assert mobile_template["real_device_result"] == "uncollected"
    assert mobile_template["real_device_evidence_collected"] is False
    assert mobile_template["may_be_recorded_as"] == "preflight/config evidence only"
    assert mobile_template["must_not_be_recorded_as"] == "real-device pass evidence"
    assert mobile_template["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert mobile_template["artifact_collection_rules"]["review_required_before_pass_claim"] is True
    assert mobile_template["fields"]["camera_qr_path_evidence"] == "uncollected"
    assert mobile_template["fields"]["actual_device_https_wss_evidence"] == "uncollected"
    assert mobile_template["fields"]["remote_input_grant_revoke_evidence"] == "uncollected"
    assert mobile_template["fields"]["remote_input_grant_expiry_evidence"] == "uncollected"
    assert mobile_template["collection_checklist_statuses"] == {
        "camera_qr": "uncollected",
        "actual_https_wss": "uncollected",
        "approval_wss": "uncollected",
        "remote_screen_wss": "uncollected",
        "remote_input_wss": "uncollected",
        "certificate_trust": "uncollected",
        "remote_input_grant_revoke_expiry": "uncollected",
        "screenshot_log_review": "uncollected",
    }
    android_gate = packet["evidence"]["android_release_gate"]
    android_latest = android_gate["latest_redacted_summary"]
    assert android_gate["status"] == "entry_available"
    assert android_gate["expected_preflight_status"] == "preflight_ready_not_release"
    assert android_gate["expected_strict_status"] == "passed"
    assert android_gate["expected_packet_creates_apk_or_real_device_pass"] is False
    assert android_latest["found"] is False
    assert android_latest["status"] == "not_collected_by_this_packet"
    assert android_latest["release_ready"] is False
    assert android_latest["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert android_latest["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert "preflight is not an APK build" in android_gate["not_signoff"]
    assert (
        "strict gate remains blocked without installable APK and reviewed real-device evidence"
        in android_gate["not_signoff"]
    )
    assert "Android release gate: entry_available; latest status=not_collected_by_this_packet" in markdown_text
    assert "not an APK/install/WSS pass created by this packet" in markdown_text
    assert packet["evidence"]["ollama_local_model_contracts"]["contract_count"] == expected_ollama_contracts
    assert packet["evidence"]["ollama_local_model_contracts"]["latest_execution_status"] == "not_run_by_this_packet"
    local_model_template = packet["evidence"]["local_model_clean_machine_template"][
        "latest_redacted_clean_machine_template"
    ]
    assert local_model_template["found"] is False
    assert local_model_template["clean_machine_signoff"] is False
    assert local_model_template["local_model_install_pass"] is False
    assert local_model_template["local_model_start_pass"] is False
    assert local_model_template["local_model_pull_pass"] is False
    assert local_model_template["local_model_task_smoke_pass"] is False
    assert local_model_template["template_is_clean_machine_pass"] is False
    assert local_model_template["dev_smoke_is_clean_machine_pass"] is False
    assert (
        packet["evidence"]["diagnostics_external_review"]["expected_external_review_status"] == "manual_review_required"
    )
    assert packet["evidence"]["diagnostics_external_review"]["expected_public_safe"] is False
    assert packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["found"] is False
    assert packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["public_safe"] is False
    assert (
        packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["external_sharing_allowed"]
        is False
    )
    assert packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["found"] is False
    assert (
        packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["result_quality_signoff"] is False
    )
    assert (
        packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["completed_result_evidence"]
        is False
    )
    assert packet["evidence"]["settings_local_model_smoke"]["present_artifact_count"] == 4
    assert "It does not create installable Android APK pass or real-device Android remote-control pass" in "\n".join(
        packet["not_clean_machine_or_signoff"]
    )
    assert "not release-candidate sign-off" in "\n".join(packet["not_clean_machine_or_signoff"]).lower()
    assert "It is not release-candidate sign-off" in markdown_text


def test_release_evidence_packet_consumes_android_preflight_without_release_claims(
    project_root: Path, tmp_path: Path
) -> None:
    android_root = tmp_path / "android-release-gate"
    _write_android_release_gate_summary(android_root, _android_release_gate_summary())

    evidence_root = tmp_path / "release-evidence-packet"
    result = _run_release_evidence_packet_with_android_gate(
        project_root,
        tmp_path,
        evidence_root,
        android_root,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert str(tmp_path) not in output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    packet = json.loads(packet_text)
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {item["id"]: item for item in packet["release_readiness_blockers"]}["android_installable_remote_control"]

    assert latest["found"] is True
    assert latest["source_contract_status"] == "valid_redacted_summary"
    assert latest["status"] == "preflight_ready_not_release"
    assert latest["release_ready"] is False
    assert latest["preflight_only"] is True
    assert latest["android_artifact"]["provided"] is False
    assert latest["android_artifact"]["installable_apk"] is False
    assert latest["artifact_gate_evaluated"] is False
    assert latest["artifact_gate_passed"] is False
    assert latest["real_device_gate_evaluated"] is False
    assert latest["real_device_gate_passed"] is False
    assert latest["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert latest["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert "installable Android app release pass" in latest["must_not_claim"]
    assert "real-device Android remote-control pass" in latest["must_not_claim"]
    assert blocker["status"] == "missing_apk_or_real_device_gate"
    assert blocker["claim_allowed"] is False
    assert packet["summary"]["release_ready"] is False
    assert packet["summary"]["claimable_release_signoff"] is False
    assert "Android release gate: entry_available; latest status=preflight_ready_not_release" in markdown_text
    assert "preflight_only=True" in markdown_text
    assert "not an APK/install/WSS pass created by this packet" in markdown_text


def test_release_evidence_packet_indexes_strict_android_gate_without_release_signoff(
    project_root: Path, tmp_path: Path
) -> None:
    android_root = tmp_path / "android-release-gate"
    _write_android_release_gate_summary(
        android_root,
        _android_release_gate_summary(
            status="passed",
            release_ready=True,
            preflight_only=False,
            installable_claim_allowed=True,
            remote_claim_allowed=True,
            artifact_provided=True,
            artifact_label="Lengrvis-preview.apk",
            artifact_bytes=2_000_000,
            installable_apk=True,
            apk_zip_header_valid=True,
            artifact_gate_evaluated=True,
            artifact_gate_passed=True,
            real_device_gate_evaluated=True,
            real_device_gate_passed=True,
            real_device_evidence_label="android-real-device-evidence.redacted.json",
            must_not_claim=[],
        ),
    )

    evidence_root = tmp_path / "release-evidence-packet"
    result = _run_release_evidence_packet_with_android_gate(
        project_root,
        tmp_path,
        evidence_root,
        android_root,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {item["id"]: item for item in packet["release_readiness_blockers"]}["android_installable_remote_control"]

    assert latest["source_contract_status"] == "valid_redacted_summary"
    assert latest["status"] == "passed"
    assert latest["release_ready"] is True
    assert latest["preflight_only"] is False
    assert latest["android_artifact"]["label"] == "Lengrvis-preview.apk"
    assert latest["android_artifact"]["installable_apk"] is True
    assert latest["android_artifact"]["apk_zip_header_valid"] is True
    assert latest["artifact_gate_passed"] is True
    assert latest["real_device_gate_passed"] is True
    assert latest["claim_controls"]["installable_android_app_claim_allowed"] is True
    assert latest["claim_controls"]["real_device_remote_control_claim_allowed"] is True
    assert blocker["status"] == "recorded_by_android_release_gate"
    assert blocker["claim_allowed"] is True
    assert packet["summary"]["packet_is_pass"] is False
    assert packet["summary"]["release_ready"] is False
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["claimable_release_signoff"] is False
    assert "It does not create installable Android APK pass or real-device Android remote-control pass" in "\n".join(
        packet["not_clean_machine_or_signoff"]
    )


def test_release_evidence_packet_rejects_forged_passed_android_gate_summary(project_root: Path, tmp_path: Path) -> None:
    android_root = tmp_path / "android-release-gate"
    forged_summary = _android_release_gate_summary(
        status="passed",
        release_ready=True,
        preflight_only=False,
        installable_claim_allowed=True,
        remote_claim_allowed=True,
        artifact_provided=True,
        artifact_label="Lengrvis-preview.apk",
        artifact_bytes=512,
        installable_apk=True,
        apk_zip_header_valid=True,
        artifact_gate_evaluated=True,
        artifact_gate_passed=True,
        real_device_gate_evaluated=True,
        real_device_gate_passed=True,
        real_device_evidence_label="android-real-device-evidence.redacted.json",
        must_not_claim=[],
    )
    forged_summary["generated_by"] = "manual-forged-summary"
    forged_summary["generated_at_utc"] = "not-a-timestamp"
    forged_summary["android_artifact"]["sha256"] = "not-a-sha256"
    forged_summary["artifact_gate"]["issues"] = [
        {"code": "forged_artifact_issue", "message": "should fail closed"},
    ]
    _write_android_release_gate_summary(android_root, forged_summary)

    evidence_root = tmp_path / "release-evidence-packet"
    result = _run_release_evidence_packet_with_android_gate(
        project_root,
        tmp_path,
        evidence_root,
        android_root,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {item["id"]: item for item in packet["release_readiness_blockers"]}["android_installable_remote_control"]
    mismatch_reasons = "\n".join(latest["mismatch_reasons"])

    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert latest["source_contract_status"] == "source_contract_mismatch"
    assert latest["status"] == "source_contract_mismatch"
    assert latest["release_ready"] is False
    assert latest["android_artifact"]["provided"] is False
    assert latest["android_artifact"]["bytes"] == 0
    assert latest["artifact_gate_passed"] is False
    assert latest["real_device_gate_passed"] is False
    assert latest["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert latest["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert blocker["status"] == "missing_apk_or_real_device_gate"
    assert blocker["claim_allowed"] is False
    assert "generated_by is not scripts/verify_android_release_gate.ps1" in mismatch_reasons
    assert "generated_at_utc is not a UTC timestamp" in mismatch_reasons
    assert "passed Android gate must include a 64-character android_artifact.sha256" in mismatch_reasons
    assert "passed Android gate must include an Android artifact of at least 1 MiB" in mismatch_reasons
    assert "passed Android gate must have no artifact_gate issues" in mismatch_reasons


def test_release_evidence_packet_fail_closes_android_gate_overclaim_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    android_root = tmp_path / "android-release-gate"
    _write_android_release_gate_summary(
        android_root,
        _android_release_gate_summary(
            status="preflight_ready_not_release",
            release_ready=True,
            preflight_only=True,
            installable_claim_allowed=True,
            remote_claim_allowed=True,
            artifact_provided=True,
            artifact_label="C:/Users/alice/private-token.apk",
            artifact_bytes=2_000_000,
            installable_apk=True,
            apk_zip_header_valid=True,
            artifact_gate_evaluated=True,
            artifact_gate_passed=True,
            real_device_gate_evaluated=True,
            real_device_gate_passed=True,
            real_device_evidence_label="C:/Users/alice/private-evidence.json",
            must_not_claim=[],
        ),
    )

    evidence_root = tmp_path / "release-evidence-packet"
    result = _run_release_evidence_packet_with_android_gate(
        project_root,
        tmp_path,
        evidence_root,
        android_root,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert str(tmp_path) not in output
    assert "private-token.apk" not in output
    assert "private-evidence.json" not in output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {item["id"]: item for item in packet["release_readiness_blockers"]}["android_installable_remote_control"]
    mismatch_reasons = "\n".join(latest["mismatch_reasons"])

    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert latest["source_contract_status"] == "source_contract_mismatch"
    assert latest["status"] == "source_contract_mismatch"
    assert latest["release_ready"] is False
    assert latest["preflight_only"] is False
    assert latest["android_artifact"]["provided"] is False
    assert latest["android_artifact"]["bytes"] == 0
    assert latest["android_artifact"]["installable_apk"] is False
    assert latest["android_artifact"]["apk_zip_header_valid"] is False
    assert latest["artifact_gate_passed"] is False
    assert latest["real_device_gate_passed"] is False
    assert latest["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert latest["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert blocker["status"] == "missing_apk_or_real_device_gate"
    assert blocker["claim_allowed"] is False
    assert "non-passed Android gate must not set release_ready=true" in mismatch_reasons
    assert "non-passed Android gate must not allow installable Android app claims" in mismatch_reasons
    assert "non-passed Android gate must not allow real-device remote-control claims" in mismatch_reasons
    assert (
        "non-passed Android gate must include installable Android app release pass in must_not_claim"
        in mismatch_reasons
    )
    assert (
        "non-passed Android gate must include real-device Android remote-control pass in must_not_claim"
        in mismatch_reasons
    )
    assert "preflight Android gate must not evaluate artifact_gate" in mismatch_reasons
    assert "preflight Android gate must not set artifact_gate.passed=true" in mismatch_reasons
    assert "preflight Android gate must not evaluate real_device_gate" in mismatch_reasons
    assert "preflight Android gate must not set real_device_gate.passed=true" in mismatch_reasons


def test_release_evidence_packet_consumes_rc_handoff_template_without_signoff(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    rc_root = tmp_path / "rc-handoff-template"
    rc_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_rc_handoff_template.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(rc_root),
            "-CandidateCommit",
            "abc123def456",
            "-BuildId",
            "build-2026.06.09",
            "-Platform",
            "windows-x64",
            "-ArtifactLabel",
            "Lengrvis-win-portable.zip",
            "-GateCommand",
            "npm run qa:gate;;npm run release:check",
            "-GateExit",
            "exit 0;;exit 0",
            "-StrictStateSource",
            "strict-state-machine",
            "-ManualP1Check",
            "manual P1 reviewed by release owner",
            "-Waiver",
            "none",
            "-ResidualRisk",
            "residual risk reviewed by release owner",
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    assert rc_result.returncode == 0, rc_result.stdout + rc_result.stderr

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            str(tmp_path / "empty-android-release-gate"),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-RcHandoffEvidenceRoot",
            str(rc_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    assert str(tmp_path) not in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    packet = json.loads(packet_text)
    latest = packet["evidence"]["rc_handoff_template"]["latest_redacted_handoff_template"]
    assert latest["found"] is True
    assert latest["marker"] == "NOT_RELEASE_CANDIDATE_SIGNOFF"
    assert latest["source_contract_status"] == "valid_not_signoff_template"
    assert latest["mismatch_reasons"] == []
    assert latest["handoff_status"] == "manual_rc_handoff_recorded_unverified_by_this_helper"
    assert latest["required_fields_recorded"] is True
    assert latest["missing_required_fields_count"] == 0
    assert latest["artifact_label_count"] == 1
    assert latest["gate_result_count"] == 2
    assert latest["manual_p1_check_count"] == 1
    assert latest["waiver_count"] == 1
    assert latest["residual_risk_count"] == 1
    assert latest["release_candidate_signoff"] is False
    assert latest["claim_allowed"] is False
    assert latest["gate_commands_run_by_this_helper"] is False
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["claimable_release_signoff"] is False
    assert "Latest RC handoff template: found=True" in markdown_text
    assert "required_fields_recorded=True" in markdown_text
    assert "RC handoff template: found=True" in markdown_text


def test_release_evidence_packet_fail_closes_malformed_rc_handoff_artifact(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    rc_root = tmp_path / "rc-handoff-template"
    run_root = rc_root / "run-malformed"
    run_root.mkdir(parents=True)
    (run_root / "rc-handoff-template.redacted.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "scripts/collect_rc_handoff_template.ps1",
                "marker": "sk-proj-RCSECRET1234",
                "summary": {
                    "status": "release_candidate_passed",
                    "release_candidate_signoff": True,
                    "claim_allowed": True,
                    "template_is_rc_pass": True,
                    "template_is_release_signoff": True,
                    "gate_commands_run_by_this_helper": True,
                    "missing_required_fields_count": "0",
                    "missing_required_fields": [],
                },
                "signoff_controls": {
                    "release_candidate_signoff": True,
                    "claim_allowed": True,
                    "pass_defaults_remain_false": False,
                    "must_not_tag_publish_or_announce": False,
                },
                "readonly_scope": {
                    "starts_product_processes": True,
                    "runs_release_commands": True,
                    "performs_network_requests": True,
                    "installs_dependencies": True,
                    "writes_only_rc_handoff_template_artifacts": False,
                },
                "candidate": {
                    "commit": r"C:\Users\Suli\Contoso\candidate?token=rc-secret",
                    "build_id": "build-token=rc-secret",
                    "platform": "windows-x64",
                    "commit_or_build_id_status": "recorded_unverified_by_this_helper",
                },
                "artifacts": {"labels": []},
                "gate_results": {
                    "exact_commands_and_exits_required": False,
                    "commands_run_by_this_helper": True,
                    "entries": [{"pass_verified_by_this_helper": True}],
                },
                "manual_p1_checks": {"entries": []},
                "waivers": {"entries": []},
                "residual_risks": {"entries": []},
                "must_not_be_recorded_as": [],
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-RcHandoffEvidenceRoot",
            str(rc_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest RC handoff helper artifact failed fail-closed validation" in release_output
    assert str(tmp_path) not in release_output
    assert "sk-proj-RCSECRET1234" not in release_output
    assert "rc-secret" not in release_output
    assert "Contoso" not in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    assert "sk-proj-RCSECRET1234" not in packet_text
    assert "rc-secret" not in packet_text
    assert "Contoso" not in packet_text
    packet = json.loads(packet_text)
    latest = packet["evidence"]["rc_handoff_template"]["latest_redacted_handoff_template"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["release_candidate_signoff"] is False
    assert latest["source_contract_status"] == "source_contract_mismatch"
    assert latest["marker"] == "invalid_redacted"
    assert latest["handoff_status"] == "invalid_redacted"
    assert latest["release_candidate_signoff"] is False
    assert latest["claim_allowed"] is False
    assert latest["required_fields_recorded"] is False
    mismatch_reasons = "\n".join(latest["mismatch_reasons"])
    assert "marker is missing or not NOT_RELEASE_CANDIDATE_SIGNOFF" in mismatch_reasons
    assert "summary.status is not a recognized non-signoff RC handoff status" in mismatch_reasons
    assert "summary.release_candidate_signoff is not false" in mismatch_reasons
    assert "summary.claim_allowed is not false" in mismatch_reasons
    assert "summary.gate_commands_run_by_this_helper is not false" in mismatch_reasons
    assert "signoff_controls.must_not_tag_publish_or_announce is not true" in mismatch_reasons
    assert "readonly_scope.runs_release_commands is not false" in mismatch_reasons
    assert "gate_results.commands_run_by_this_helper is not false" in mismatch_reasons
    assert "gate_results.entries.pass_verified_by_this_helper is not false" in mismatch_reasons


def test_release_evidence_packet_fail_closes_unredacted_mobile_preflight_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-malformed-mobile"
    mobile_run.mkdir(parents=True)
    (mobile_run / "evidence-summary.redacted.json").write_text(
        json.dumps(
            {
                "result": "ready_for_manual_real_device_collection_only",
                "generated_at_utc": "2026-06-08T00:00:00.0000000Z",
                "backend": {
                    "host_redacted": "10.0.0.42",
                    "public_base_url_redacted": "https://10.0.0.42:9443?token=mobile-secret",
                    "websocket_approvals_url_redacted": "wss://10.0.0.42:9443/ws/mobile/approvals?token=mobile-secret",
                    "websocket_remote_screen_url_redacted": "wss://10.0.0.42:9443/ws/remote/screen?token=mobile-secret",
                    "websocket_remote_input_url_redacted": "wss://10.0.0.42:9443/ws/remote/input?token=mobile-secret",
                },
                "lan_tls": {
                    "enabled": "false",
                    "tls_material_valid": "false",
                    "tls_host_valid": "false",
                },
                "qr_payload_shape": {
                    "transport_security_status": "https_ready_preflight",
                    "transport_security_tls_ready": "false",
                    "websocket_approvals_url_redacted": "wss://10.0.0.42:9443/ws/mobile/approvals?token=mobile-secret",
                    "websocket_remote_screen_url_redacted": "wss://10.0.0.42:9443/ws/remote/screen?token=mobile-secret",
                    "websocket_remote_input_url_redacted": "wss://10.0.0.42:9443/ws/remote/input?token=mobile-secret",
                },
                "issues": ["token=mobile-secret"],
                "warnings": ["private host 10.0.0.42"],
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "latest mobile LAN/WSS preflight artifact failed redacted contract validation" in output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((output, packet_text, markdown_text))
    for raw in ("10.0.0.42", "mobile-secret", "?token="):
        assert raw not in combined

    packet = json.loads(packet_text)
    mobile_summary = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["packet_is_pass"] is False
    assert mobile_summary["source_contract_status"] == "source_contract_mismatch"
    assert mobile_summary["result"] == "source_contract_mismatch"
    assert mobile_summary["backend"] == {
        "host_redacted": "invalid_redacted",
        "public_base_url_redacted": "invalid_redacted",
        "websocket_approvals_url_redacted": "invalid_redacted",
        "websocket_remote_screen_url_redacted": "invalid_redacted",
        "websocket_remote_input_url_redacted": "invalid_redacted",
    }
    assert mobile_summary["lan_tls"] == {
        "enabled": False,
        "tls_material_valid": False,
        "tls_host_valid": False,
    }
    assert mobile_summary["qr_payload_shape"]["transport_security_status"] == "source_contract_mismatch"
    assert mobile_summary["qr_payload_shape"]["transport_security_tls_ready"] is False
    assert mobile_summary["qr_payload_shape"]["websocket_approvals_url_redacted"] == "invalid_redacted"
    assert mobile_summary["qr_payload_shape"]["websocket_remote_screen_url_redacted"] == "invalid_redacted"
    assert mobile_summary["qr_payload_shape"]["websocket_remote_input_url_redacted"] == "invalid_redacted"
    mismatch_reasons = "\n".join(mobile_summary["mismatch_reasons"])
    assert "backend.host_redacted is not a safe redacted host label" in mismatch_reasons
    assert "backend.public_base_url_redacted is not a safe redacted origin" in mismatch_reasons
    assert "backend.websocket_approvals_url_redacted is not a safe redacted websocket URL" in mismatch_reasons
    assert "backend.websocket_remote_screen_url_redacted is not a safe redacted websocket URL" in mismatch_reasons
    assert "qr_payload_shape.websocket_approvals_url_redacted is not a safe redacted websocket URL" in mismatch_reasons
    assert (
        "qr_payload_shape.websocket_remote_screen_url_redacted is not a safe redacted websocket URL" in mismatch_reasons
    )
    assert (
        "qr_payload_shape.websocket_remote_input_url_redacted is not a safe redacted websocket URL" in mismatch_reasons
    )
    assert "lan_tls.enabled is not a JSON boolean" in mismatch_reasons
    assert "qr_payload_shape.transport_security_tls_ready is not a JSON boolean" in mismatch_reasons


def test_release_evidence_packet_fail_closes_unparseable_mobile_preflight_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-unparseable-mobile"
    mobile_run.mkdir(parents=True)
    (mobile_run / "evidence-summary.redacted.json").write_text(
        '{"result": "ready_for_manual_real_device_collection_only",',
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "latest mobile LAN/WSS preflight artifact could not be parsed" in output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    mobile_summary = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["packet_is_pass"] is False
    assert mobile_summary["source_contract_status"] == "source_contract_mismatch"
    assert mobile_summary["parse_error"] == "latest JSON artifact could not be parsed"
    assert mobile_summary["result"] == "source_contract_mismatch"


def test_release_evidence_packet_fail_closes_ready_mobile_preflight_missing_redacted_urls(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-ready-missing-urls"
    mobile_run.mkdir(parents=True)
    (mobile_run / "evidence-summary.redacted.json").write_text(
        json.dumps(
            {
                "result": "ready_for_manual_real_device_collection_only",
                "generated_at_utc": "2026-06-08T00:00:00.0000000Z",
                "backend": {
                    "host_redacted": "[redacted-host]",
                    "public_base_url_redacted": "",
                    "websocket_approvals_url_redacted": "",
                    "websocket_remote_screen_url_redacted": "",
                    "websocket_remote_input_url_redacted": "",
                },
                "lan_tls": {
                    "enabled": True,
                    "tls_material_valid": True,
                    "tls_host_valid": True,
                },
                "qr_payload_shape": {
                    "transport_security_status": "https_ready_preflight",
                    "transport_security_tls_ready": True,
                    "websocket_approvals_url_redacted": "",
                    "websocket_remote_screen_url_redacted": "",
                    "websocket_remote_input_url_redacted": "",
                },
                "issues": [],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "latest mobile LAN/WSS preflight artifact failed redacted contract validation" in output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    mobile_summary = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert mobile_summary["source_contract_status"] == "source_contract_mismatch"
    assert mobile_summary["result"] == "source_contract_mismatch"
    assert mobile_summary["backend"]["public_base_url_redacted"] == "invalid_redacted"
    assert mobile_summary["backend"]["websocket_approvals_url_redacted"] == "invalid_redacted"
    assert mobile_summary["backend"]["websocket_remote_screen_url_redacted"] == "invalid_redacted"
    assert mobile_summary["backend"]["websocket_remote_input_url_redacted"] == "invalid_redacted"
    assert mobile_summary["qr_payload_shape"]["websocket_approvals_url_redacted"] == "invalid_redacted"
    assert mobile_summary["qr_payload_shape"]["websocket_remote_screen_url_redacted"] == "invalid_redacted"
    assert mobile_summary["qr_payload_shape"]["websocket_remote_input_url_redacted"] == "invalid_redacted"
    mismatch_reasons = "\n".join(mobile_summary["mismatch_reasons"])
    assert "ready mobile preflight is missing backend.public_base_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing backend.websocket_approvals_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing backend.websocket_remote_screen_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing backend.websocket_remote_input_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing qr_payload_shape.websocket_approvals_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing qr_payload_shape.websocket_remote_screen_url_redacted" in mismatch_reasons
    assert "ready mobile preflight is missing qr_payload_shape.websocket_remote_input_url_redacted" in mismatch_reasons


def test_release_evidence_packet_fail_closes_ready_mobile_preflight_missing_qr_remote_screen_url(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-ready-missing-qr-screen"
    mobile_run.mkdir(parents=True)
    mobile_summary = _mobile_lan_wss_preflight_summary()
    mobile_summary["qr_payload_shape"]["websocket_remote_screen_url_redacted"] = ""
    (mobile_run / "evidence-summary.redacted.json").write_text(
        json.dumps(mobile_summary),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "latest mobile LAN/WSS preflight artifact failed redacted contract validation" in output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    latest = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]
    assert latest["source_contract_status"] == "source_contract_mismatch"
    assert latest["backend"]["websocket_remote_screen_url_redacted"] == "wss://[redacted-host]:9443/ws/remote/screen"
    assert latest["qr_payload_shape"]["websocket_remote_screen_url_redacted"] == "invalid_redacted"
    mismatch_reasons = "\n".join(latest["mismatch_reasons"])
    assert "ready mobile preflight is missing qr_payload_shape.websocket_remote_screen_url_redacted" in mismatch_reasons


def test_release_evidence_packet_fail_closes_mobile_preflight_with_collected_real_device_checklist(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    mobile_root = tmp_path / "mobile-lan-wss-preflight"
    mobile_run = mobile_root / "run-collected-checklist"
    mobile_run.mkdir(parents=True)
    mobile_summary = _mobile_lan_wss_preflight_summary()
    mobile_summary["manual_real_device_evidence_template"]["real_device_collection_checklist"]["remote_input_wss"][
        "status"
    ] = "collected"
    (mobile_run / "evidence-summary.redacted.json").write_text(
        json.dumps(mobile_summary),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "latest mobile LAN/WSS preflight artifact failed redacted contract validation" in output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    latest = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert latest["source_contract_status"] == "source_contract_mismatch"
    assert latest["result"] == "source_contract_mismatch"
    assert (
        latest["manual_real_device_evidence_template"]["collection_checklist_statuses"]["remote_input_wss"]
        == "invalid_redacted"
    )
    mismatch_reasons = "\n".join(latest["mismatch_reasons"])
    assert (
        "manual_real_device_evidence_template.real_device_collection_checklist.remote_input_wss.status is not uncollected"
        in mismatch_reasons
    )


def test_release_evidence_packet_redacts_sensitive_evidence_root_labels(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    raw_root_label = "mobile-192.168.56.10-token=mobile-secret-client_secret=client-secret"
    mobile_root = tmp_path / raw_root_label
    mobile_root.mkdir()
    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    result = subprocess.run(
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
            str(mobile_root),
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((output, packet_text, markdown_text))
    for raw in (
        "192.168.56.10",
        "mobile-secret",
        "client-secret",
        "token=mobile-secret",
        "client_secret=client-secret",
    ):
        assert raw not in combined
    assert "[redacted-host]" in packet_text
    assert "[redacted-sensitive]=[redacted]" in packet_text


def test_release_evidence_packet_consumes_local_model_clean_machine_template(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    local_model_root = tmp_path / "local-model-clean-machine-evidence"
    runtime_source = tmp_path / "runtime" / "Contoso-token-secret-sk-proj-RUNTIME1234.exe"
    artifact_path = tmp_path / "qa artifacts" / "Contoso-token=artifact-secret-sk-proj-ARTIFACT1234.log"
    template_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(local_model_root),
            "-Candidate",
            "rc-Contoso-token-secret-sk-proj-CANDIDATE1234",
            "-EvidenceMode",
            "clean-machine",
            "-Platform",
            "Windows x64",
            "-ArtifactUnderTest",
            str(tmp_path / "dist" / "mavris-local-model-candidate.exe"),
            "-BuildIdentifier",
            "build-2026.06.08",
            "-ProfileUnderTest",
            r"C:\Users\Suli\clean-machine-profile",
            "-Runtime",
            "Ollama",
            "-RuntimeVersion",
            "0.5.7",
            "-RuntimeSource",
            str(runtime_source),
            "-Model",
            "qwen2.5:3b",
            "-ModelVersion",
            "sha256:abc123",
            "-ModelSource",
            "https://models.example.test/Contoso/token=local-model-secret/private-model",
            "-InstallOutcome",
            "Install outcome recorded with token=install-secret",
            "-StartOutcome",
            "Start outcome recorded with token=start-secret",
            "-PullOutcome",
            "Pull outcome recorded with token=pull-secret",
            "-TaskSmokeOutcome",
            "Task smoke outcome recorded with token=task-secret",
            "-BlockedReason",
            "Manual run blocked by token=local-model-secret",
            "-Artifact",
            str(artifact_path),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    template_output = template_result.stdout + template_result.stderr
    assert template_result.returncode == 0, template_output

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-RcHandoffEvidenceRoot",
            str(tmp_path / "empty-rc-handoff-template"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(local_model_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((template_output, release_output, packet_text, markdown_text))
    for raw in (
        str(tmp_path),
        "Contoso",
        "token-secret",
        "sk-proj-CANDIDATE1234",
        "sk-proj-RUNTIME1234",
        "sk-proj-ARTIFACT1234",
        "artifact-secret",
        "local-model-secret",
        "install-secret",
        "start-secret",
        "pull-secret",
        "task-secret",
        "private-model",
        "models.example.test",
    ):
        assert raw not in combined

    packet = json.loads(packet_text)
    local_summary = packet["evidence"]["local_model_clean_machine_template"]["latest_redacted_clean_machine_template"]
    assert packet["summary"]["source_contract_failures"] == 0
    assert packet["summary"]["clean_machine_signoff"] is False
    assert packet["summary"]["local_model_install_pass"] is False
    assert packet["summary"]["local_model_start_pass"] is False
    assert packet["summary"]["local_model_pull_pass"] is False
    assert packet["summary"]["local_model_task_smoke_pass"] is False
    assert packet["summary"]["template_is_clean_machine_pass"] is False
    assert packet["summary"]["dev_smoke_is_clean_machine_pass"] is False
    assert local_summary["found"] is True
    assert local_summary["marker"] == "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    assert local_summary["source_contract_status"] == "valid_not_signoff_template"
    assert local_summary["mismatch_reasons"] == []
    assert local_summary["template_status"] == "blocked_reason_recorded"
    assert local_summary["clean_machine_signoff"] is False
    assert local_summary["local_model_install_pass"] is False
    assert local_summary["local_model_start_pass"] is False
    assert local_summary["local_model_pull_pass"] is False
    assert local_summary["local_model_task_smoke_pass"] is False
    assert local_summary["real_install_start_pull_pass"] is False
    assert local_summary["template_is_clean_machine_pass"] is False
    assert local_summary["dev_smoke_is_clean_machine_pass"] is False
    assert local_summary["release_candidate_signoff"] is False
    assert local_summary["artifact_build_profile"]["status"] == "recorded_unverified_by_this_helper"
    assert local_summary["artifact_build_profile"]["artifact_under_test"] == "mavris-local-model-candidate.exe"
    assert local_summary["artifact_build_profile"]["build_identifier"] == "build-2026.06.08"
    assert local_summary["artifact_build_profile"]["profile_under_test"] == "clean-machine-profile"
    assert local_summary["runtime"] == {
        "name": "Ollama",
        "version": "0.5.7",
        "status": "unverified_by_this_helper",
    }
    assert local_summary["model"] == {
        "name": "qwen2.5:3b",
        "version": "sha256:abc123",
        "status": "unverified_by_this_helper",
    }
    assert (
        local_summary["clean_machine_run"]["install"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    )
    assert local_summary["clean_machine_run"]["start"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    assert local_summary["clean_machine_run"]["pull"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    assert (
        local_summary["clean_machine_run"]["task_smoke"]["status"]
        == "manual_outcome_recorded_unverified_by_this_helper"
    )
    assert local_summary["clean_machine_run"]["task_smoke"]["clean_machine_pass"] is False
    assert local_summary["missing_required_fields_count"] == 0
    assert local_summary["blocked_reason_count"] == 1
    assert local_summary["observed_artifact_count"] == 1
    assert (
        "not true local model install pass" in packet["evidence"]["local_model_clean_machine_template"]["not_signoff"]
    )
    assert (
        "not true local model task-smoke pass"
        in packet["evidence"]["local_model_clean_machine_template"]["not_signoff"]
    )
    assert (
        "template/dev smoke must not be recorded as clean-machine pass"
        in packet["evidence"]["local_model_clean_machine_template"]["not_signoff"]
    )
    assert "Local model clean-machine template: found=True" in markdown_text
    assert "task_smoke=manual_outcome_recorded_unverified_by_this_helper" in markdown_text


def test_release_evidence_packet_consumes_portable_first_screen_status_log(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[pass] portable renderer DOM read-only task evidence passed: clicked '妫€鏌ョ數鑴戠姸鎬? and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy (log=C:\\Users\\Suli\\Desktop\\mavris\\.tmp\\portable-first-screen-smoke\\run-private\\portable-renderer-dom-evidence.log); no chat/run/task writes and no diagnostics export package after GUI click (tasks=0; runs=0; chat messages=0; diagnostic-packages=0)",
                "[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; backend task/run evidence will be verified separately (log=C:\\Users\\Suli\\Desktop\\mavris\\.tmp\\portable-first-screen-smoke\\run-private\\portable-renderer-natural-language-evidence.log); natural-language prompt created read-only/system diagnostics task task_99963aecac4841d2af25feb2f675c2ad; completion_evidence.level=visible_progress result_verified=false (tasks=1, relatedTasks=1, runs=1, relatedRuns=0, chat messages=0, diagnostic-packages=0)",
                "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked; health=http://127.0.0.1:53913/health; window pid=35416; window title='Lengrvis'; renderer DOM evidence=passed; natural-language renderer DOM evidence=passed",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    assert str(tmp_path) not in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    assert str(tmp_path) not in packet_text
    assert "task_99963aecac4841d2af25feb2f675c2ad" not in packet_text
    assert "task_99963aecac4841d2af25feb2f675c2ad" not in markdown_text

    packet = json.loads(packet_text)
    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert portable_summary["found"] is True
    assert portable_summary["source_contract_status"] == "valid_limited_evidence_log"
    assert portable_summary["first_screen_read_only_pass"] is True
    assert portable_summary["renderer_dom_read_only_evidence"] == "passed"
    assert (
        portable_summary["natural_language_submission_evidence"]
        == "packaged_command_dock_submission_plus_read_only_task_evidence"
    )
    assert portable_summary["observed_post_endpoint"] == "/api/runs"
    assert portable_summary["task_evidence_kind"] == "read_only_system_diagnostics_task_or_run"
    assert portable_summary["read_only_counts"] == {
        "tasks": 0,
        "runs": 0,
        "chat_messages": 0,
        "diagnostic_packages": 0,
    }
    assert portable_summary["natural_language_counts"] == {
        "tasks": 1,
        "related_tasks": 1,
        "runs": 1,
        "related_runs": 0,
        "chat_messages": 0,
        "diagnostic_packages": 0,
    }
    assert portable_summary["natural_language_completion_evidence"] == {
        "level": "visible_progress",
        "result_verified": False,
        "completed_result_evidence": False,
        "signoff": False,
    }
    assert packet["redaction"]["source_artifacts_read_for_summary"] is True
    assert packet["redaction"]["secrets_or_tokens_emitted"] is False
    assert "secrets_or_tokens_read" not in packet["redaction"]
    assert portable_summary["clean_machine_signoff"] is False
    assert portable_summary["completed_task_result_signoff"] is False
    assert portable_summary["release_candidate_signoff"] is False
    assert "not completed task-result sign-off" in packet["evidence"]["portable_first_screen_smoke"]["not_signoff"]
    assert "completion_evidence.level=visible_progress" in markdown_text
    assert "completed_result_evidence=False" in markdown_text
    assert "Portable first-screen smoke: found=True" in markdown_text


def test_release_evidence_packet_records_completed_result_evidence_without_signoff(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[pass] portable renderer DOM read-only task evidence passed: clicked '妫€鏌ョ數鑴戠姸鎬? and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy; no chat/run/task writes and no diagnostics export package after GUI click (tasks=0; runs=0; chat messages=0; diagnostic-packages=0)",
                "[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; natural-language prompt created read-only/system diagnostics task task_99963aecac4841d2af25feb2f675c2ad; completion_evidence.level=completed_result result_verified=true (tasks=1, relatedTasks=1, runs=1, relatedRuns=0, chat messages=0, diagnostic-packages=0)",
                "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked; renderer DOM evidence=passed; natural-language renderer DOM evidence=passed",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")

    packet = json.loads(packet_text)
    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert portable_summary["source_contract_status"] == "valid_limited_evidence_log"
    assert (
        portable_summary["natural_language_submission_evidence"]
        == "packaged_command_dock_submission_plus_read_only_task_evidence"
    )
    assert portable_summary["natural_language_completion_evidence"] == {
        "level": "completed_result",
        "result_verified": True,
        "completed_result_evidence": True,
        "signoff": False,
    }
    assert portable_summary["completed_task_result_signoff"] is False
    assert packet["summary"]["agent_task_completion_signoff"] is False
    assert packet["summary"]["result_quality_signoff"] is False
    assert "completion_evidence.level=completed_result" in markdown_text
    assert "result_verified=True" in markdown_text
    assert "completed_result_evidence=True" in markdown_text
    assert "task_99963aecac4841d2af25feb2f675c2ad" not in packet_text
    assert "task_99963aecac4841d2af25feb2f675c2ad" not in markdown_text


@pytest.mark.parametrize(
    (
        "level",
        "result_verified",
        "signoff",
        "expected_returncode",
        "expected_mismatch",
    ),
    [
        ("completed_result", False, False, 0, ""),
        ("submission", False, False, 0, ""),
        ("task_created", False, False, 0, ""),
        ("visible_progress", False, False, 0, ""),
        ("safe_failure", False, False, 0, ""),
        (
            "visible_progress",
            True,
            False,
            1,
            "natural-language pass line reports result_verified without completed_result level",
        ),
        (
            "completed_result",
            True,
            True,
            1,
            "natural-language pass line must not report completion_evidence signoff",
        ),
    ],
)
def test_release_evidence_packet_does_not_mark_unverified_or_non_completed_levels_as_completed_result_evidence(
    project_root: Path,
    tmp_path: Path,
    level: str,
    result_verified: bool,
    signoff: bool,
    expected_returncode: int,
    expected_mismatch: str,
) -> None:
    result_verified_text = str(result_verified).lower()
    signoff_text = str(signoff).lower()
    release_result, evidence_root = _run_release_evidence_packet_for_portable_status_log(
        project_root,
        tmp_path,
        [
            "[pass] portable renderer DOM read-only task evidence passed: clicked 'status' and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy; no chat/run/task writes and no diagnostics export package after GUI click (tasks=0; runs=0; chat messages=0; diagnostic-packages=0)",
            f"[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; natural-language prompt created read-only/system diagnostics task task_completion; completion_evidence.level={level} result_verified={result_verified_text} signoff={signoff_text} (tasks=1, relatedTasks=1, runs=1, relatedRuns=0, chat messages=0, diagnostic-packages=0)",
            "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked; renderer DOM evidence=passed; natural-language renderer DOM evidence=passed",
        ],
    )
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == expected_returncode, release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    completion_evidence = portable_summary["natural_language_completion_evidence"]

    assert completion_evidence["level"] == level
    assert completion_evidence["result_verified"] is result_verified
    assert completion_evidence["completed_result_evidence"] is False
    assert completion_evidence["signoff"] is False
    assert portable_summary["completed_task_result_signoff"] is False
    assert packet["summary"]["agent_task_completion_signoff"] is False
    assert packet["summary"]["result_quality_signoff"] is False

    if expected_mismatch:
        assert portable_summary["source_contract_status"] == "source_contract_mismatch"
        assert expected_mismatch in "\n".join(portable_summary["mismatch_reasons"])
    else:
        assert portable_summary["source_contract_status"] == "valid_limited_evidence_log"
        assert portable_summary["mismatch_reasons"] == []


def test_release_evidence_packet_sanitizes_portable_status_log_and_does_not_overclaim(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[info] started portable launcher pid 123; token=portable-secret; temp state C:\\Users\\Suli\\Contoso-token-secret\\private",
                "[unsupported] portable renderer DOM natural-language read-only task evidence unavailable: packaged command dock disabled; read-only entry evidence remains valid but must not be counted as natural-language task evidence",
                "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((release_output, packet_text, markdown_text))
    for raw in (
        str(tmp_path),
        "portable-secret",
        "Contoso",
        "token-secret",
        r"C:\Users\Suli",
    ):
        assert raw not in combined

    packet = json.loads(packet_text)
    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert portable_summary["source_contract_status"] == "limited_or_incomplete_evidence_log"
    assert portable_summary["first_screen_read_only_pass"] is False
    assert portable_summary["renderer_dom_read_only_evidence"] == "unsupported"
    assert portable_summary["natural_language_submission_evidence"] == "unsupported"
    assert portable_summary["observed_post_endpoint"] == ""
    assert portable_summary["clean_machine_signoff"] is False
    assert portable_summary["completed_task_result_signoff"] is False
    assert portable_summary["release_candidate_signoff"] is False
    assert packet["redaction"]["source_artifacts_read_for_summary"] is True
    assert packet["redaction"]["secrets_or_tokens_emitted"] is False
    assert "secrets_or_tokens_read" not in packet["redaction"]
    assert "completed task-result sign-off" in "\n".join(
        packet["evidence"]["portable_first_screen_smoke"]["not_signoff"]
    )


def test_release_evidence_packet_fail_closes_empty_portable_status_log(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(portable_root, "")

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest portable first-screen status log could not be read or was empty" in release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["packet_is_pass"] is False
    assert portable_summary["source_contract_status"] == "source_contract_mismatch"
    assert portable_summary["first_screen_read_only_pass"] is False
    assert portable_summary["natural_language_submission_evidence"] == "not_observed"
    assert portable_summary["clean_machine_signoff"] is False
    assert portable_summary["completed_task_result_signoff"] is False
    assert portable_summary["release_candidate_signoff"] is False


def test_release_evidence_packet_fail_closes_generic_natural_language_pass_log(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[pass] portable renderer DOM read-only task evidence passed: clicked '妫€鏌ョ數鑴戠姸鎬? and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy; no chat/run/task writes and no diagnostics export package after GUI click (tasks=0; runs=0; chat messages=0; diagnostic-packages=0)",
                "[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; generic task created without diagnostics relation (tasks=1, runs=1, chat messages=0, diagnostic-packages=0)",
                "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked; renderer DOM evidence=passed; natural-language renderer DOM evidence=passed",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest portable first-screen status log failed limited-evidence validation" in release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["packet_is_pass"] is False
    assert portable_summary["source_contract_status"] == "source_contract_mismatch"
    assert portable_summary["first_screen_read_only_pass"] is False
    assert portable_summary["renderer_dom_read_only_evidence"] == "passed"
    assert portable_summary["natural_language_submission_evidence"] == "source_contract_mismatch"
    assert portable_summary["observed_post_endpoint"] == "/api/runs"
    assert portable_summary["task_evidence_kind"] == "not_observed"
    assert portable_summary["natural_language_counts"] == {
        "tasks": 1,
        "related_tasks": 0,
        "runs": 1,
        "related_runs": 0,
        "chat_messages": 0,
        "diagnostic_packages": 0,
    }
    mismatch_reasons = "\n".join(portable_summary["mismatch_reasons"])
    assert "natural-language pass line does not prove POST plus read-only task/run evidence" in mismatch_reasons
    assert (
        "natural-language pass line does not prove read-only/system diagnostics task or run semantics"
        in mismatch_reasons
    )
    assert (
        "natural-language pass line does not prove related read-only/system diagnostics task or run evidence"
        in mismatch_reasons
    )


def test_release_evidence_packet_fail_closes_natural_language_pass_without_related_counts(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[pass] portable renderer DOM read-only task evidence passed: clicked '妫€鏌ョ數鑴戠姸鎬? and observed packaged renderer /api/system/diagnostics plus read-only diagnostics copy; no chat/run/task writes and no diagnostics export package after GUI click (tasks=0; runs=0; chat messages=0; diagnostic-packages=0)",
                "[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; natural-language prompt created read-only/system diagnostics task task_123 (tasks=1, runs=0, chat messages=0, diagnostic-packages=0)",
                "[pass] portable first-screen/read-only diagnostics smoke passed: read-only diagnostics GET succeeded with scope=local_only, product=Lengrvis, temp data dir confirmed; no export/write endpoint invoked; renderer DOM evidence=passed; natural-language renderer DOM evidence=passed",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )

    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert portable_summary["source_contract_status"] == "source_contract_mismatch"
    assert portable_summary["natural_language_submission_evidence"] == "source_contract_mismatch"
    assert portable_summary["observed_post_endpoint"] == "/api/runs"
    assert portable_summary["task_evidence_kind"] == "not_observed"
    assert portable_summary["natural_language_counts"] == {
        "tasks": 1,
        "related_tasks": 0,
        "runs": 0,
        "related_runs": 0,
        "chat_messages": 0,
        "diagnostic_packages": 0,
    }
    mismatch_reasons = "\n".join(portable_summary["mismatch_reasons"])
    assert "natural-language pass line does not prove POST plus read-only task/run evidence" in mismatch_reasons
    assert (
        "natural-language pass line does not prove related read-only/system diagnostics task or run evidence"
        in mismatch_reasons
    )
    assert (
        "natural-language pass line does not prove read-only/system diagnostics task or run semantics"
        not in mismatch_reasons
    )


def test_release_evidence_packet_fail_closes_malformed_portable_pass_log(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    portable_root = tmp_path / "portable-first-screen-smoke"
    _write_portable_status_log(
        portable_root,
        "\n".join(
            [
                "[pass] portable renderer DOM read-only task evidence passed: clicked '妫€鏌ョ數鑴戠姸鎬? but missing side-effect counts",
                "[pass] portable renderer DOM natural-language read-only task evidence passed: submitted natural-language prompt through packaged command dock and observed expected POST /api/runs; missing backend task/run counts",
                "[fail] portable first-screen/read-only diagnostics smoke errored before readiness was proven token=portable-secret",
            ]
        ),
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)
    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
            "-PortableFirstScreenEvidenceRoot",
            str(portable_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest portable first-screen status log failed limited-evidence validation" in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    combined = "\n".join((release_output, packet_text))
    assert str(tmp_path) not in combined
    assert "portable-secret" not in combined

    packet = json.loads(packet_text)
    portable_summary = packet["evidence"]["portable_first_screen_smoke"]["latest_redacted_status_log"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["packet_is_pass"] is False
    assert portable_summary["source_contract_status"] == "source_contract_mismatch"
    assert portable_summary["first_screen_read_only_pass"] is False
    assert portable_summary["natural_language_submission_evidence"] == "source_contract_mismatch"
    assert portable_summary["clean_machine_signoff"] is False
    assert portable_summary["completed_task_result_signoff"] is False
    assert portable_summary["release_candidate_signoff"] is False
    mismatch_reasons = "\n".join(portable_summary["mismatch_reasons"])
    assert "portable status log contains pass and fail/blocked lines" in mismatch_reasons
    assert "read-only pass line does not prove zero task/run/chat/export side effects" in mismatch_reasons
    assert "natural-language pass line does not prove POST plus read-only task/run evidence" in mismatch_reasons
    assert "portable status log is missing final first-screen pass line" in mismatch_reasons


def test_release_evidence_packet_fail_closes_malformed_local_model_template_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    local_model_root = tmp_path / "local-model-clean-machine-evidence"
    malformed_root = local_model_root / "run-malformed"
    malformed_root.mkdir(parents=True)
    (malformed_root / "local-model-clean-machine-evidence.redacted.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "generated_by": "sk-proj-LOCALMODELSECRET1234",
                "marker": "sk-proj-LOCALMODELSECRET1234",
                "summary": {
                    "template_status": r"C:\Users\Suli\Contoso\ready?token=local-model-secret",
                    "clean_machine_signoff": True,
                    "local_model_install_pass": True,
                    "local_model_start_pass": True,
                    "local_model_pull_pass": True,
                    "real_install_start_pull_pass": True,
                    "release_candidate_signoff": True,
                    "missing_required_fields": [],
                },
                "readonly_scope": {
                    "starts_product_processes": True,
                    "performs_network_requests": True,
                    "installs_runtime": True,
                    "starts_runtime": True,
                    "pulls_models": True,
                    "runs_model_inference": True,
                },
                "redaction": {
                    "raw_logs_included": True,
                    "secrets_or_tokens_read": True,
                    "urls_redacted": False,
                },
                "evidence_template": {
                    "template_status": "clean_machine_local_model_ready",
                    "runtime": {"status": "verified"},
                    "model": {"status": "verified"},
                    "must_not_be_recorded_as": [],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-RcHandoffEvidenceRoot",
            str(tmp_path / "empty-rc-handoff-template"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(local_model_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest local-model clean-machine helper artifact failed fail-closed validation" in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    combined = "\n".join((release_output, packet_text))
    for raw in (
        str(tmp_path),
        "sk-proj-LOCALMODELSECRET1234",
        "local-model-secret",
        "Contoso",
    ):
        assert raw not in combined

    packet = json.loads(packet_text)
    local_summary = packet["evidence"]["local_model_clean_machine_template"]["latest_redacted_clean_machine_template"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["source_contract_failures"] == 1
    assert local_summary["source_contract_status"] == "source_contract_mismatch"
    assert local_summary["marker"] == "invalid_redacted"
    assert local_summary["template_status"] == "invalid_redacted"
    assert local_summary["clean_machine_signoff"] is False
    assert local_summary["local_model_install_pass"] is False
    assert local_summary["local_model_start_pass"] is False
    assert local_summary["local_model_pull_pass"] is False
    assert local_summary["real_install_start_pull_pass"] is False
    assert local_summary["release_candidate_signoff"] is False
    mismatch_reasons = "\n".join(local_summary["mismatch_reasons"])
    assert "marker is missing or not NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS" in mismatch_reasons
    assert "schema_version is not 1" in mismatch_reasons
    assert "generated_by is not the local-model clean-machine helper" in mismatch_reasons
    assert "summary.clean_machine_signoff is not false" in mismatch_reasons
    assert "summary.local_model_install_pass is not false" in mismatch_reasons
    assert "summary.local_model_start_pass is not false" in mismatch_reasons
    assert "summary.local_model_pull_pass is not false" in mismatch_reasons
    assert "summary.real_install_start_pull_pass is not false" in mismatch_reasons
    assert "summary.release_candidate_signoff is not false" in mismatch_reasons
    assert "readonly_scope.starts_product_processes is not false" in mismatch_reasons
    assert "redaction.urls_redacted is not true" in mismatch_reasons
    assert "evidence_template.runtime.status is not unverified_by_this_helper" in mismatch_reasons
    assert "must_not_be_recorded_as is missing true local model install pass" in mismatch_reasons


def test_release_evidence_packet_fail_closes_manual_review_ready_local_model_template_missing_evidence(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    local_model_root = tmp_path / "local-model-clean-machine-evidence"
    malformed_root = local_model_root / "run-manual-ready-missing-evidence"
    malformed_root.mkdir(parents=True)
    (malformed_root / "local-model-clean-machine-evidence.redacted.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "scripts/collect_local_model_clean_machine_evidence_template.ps1",
                "marker": "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS",
                "summary": {
                    "template_status": "manual_review_ready",
                    "clean_machine_signoff": False,
                    "local_model_install_pass": False,
                    "local_model_start_pass": False,
                    "local_model_pull_pass": False,
                    "local_model_task_smoke_pass": False,
                    "real_install_start_pull_pass": False,
                    "template_is_clean_machine_pass": False,
                    "dev_smoke_is_clean_machine_pass": False,
                    "release_candidate_signoff": False,
                    "missing_required_fields_count": 0,
                    "missing_required_fields": [],
                },
                "readonly_scope": {
                    "starts_product_processes": False,
                    "performs_network_requests": False,
                    "installs_runtime": False,
                    "starts_runtime": False,
                    "pulls_models": False,
                    "runs_model_inference": False,
                },
                "redaction": {
                    "raw_logs_included": False,
                    "secrets_or_tokens_read": False,
                    "urls_redacted": True,
                },
                "evidence_template": {
                    "template_status": "manual_clean_machine_local_model_evidence_required",
                    "runtime": {
                        "name": "",
                        "version": "",
                        "status": "unverified_by_this_helper",
                    },
                    "model": {
                        "name": "",
                        "version": "",
                        "status": "unverified_by_this_helper",
                    },
                    "artifact_build_profile": {
                        "status": "recorded_unverified_by_this_helper",
                        "artifact": {
                            "label": "",
                            "status": "unverified_by_this_helper",
                        },
                        "build": {
                            "identifier": "",
                            "status": "unverified_by_this_helper",
                        },
                        "profile": {
                            "label": "",
                            "status": "unverified_by_this_helper",
                        },
                    },
                    "clean_machine_run": {
                        step: {
                            "step": step,
                            "outcome": "uncollected",
                            "status": "blocked_missing_outcome_or_blocked_reason",
                            "blocked_reason_redacted": [],
                            "pass_verified_by_this_helper": False,
                            "clean_machine_pass": False,
                        }
                        for step in ("install", "start", "pull", "task_smoke")
                    },
                    "must_not_be_recorded_as": [
                        "true local model install pass",
                        "true local model start pass",
                        "true local model pull pass",
                        "true local model task-smoke pass",
                        "clean-machine local-model readiness",
                        "template/dev smoke clean-machine pass",
                        "release-candidate sign-off",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-RcHandoffEvidenceRoot",
            str(tmp_path / "empty-rc-handoff-template"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(local_model_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest local-model clean-machine helper artifact failed fail-closed validation" in release_output
    assert str(tmp_path) not in release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    local_summary = packet["evidence"]["local_model_clean_machine_template"]["latest_redacted_clean_machine_template"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["source_contract_failures"] == 1
    assert local_summary["source_contract_status"] == "source_contract_mismatch"
    assert local_summary["marker"] == "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    assert local_summary["template_status"] == "manual_review_ready"
    assert local_summary["clean_machine_signoff"] is False
    assert local_summary["local_model_install_pass"] is False
    assert local_summary["local_model_start_pass"] is False
    assert local_summary["local_model_pull_pass"] is False
    assert local_summary["local_model_task_smoke_pass"] is False
    assert local_summary["real_install_start_pull_pass"] is False
    assert local_summary["template_is_clean_machine_pass"] is False
    assert local_summary["dev_smoke_is_clean_machine_pass"] is False
    assert local_summary["release_candidate_signoff"] is False
    assert local_summary["missing_required_fields_count"] == 0
    mismatch_reasons = "\n".join(local_summary["mismatch_reasons"])
    assert "manual_review_ready local-model template is missing runtime.name" in mismatch_reasons
    assert "manual_review_ready local-model template is missing runtime.version" in mismatch_reasons
    assert "manual_review_ready local-model template is missing model.name" in mismatch_reasons
    assert "manual_review_ready local-model template is missing model.version" in mismatch_reasons
    assert "manual_review_ready local-model template is missing artifact label" in mismatch_reasons
    assert "manual_review_ready local-model template is missing build identifier" in mismatch_reasons
    assert "manual_review_ready local-model template is missing profile label" in mismatch_reasons
    for step in ("install", "start", "pull", "task_smoke"):
        assert (
            f"manual_review_ready local-model template {step} status is not a recorded manual outcome"
            in mismatch_reasons
        )
        assert f"manual_review_ready local-model template {step} outcome is missing" in mismatch_reasons


def test_release_evidence_packet_rejects_string_boolean_local_model_template_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    local_model_root = tmp_path / "local-model-clean-machine-evidence"
    malformed_root = local_model_root / "run-string-booleans"
    malformed_root.mkdir(parents=True)
    (malformed_root / "local-model-clean-machine-evidence.redacted.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_by": "scripts/collect_local_model_clean_machine_evidence_template.ps1",
                "marker": "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS",
                "summary": {
                    "template_status": "manual_review_ready",
                    "clean_machine_signoff": "false",
                    "local_model_install_pass": "false",
                    "local_model_start_pass": "false",
                    "local_model_pull_pass": "false",
                    "real_install_start_pull_pass": "false",
                    "release_candidate_signoff": "false",
                    "missing_required_fields": [],
                },
                "readonly_scope": {
                    "starts_product_processes": "false",
                    "performs_network_requests": "false",
                    "installs_runtime": "false",
                    "starts_runtime": "false",
                    "pulls_models": "false",
                    "runs_model_inference": "false",
                },
                "redaction": {
                    "raw_logs_included": "false",
                    "secrets_or_tokens_read": "false",
                    "urls_redacted": "true",
                },
                "evidence_template": {
                    "template_status": "manual_clean_machine_local_model_evidence_required",
                    "runtime": {"status": "unverified_by_this_helper"},
                    "model": {"status": "unverified_by_this_helper"},
                    "must_not_be_recorded_as": [
                        "true local model install pass",
                        "true local model start pass",
                        "true local model pull pass",
                        "clean-machine local-model readiness",
                        "release-candidate sign-off",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-RcHandoffEvidenceRoot",
            str(tmp_path / "empty-rc-handoff-template"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(local_model_root),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    local_summary = packet["evidence"]["local_model_clean_machine_template"]["latest_redacted_clean_machine_template"]
    assert local_summary["source_contract_status"] == "source_contract_mismatch"
    assert local_summary["marker"] == "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    assert local_summary["template_status"] == "manual_review_ready"
    assert local_summary["clean_machine_signoff"] is False
    assert local_summary["local_model_install_pass"] is False
    assert local_summary["local_model_start_pass"] is False
    assert local_summary["local_model_pull_pass"] is False
    mismatch_reasons = "\n".join(local_summary["mismatch_reasons"])
    assert "summary.clean_machine_signoff is not false" in mismatch_reasons
    assert "summary.local_model_install_pass is not false" in mismatch_reasons
    assert "summary.local_model_start_pass is not false" in mismatch_reasons
    assert "summary.local_model_pull_pass is not false" in mismatch_reasons
    assert "summary.real_install_start_pull_pass is not false" in mismatch_reasons
    assert "summary.release_candidate_signoff is not false" in mismatch_reasons
    assert "readonly_scope.starts_product_processes is not false" in mismatch_reasons
    assert "readonly_scope.performs_network_requests is not false" in mismatch_reasons
    assert "redaction.raw_logs_included is not false" in mismatch_reasons
    assert "redaction.urls_redacted is not true" in mismatch_reasons


def test_release_evidence_packet_consumes_diagnostics_external_review_template(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_package = tmp_path / "diagnostic-packages" / "lengrvis-diagnostics-review.json"
    _write_diagnostics_support_package(diagnostics_package, generated_at="2026-06-09T00:00:00+00:00")
    diagnostics_review_root = tmp_path / "diagnostics-external-review"
    review_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsPackagePath",
            str(diagnostics_package),
            "-EvidenceRoot",
            str(diagnostics_review_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    assert review_result.returncode == 0, review_result.stdout + review_result.stderr

    qa_root = tmp_path / "qa-evidence"
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(diagnostics_review_root),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    assert str(tmp_path) not in release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    review_summary = packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]
    assert review_summary["found"] is True
    assert review_summary["marker"] == "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF"
    assert review_summary["source_contract_status"] == "valid_not_signoff_template"
    assert review_summary["mismatch_reasons"] == []
    assert review_summary["review_status"] == "manual_external_review_template_ready"
    assert review_summary["public_safe"] is False
    assert review_summary["external_sharing_allowed"] is False
    assert review_summary["claim_allowed"] is False
    assert review_summary["human_review_signoff"] is False
    assert review_summary["template_is_human_signoff"] is False
    assert review_summary["review_fields_complete"] is False
    assert review_summary["actual_package_content_review_completed"] is False
    assert review_summary["external_sharing_blocked"] is True
    assert review_summary["separate_human_content_review_required"] is True
    assert review_summary["checklist_count"] >= 6
    assert packet["summary"]["source_contract_failures"] == 0
    assert packet["summary"]["diagnostics_public_safe"] is False


def test_release_evidence_packet_accepts_blocked_diagnostics_review_template(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_review_root = tmp_path / "diagnostics-external-review"
    blocked_root = diagnostics_review_root / "run-blocked"
    blocked_root.mkdir(parents=True)
    (blocked_root / "diagnostics-external-review.redacted.json").write_text(
        json.dumps(
            {
                "marker": "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF",
                "summary": {
                    "status": "blocked_missing_diagnostics_package",
                    "public_safe": False,
                    "required_before_external_sharing": True,
                    "human_review_signoff": False,
                    "external_public_safe_signoff": False,
                    "template_is_human_signoff": False,
                    "external_sharing_allowed": False,
                    "claim_allowed": False,
                    "review_fields_complete": False,
                    "external_sharing_blocked": True,
                    "separate_human_content_review_required": True,
                    "actual_package_content_review_completed": False,
                    "automated_template_only": True,
                    "input_issue_count": 1,
                },
                "review_scope": {
                    "automated_redaction_template": True,
                    "review_fields_complete": False,
                    "external_sharing_blocked": True,
                    "separate_human_content_review_required": True,
                    "actual_package_content_review_completed": False,
                    "automated_template_is_actual_package_content_review": False,
                    "actual_content_review_required_before_external_sharing": True,
                },
                "claim_controls": {
                    "public_safe": False,
                    "external_sharing_allowed": False,
                    "claim_allowed": False,
                    "helper_can_approve_public_safety": False,
                    "helper_can_authorize_external_sharing": False,
                    "actual_content_review_required": True,
                    "actual_content_review_completed": False,
                    "external_sharing_blocked": True,
                    "separate_human_content_review_required": True,
                    "public_safe_approval_created": False,
                },
                "review_template": {
                    "public_safe": False,
                    "external_sharing_allowed": False,
                    "claim_allowed": False,
                    "required_before_external_sharing": True,
                    "review_fields_complete": False,
                    "external_sharing_blocked": True,
                    "separate_human_content_review_required": True,
                    "actual_package_content_review_completed": False,
                    "checklist": [{"id": "scope_and_audience", "status": "pending"}],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(diagnostics_review_root),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    review_summary = packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]
    assert packet["summary"]["packet_status"] == "redacted_partial_evidence_summary"
    assert packet["summary"]["source_contract_failures"] == 0
    assert packet["summary"]["diagnostics_public_safe"] is False
    assert review_summary["source_contract_status"] == "valid_fail_closed_template"
    assert review_summary["mismatch_reasons"] == []
    assert review_summary["review_status"] == "blocked_missing_diagnostics_package"
    assert review_summary["public_safe"] is False
    assert review_summary["external_sharing_allowed"] is False
    assert review_summary["claim_allowed"] is False
    assert review_summary["human_review_signoff"] is False
    assert review_summary["template_is_human_signoff"] is False
    assert review_summary["review_fields_complete"] is False
    assert review_summary["actual_package_content_review_completed"] is False
    assert review_summary["external_sharing_blocked"] is True
    assert review_summary["separate_human_content_review_required"] is True


def test_release_evidence_packet_consumes_result_quality_review_template(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    result_quality_root = tmp_path / "result-quality-review"
    review_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_result_quality_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(result_quality_root),
            "-TaskArtifactLabel",
            "task/run status-log label",
            "-ResultArtifactLabel",
            "user-visible result artifact label",
            "-UserVisibleResultReview",
            "Visible result matched the beginner request.",
            "-SourceArtifactCheck",
            "Cited artifact labels were present.",
            "-NextStepActionabilityCheck",
            "Next step was clear and actionable.",
            "-Reviewer",
            "QA reviewer",
            "-ReviewedAtUtc",
            "2026-06-09T12:34:56Z",
            "-BlockedReason",
            "none",
            "-ObservedArtifact",
            "portable.status.log",
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    assert review_result.returncode == 0, review_result.stdout + review_result.stderr

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(result_quality_root),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 0, release_output
    assert str(tmp_path) not in release_output
    packet_path = next(evidence_root.rglob("release-evidence-packet.redacted.json"))
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(encoding="utf-8-sig")
    packet = json.loads(packet_path.read_text(encoding="utf-8-sig"))
    review_summary = packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]
    assert review_summary["found"] is True
    assert review_summary["marker"] == "NOT_RESULT_QUALITY_SIGNOFF"
    assert review_summary["source_contract_status"] == "valid_not_signoff_template"
    assert review_summary["mismatch_reasons"] == []
    assert review_summary["review_status"] == "manual_review_fields_recorded_not_signoff"
    assert review_summary["review_fields_complete"] is True
    assert review_summary["missing_field_count"] == 0
    assert review_summary["issue_count"] == 0
    assert review_summary["blocked_reason_count"] == 1
    assert review_summary["observed_artifact_count"] == 1
    assert review_summary["result_quality_signoff"] is False
    assert review_summary["result_quality_claim_blocked"] is True
    assert review_summary["separate_human_signoff_required"] is True
    assert review_summary["signoff"] is False
    assert review_summary["claim_allowed"] is False
    assert review_summary["completed_result_evidence"] is False
    assert review_summary["release_candidate_signoff"] is False
    assert review_summary["release_signoff"] is False
    assert packet["summary"]["source_contract_failures"] == 0
    assert packet["summary"]["result_quality_signoff"] is False
    assert "Result-quality review packet: found=True" in markdown_text
    assert "result_quality_signoff=False" in markdown_text
    assert "completed_result_evidence=False" in markdown_text


def test_release_evidence_packet_fail_closes_malformed_result_quality_review_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    result_quality_root = tmp_path / "result-quality-review"
    malformed_root = result_quality_root / "run-malformed"
    malformed_root.mkdir(parents=True)
    (malformed_root / "result-quality-review.redacted.json").write_text(
        json.dumps(
            {
                "marker": "sk-proj-RESULTQUALITYSECRET1234",
                "summary": {
                    "status": r"C:\Users\Suli\Contoso\approved?token=quality-secret",
                    "signoff": True,
                    "result_quality_signoff": True,
                    "claim_allowed": True,
                    "review_fields_complete": True,
                    "result_quality_claim_blocked": False,
                    "separate_human_signoff_required": False,
                    "completed_result_evidence": True,
                    "release_candidate_signoff": True,
                    "release_signoff": True,
                    "template_is_signoff": True,
                    "missing_field_count": "0",
                    "issue_count": "0",
                },
                "claim_controls": {
                    "claim_allowed": True,
                    "result_quality_claim_blocked": False,
                    "separate_human_signoff_required": False,
                    "result_quality_signoff": True,
                    "completed_result_evidence": True,
                    "packet_is_rc_signoff": True,
                    "packet_is_release_signoff": True,
                },
                "readonly_scope": {
                    "starts_product_processes": True,
                    "performs_network_requests": True,
                    "uploads_external_services": True,
                },
                "reviewer": {
                    "blocked_reason_redacted": ["token=quality-secret"],
                },
                "task_result_artifact": {
                    "observed_artifacts_redacted": [r"C:\Users\Suli\Contoso\artifact.txt"],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    _write_settings_local_model_smoke_artifacts(qa_root)

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(tmp_path / "empty-diagnostics-review"),
            "-ResultQualityReviewEvidenceRoot",
            str(result_quality_root),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest result-quality review helper artifact failed fail-closed validation" in release_output
    assert str(tmp_path) not in release_output
    assert "sk-proj-RESULTQUALITYSECRET1234" not in release_output
    assert "quality-secret" not in release_output
    assert "Contoso" not in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    assert "sk-proj-RESULTQUALITYSECRET1234" not in packet_text
    assert "quality-secret" not in packet_text
    assert "Contoso" not in packet_text
    packet = json.loads(packet_text)
    review_summary = packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert review_summary["source_contract_status"] == "source_contract_mismatch"
    assert review_summary["marker"] == "invalid_redacted"
    assert review_summary["review_status"] == "invalid_redacted"
    assert review_summary["result_quality_signoff"] is False
    assert review_summary["signoff"] is False
    assert review_summary["claim_allowed"] is False
    assert review_summary["completed_result_evidence"] is False
    mismatch_reasons = "\n".join(review_summary["mismatch_reasons"])
    assert "marker is missing or not NOT_RESULT_QUALITY_SIGNOFF" in mismatch_reasons
    assert "summary.status is not an allowed result-quality review status" in mismatch_reasons
    assert "summary.signoff is not false" in mismatch_reasons
    assert "summary.result_quality_signoff is not false" in mismatch_reasons
    assert "summary.claim_allowed is not false" in mismatch_reasons
    assert "summary.result_quality_claim_blocked is not true" in mismatch_reasons
    assert "summary.separate_human_signoff_required is not true" in mismatch_reasons
    assert "summary.completed_result_evidence is not false" in mismatch_reasons
    assert "summary.release_candidate_signoff is not false" in mismatch_reasons
    assert "summary.release_signoff is not false" in mismatch_reasons
    assert "summary.template_is_signoff is not false" in mismatch_reasons
    assert "summary.missing_field_count is not a non-negative JSON integer" in mismatch_reasons
    assert "summary.issue_count is not a non-negative JSON integer" in mismatch_reasons
    assert "claim_controls.claim_allowed is not false" in mismatch_reasons
    assert "claim_controls.result_quality_claim_blocked is not true" in mismatch_reasons
    assert "claim_controls.separate_human_signoff_required is not true" in mismatch_reasons
    assert "claim_controls.result_quality_signoff is not false" in mismatch_reasons
    assert "claim_controls.completed_result_evidence is not false" in mismatch_reasons
    assert "claim_controls.packet_is_rc_signoff is not false" in mismatch_reasons
    assert "claim_controls.packet_is_release_signoff is not false" in mismatch_reasons
    assert "readonly_scope.starts_product_processes is not false" in mismatch_reasons
    assert "readonly_scope.performs_network_requests is not false" in mismatch_reasons
    assert "readonly_scope.uploads_external_services is not false" in mismatch_reasons


def test_release_evidence_packet_fail_closes_malformed_diagnostics_review_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_review_root = tmp_path / "diagnostics-external-review"
    malformed_root = diagnostics_review_root / "run-malformed"
    malformed_root.mkdir(parents=True)
    (malformed_root / "diagnostics-external-review.redacted.json").write_text(
        json.dumps(
            {
                "marker": "sk-proj-RELEASESECRET1234",
                "summary": {
                    "status": r"C:\Users\Suli\Contoso\approved?token=release-secret",
                    "public_safe": True,
                    "required_before_external_sharing": False,
                    "human_review_signoff": True,
                    "external_public_safe_signoff": True,
                    "template_is_human_signoff": True,
                    "external_sharing_allowed": True,
                    "claim_allowed": True,
                    "review_fields_complete": True,
                    "external_sharing_blocked": False,
                    "separate_human_content_review_required": False,
                    "actual_package_content_review_completed": True,
                    "automated_template_only": False,
                },
                "review_scope": {
                    "automated_redaction_template": False,
                    "review_fields_complete": True,
                    "external_sharing_blocked": False,
                    "separate_human_content_review_required": False,
                    "actual_package_content_review_completed": True,
                    "automated_template_is_actual_package_content_review": True,
                    "actual_content_review_required_before_external_sharing": False,
                },
                "claim_controls": {
                    "public_safe": True,
                    "external_sharing_allowed": True,
                    "claim_allowed": True,
                    "helper_can_approve_public_safety": True,
                    "helper_can_authorize_external_sharing": True,
                    "actual_content_review_required": False,
                    "actual_content_review_completed": True,
                    "external_sharing_blocked": False,
                    "separate_human_content_review_required": False,
                    "public_safe_approval_created": True,
                },
                "review_template": {
                    "public_safe": True,
                    "external_sharing_allowed": True,
                    "claim_allowed": True,
                    "required_before_external_sharing": False,
                    "review_fields_complete": True,
                    "external_sharing_blocked": False,
                    "separate_human_content_review_required": False,
                    "actual_package_content_review_completed": True,
                    "checklist": [],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(diagnostics_review_root),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    assert "latest diagnostics external-review helper artifact failed fail-closed validation" in release_output
    assert str(tmp_path) not in release_output
    assert "sk-proj-RELEASESECRET1234" not in release_output
    assert "release-secret" not in release_output
    assert "Contoso" not in release_output
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    assert "sk-proj-RELEASESECRET1234" not in packet_text
    assert "release-secret" not in packet_text
    assert "Contoso" not in packet_text
    packet = json.loads(packet_text)
    review_summary = packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]
    assert packet["summary"]["packet_status"] == "source_contract_failure"
    assert packet["summary"]["source_contract_failures"] == 1
    assert review_summary["source_contract_status"] == "source_contract_mismatch"
    assert review_summary["marker"] == "invalid_redacted"
    assert review_summary["review_status"] == "invalid_redacted"
    assert review_summary["public_safe"] is False
    assert review_summary["external_sharing_allowed"] is False
    assert review_summary["human_review_signoff"] is False
    assert review_summary["template_is_human_signoff"] is False
    mismatch_reasons = "\n".join(review_summary["mismatch_reasons"])
    assert "marker is missing or not NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF" in mismatch_reasons
    assert "review status is not a recognized fail-closed diagnostics review status" in mismatch_reasons
    assert "summary.public_safe is not false" in mismatch_reasons
    assert "summary.required_before_external_sharing is not true" in mismatch_reasons
    assert "summary.human_review_signoff is not false" in mismatch_reasons
    assert "summary.external_public_safe_signoff is not false" in mismatch_reasons
    assert "summary.template_is_human_signoff is not false" in mismatch_reasons
    assert "summary.external_sharing_allowed is not false" in mismatch_reasons
    assert "summary.claim_allowed is not false" in mismatch_reasons
    assert "summary.actual_package_content_review_completed is not false" in mismatch_reasons
    assert "summary.automated_template_only is not true" in mismatch_reasons
    assert "summary.review_fields_complete is not false" in mismatch_reasons
    assert "summary.external_sharing_blocked is not true" in mismatch_reasons
    assert "summary.separate_human_content_review_required is not true" in mismatch_reasons
    assert "review_scope.automated_redaction_template is not true" in mismatch_reasons
    assert "review_scope.actual_package_content_review_completed is not false" in mismatch_reasons
    assert "review_scope.automated_template_is_actual_package_content_review is not false" in mismatch_reasons
    assert "review_scope.actual_content_review_required_before_external_sharing is not true" in mismatch_reasons
    assert "review_scope.review_fields_complete is not false" in mismatch_reasons
    assert "review_scope.external_sharing_blocked is not true" in mismatch_reasons
    assert "review_scope.separate_human_content_review_required is not true" in mismatch_reasons
    assert "claim_controls.public_safe is not false" in mismatch_reasons
    assert "claim_controls.external_sharing_allowed is not false" in mismatch_reasons
    assert "claim_controls.claim_allowed is not false" in mismatch_reasons
    assert "claim_controls.helper_can_approve_public_safety is not false" in mismatch_reasons
    assert "claim_controls.helper_can_authorize_external_sharing is not false" in mismatch_reasons
    assert "claim_controls.actual_content_review_required is not true" in mismatch_reasons
    assert "claim_controls.actual_content_review_completed is not false" in mismatch_reasons
    assert "claim_controls.external_sharing_blocked is not true" in mismatch_reasons
    assert "claim_controls.separate_human_content_review_required is not true" in mismatch_reasons
    assert "claim_controls.public_safe_approval_created is not false" in mismatch_reasons
    assert "review_template.public_safe is not false" in mismatch_reasons
    assert "review_template.external_sharing_allowed is not false" in mismatch_reasons
    assert "review_template.claim_allowed is not false" in mismatch_reasons
    assert "review_template.required_before_external_sharing is not true" in mismatch_reasons
    assert "review_template.review_fields_complete is not false" in mismatch_reasons
    assert "review_template.external_sharing_blocked is not true" in mismatch_reasons
    assert "review_template.separate_human_content_review_required is not true" in mismatch_reasons
    assert "review_template.actual_package_content_review_completed is not false" in mismatch_reasons


def test_release_evidence_packet_rejects_string_boolean_diagnostics_review_artifact(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_review_root = tmp_path / "diagnostics-external-review"
    malformed_root = diagnostics_review_root / "run-string-booleans"
    malformed_root.mkdir(parents=True)
    (malformed_root / "diagnostics-external-review.redacted.json").write_text(
        json.dumps(
            {
                "marker": "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF",
                "summary": {
                    "status": "manual_external_review_template_ready",
                    "public_safe": "false",
                    "required_before_external_sharing": "true",
                    "human_review_signoff": "false",
                    "external_public_safe_signoff": "false",
                    "template_is_human_signoff": "false",
                    "external_sharing_allowed": "false",
                    "claim_allowed": "false",
                    "review_fields_complete": "false",
                    "external_sharing_blocked": "true",
                    "separate_human_content_review_required": "true",
                    "actual_package_content_review_completed": "false",
                    "automated_template_only": "true",
                },
                "review_scope": {
                    "automated_redaction_template": "true",
                    "review_fields_complete": "false",
                    "external_sharing_blocked": "true",
                    "separate_human_content_review_required": "true",
                    "actual_package_content_review_completed": "false",
                    "automated_template_is_actual_package_content_review": "false",
                    "actual_content_review_required_before_external_sharing": "true",
                },
                "claim_controls": {
                    "public_safe": "false",
                    "external_sharing_allowed": "false",
                    "claim_allowed": "false",
                    "helper_can_approve_public_safety": "false",
                    "helper_can_authorize_external_sharing": "false",
                    "actual_content_review_required": "true",
                    "actual_content_review_completed": "false",
                    "external_sharing_blocked": "true",
                    "separate_human_content_review_required": "true",
                    "public_safe_approval_created": "false",
                },
                "review_template": {
                    "public_safe": "false",
                    "external_sharing_allowed": "false",
                    "claim_allowed": "false",
                    "required_before_external_sharing": "true",
                    "review_fields_complete": "false",
                    "external_sharing_blocked": "true",
                    "separate_human_content_review_required": "true",
                    "actual_package_content_review_completed": "false",
                    "checklist": [],
                },
            }
        ),
        encoding="utf-8",
    )

    qa_root = tmp_path / "qa-evidence"
    qa_root.mkdir()
    for name in (
        "settings-local-model-experience-smoke-desktop.png",
        "settings-local-model-experience-smoke-desktop-setup.png",
        "settings-local-model-experience-smoke-narrow.png",
        "settings-local-model-experience-smoke-narrow-setup.png",
    ):
        (qa_root / name).write_bytes(b"redacted-smoke-artifact")

    evidence_root = tmp_path / "release-evidence-packet"
    release_result = subprocess.run(
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
            "-DiagnosticsReviewEvidenceRoot",
            str(diagnostics_review_root),
            "-ResultQualityReviewEvidenceRoot",
            str(tmp_path / "empty-result-quality-review"),
            "-LocalModelCleanMachineEvidenceRoot",
            str(tmp_path / "empty-local-model-template"),
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
    release_output = release_result.stdout + release_result.stderr

    assert release_result.returncode == 1, release_output
    packet = json.loads(
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(encoding="utf-8-sig")
    )
    review_summary = packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]
    assert review_summary["source_contract_status"] == "source_contract_mismatch"
    assert review_summary["marker"] == "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF"
    assert review_summary["review_status"] == "manual_external_review_template_ready"
    assert review_summary["public_safe"] is False
    mismatch_reasons = "\n".join(review_summary["mismatch_reasons"])
    assert "summary.public_safe is not false" in mismatch_reasons
    assert "summary.required_before_external_sharing is not true" in mismatch_reasons
    assert "summary.human_review_signoff is not false" in mismatch_reasons
    assert "summary.external_public_safe_signoff is not false" in mismatch_reasons
    assert "summary.template_is_human_signoff is not false" in mismatch_reasons
    assert "summary.external_sharing_allowed is not false" in mismatch_reasons
    assert "summary.claim_allowed is not false" in mismatch_reasons
    assert "summary.actual_package_content_review_completed is not false" in mismatch_reasons
    assert "summary.automated_template_only is not true" in mismatch_reasons
    assert "summary.review_fields_complete is not a JSON boolean" in mismatch_reasons
    assert "summary.external_sharing_blocked is not true" in mismatch_reasons
    assert "summary.separate_human_content_review_required is not true" in mismatch_reasons
    assert "review_scope.automated_redaction_template is not true" in mismatch_reasons
    assert "claim_controls.external_sharing_blocked is not true" in mismatch_reasons
    assert "claim_controls.separate_human_content_review_required is not true" in mismatch_reasons
    assert "review_template.public_safe is not false" in mismatch_reasons
    assert "review_template.external_sharing_blocked is not true" in mismatch_reasons
    assert "review_template.separate_human_content_review_required is not true" in mismatch_reasons


def _write_diagnostics_support_package(path: Path, *, generated_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": generated_at,
                "diagnostic_scope": "local_only",
                "diagnostics": {
                    "diagnostic_scope": "local_only",
                    "product": {"name": "Lengrvis", "version": "0.1.0"},
                    "local_paths": {
                        "data_dir": r"C:\Users\Suli\Contoso\LengrvisData",
                        "database": r"C:\Users\Suli\Contoso\LengrvisData\lengrvis.db",
                    },
                    "top_processes": [
                        {
                            "name": "Lengrvis.exe",
                            "username": "Suli",
                            "command_line": "Authorization: Bearer diagnostics-secret-token",
                        }
                    ],
                    "support_debug": {
                        "note": "callback=https://private.example.test/path?token=diagnostics-url-secret",
                    },
                    "support_package_redaction": {
                        "schema_version": 1,
                        "applies_to": "diagnostics_export_payload",
                        "scope": "local_only",
                        "intended_audience": "trusted_support",
                        "public_safe": False,
                        "review_before_external_sharing": True,
                        "current_response": {
                            "public_safe": False,
                            "contains_local_paths": False,
                            "external_review_required": True,
                        },
                        "external_review": {
                            "schema_version": 1,
                            "status": "manual_review_required",
                            "required_before_external_sharing": True,
                            "public_safe": False,
                            "checklist": [
                                {
                                    "id": "external_sharing_decision",
                                    "status": "pending",
                                    "required": True,
                                }
                            ],
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_diagnostics_external_review_packet_script_is_read_only_and_not_signoff(project_root: Path) -> None:
    text = _diagnostics_external_review_packet_text(project_root)

    assert "diagnostics-external-review.redacted.json" in text
    assert "diagnostics-external-review.redacted.md" in text
    assert "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF" in text
    assert "manual_external_diagnostics_review_required" in text
    assert "public_safe = $false" in text
    assert "required_before_external_sharing = $true" in text
    assert "human_review_signoff = $false" in text
    assert "external_public_safe_signoff = $false" in text
    assert "template_is_human_signoff = $false" in text
    assert "starts_product_processes = $false" in text
    assert "performs_network_requests = $false" in text
    assert "uploads_external_services = $false" in text
    assert "installs_dependencies = $false" in text
    assert "external_service_data_read = $false" in text
    assert "package_payload_copied = $false" in text
    assert "external public-safe signoff" in text
    assert "permission to publish diagnostics" in text
    assert "human reviewer approval" in text
    assert "public_safe remains false" in text

    assert "Start-Process" not in text
    assert "Stop-Process" not in text
    assert "Invoke-WebRequest" not in text
    assert "Invoke-RestMethod" not in text
    assert "curl" not in text.lower()
    assert "pip install" not in text.lower()
    assert "npm install" not in text.lower()
    assert "Copy-Item" not in text
    assert "Move-Item" not in text
    assert "Remove-Item" not in text
    assert "public_safe = $true" not in text
    assert "Set-Content -LiteralPath $jsonPath" in text
    assert "Set-Content -LiteralPath $markdownPath" in text


def test_diagnostics_external_review_packet_outputs_latest_redacted_template(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_root = tmp_path / "diagnostic-packages"
    old_package = diagnostics_root / "lengrvis-diagnostics-old.json"
    latest_package = diagnostics_root / "lengrvis-diagnostics-new.json"
    _write_diagnostics_support_package(old_package, generated_at="2026-06-08T00:00:00+00:00")
    _write_diagnostics_support_package(latest_package, generated_at="2026-06-09T00:00:00+00:00")
    os.utime(old_package, (1_700_000_000, 1_700_000_000))
    os.utime(latest_package, (1_800_000_000, 1_800_000_000))

    evidence_root = tmp_path / "diagnostics-external-review"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsRoot",
            str(diagnostics_root),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Diagnostics external review template" in output
    assert "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF" in output
    assert "public_safe remains false" in output
    for raw in (
        str(tmp_path),
        r"C:\Users\Suli",
        "Contoso",
        "diagnostics-secret-token",
        "diagnostics-url-secret",
        "private.example.test",
    ):
        assert raw not in output

    json_outputs = list(evidence_root.rglob("diagnostics-external-review.redacted.json"))
    markdown_outputs = list(evidence_root.rglob("diagnostics-external-review.redacted.md"))
    assert len(json_outputs) == 1
    assert len(markdown_outputs) == 1

    packet_text = json_outputs[0].read_text(encoding="utf-8-sig")
    markdown_text = markdown_outputs[0].read_text(encoding="utf-8-sig")
    for raw in (
        str(tmp_path),
        r"C:\Users\Suli",
        "Contoso",
        "diagnostics-secret-token",
        "diagnostics-url-secret",
        "private.example.test",
    ):
        assert raw not in packet_text
        assert raw not in markdown_text
    assert '"public_safe": true' not in packet_text.lower()

    packet = json.loads(packet_text)
    assert packet["marker"] == "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF"
    assert packet["summary"]["status"] == "manual_external_review_template_ready"
    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["required_before_external_sharing"] is True
    assert packet["summary"]["human_review_signoff"] is False
    assert packet["summary"]["external_public_safe_signoff"] is False
    assert packet["summary"]["template_is_human_signoff"] is False
    assert packet["readonly_scope"]["starts_product_processes"] is False
    assert packet["readonly_scope"]["performs_network_requests"] is False
    assert packet["readonly_scope"]["uploads_external_services"] is False
    assert packet["redaction"]["external_service_data_read"] is False
    assert packet["input_diagnostics_package"]["selection_mode"] == "latest"
    assert packet["input_diagnostics_package"]["package_label"] == "lengrvis-diagnostics-new.json"
    assert packet["source_redaction_contract"]["package_public_safe_observation"] == "false"
    assert packet["source_redaction_contract"]["external_review_public_safe_observation"] == "false"
    assert packet["source_redaction_contract"]["external_review_status"] == "manual_review_required"
    assert packet["review_template"]["public_safe"] is False
    assert packet["review_template"]["human_decision"] == "pending"
    assert "external public-safe signoff" in packet["review_template"]["must_not_be_recorded_as"]
    assert "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF" in markdown_text
    assert "This template is not human reviewer approval" in markdown_text


def test_diagnostics_external_review_packet_uses_specified_package_path(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    diagnostics_root = tmp_path / "diagnostic-packages"
    latest_package = diagnostics_root / "lengrvis-diagnostics-latest.json"
    specified_package = tmp_path / "manual package" / "lengrvis-diagnostics-specified.json"
    _write_diagnostics_support_package(latest_package, generated_at="2026-06-09T00:00:00+00:00")
    _write_diagnostics_support_package(specified_package, generated_at="2026-06-08T12:00:00+00:00")
    os.utime(latest_package, (1_800_000_000, 1_800_000_000))
    os.utime(specified_package, (1_700_000_000, 1_700_000_000))

    evidence_root = tmp_path / "specified-review"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsRoot",
            str(diagnostics_root),
            "-DiagnosticsPackagePath",
            str(specified_package),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert str(tmp_path) not in output
    packet = json.loads(
        next(evidence_root.rglob("diagnostics-external-review.redacted.json")).read_text(encoding="utf-8-sig")
    )
    assert packet["input_diagnostics_package"]["selection_mode"] == "specified"
    assert packet["input_diagnostics_package"]["package_label"] == "lengrvis-diagnostics-specified.json"
    assert packet["input_diagnostics_package"]["package_label"] != "lengrvis-diagnostics-latest.json"
    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["template_is_human_signoff"] is False


def test_diagnostics_external_review_packet_redacts_sensitive_package_basename(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    specified_package = tmp_path / "manual package" / "Contoso-token-secret-sk-proj-ABCDEFGH.json"
    _write_diagnostics_support_package(specified_package, generated_at="2026-06-09T00:00:00+00:00")

    evidence_root = tmp_path / "sensitive-name-review"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsPackagePath",
            str(specified_package),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet_text = next(evidence_root.rglob("diagnostics-external-review.redacted.json")).read_text(encoding="utf-8-sig")
    markdown_text = next(evidence_root.rglob("diagnostics-external-review.redacted.md")).read_text(encoding="utf-8-sig")
    combined = "\n".join((output, packet_text, markdown_text))
    for raw in (
        str(tmp_path),
        "Contoso",
        "Contoso-token-secret",
        "token-secret",
        "sk-proj-ABCDEFGH",
    ):
        assert raw not in combined

    packet = json.loads(packet_text)
    package_label = packet["input_diagnostics_package"]["package_label"]
    assert package_label != "Contoso-token-secret-sk-proj-ABCDEFGH.json"
    assert "[redacted" in package_label
    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["template_is_human_signoff"] is False


def test_diagnostics_external_review_packet_blocks_missing_latest_and_specified_path(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    empty_root = tmp_path / "empty-diagnostic-packages"
    empty_root.mkdir()
    latest_evidence_root = tmp_path / "missing-latest-review"
    latest_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsRoot",
            str(empty_root),
            "-EvidenceRoot",
            str(latest_evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    latest_output = latest_result.stdout + latest_result.stderr

    assert latest_result.returncode == 1, latest_output
    assert "blocked_missing_diagnostics_package" in latest_output
    assert str(tmp_path) not in latest_output
    latest_packet = json.loads(
        next(latest_evidence_root.rglob("diagnostics-external-review.redacted.json")).read_text(encoding="utf-8-sig")
    )
    assert latest_packet["summary"]["status"] == "blocked_missing_diagnostics_package"
    assert latest_packet["summary"]["public_safe"] is False
    assert latest_packet["input_diagnostics_package"]["selection_mode"] == "latest"
    assert latest_packet["input_diagnostics_package"]["package_found"] is False
    assert "no diagnostics package JSON was found" in "\n".join(latest_packet["issues_redacted"])

    missing_path = tmp_path / "private-sk-diagnostics-secret" / "missing-diagnostics.json"
    specified_evidence_root = tmp_path / "missing-specified-review"
    specified_result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-DiagnosticsPackagePath",
            str(missing_path),
            "-EvidenceRoot",
            str(specified_evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    specified_output = specified_result.stdout + specified_result.stderr

    assert specified_result.returncode == 1, specified_output
    assert "blocked_missing_diagnostics_package" in specified_output
    assert str(tmp_path) not in specified_output
    assert "private-sk-diagnostics-secret" not in specified_output
    specified_packet = json.loads(
        next(specified_evidence_root.rglob("diagnostics-external-review.redacted.json")).read_text(encoding="utf-8-sig")
    )
    assert specified_packet["summary"]["status"] == "blocked_missing_diagnostics_package"
    assert specified_packet["summary"]["public_safe"] is False
    assert specified_packet["input_diagnostics_package"]["selection_mode"] == "specified"
    assert specified_packet["input_diagnostics_package"]["package_found"] is False
    assert specified_packet["input_diagnostics_package"]["package_label"] == "missing-diagnostics.json"
    assert "specified diagnostics package was not found" in "\n".join(specified_packet["issues_redacted"])


def test_local_model_clean_machine_evidence_template_script_is_read_only_and_redacted(project_root: Path) -> None:
    text = _local_model_clean_machine_evidence_template_text(project_root)

    assert "local-model-clean-machine-evidence.redacted.json" in text
    assert "local-model-clean-machine-evidence.redacted.md" in text
    assert "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS" in text
    assert "runtime.name" in text
    assert "runtime.version" in text
    assert "model.name" in text
    assert "model.version" in text
    assert "artifact_build_profile.artifact_under_test" in text
    assert "artifact_build_profile.build_identifier" in text
    assert "artifact_build_profile.profile_under_test" in text
    assert "clean_machine_run.install.outcome_or_blocked_reason" in text
    assert "clean_machine_run.task_smoke.outcome_or_blocked_reason" in text
    assert "blocked_reason_redacted" in text
    assert "clean_machine_signoff = $false" in text
    assert "local_model_install_pass = $false" in text
    assert "local_model_start_pass = $false" in text
    assert "local_model_pull_pass = $false" in text
    assert "local_model_task_smoke_pass = $false" in text
    assert "real_install_start_pull_pass = $false" in text
    assert "template_is_clean_machine_pass = $false" in text
    assert "dev_smoke_is_clean_machine_pass = $false" in text
    assert "starts_product_processes = $false" in text
    assert "performs_network_requests = $false" in text
    assert "installs_runtime = $false" in text
    assert "starts_runtime = $false" in text
    assert "pulls_models = $false" in text
    assert "runs_model_inference = $false" in text
    assert "workspace-relative paths or file labels only" in text

    assert "Start-Process" not in text
    assert "Stop-Process" not in text
    assert "Invoke-WebRequest" not in text
    assert "Invoke-RestMethod" not in text
    assert "ollama pull" not in text.lower()
    assert "ollama serve" not in text.lower()
    assert "/api/settings/install-local-model" not in text
    assert "pip install" not in text.lower()
    assert "npm install" not in text.lower()
    assert "Copy-Item" not in text
    assert "Move-Item" not in text
    assert "Remove-Item" not in text
    assert "Set-Content -LiteralPath $jsonPath" in text
    assert "Set-Content -LiteralPath $markdownPath" in text


def test_local_model_clean_machine_evidence_template_default_output_is_actionable_not_pass(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "local-model-clean-machine-evidence"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "What is missing now:" in output
    assert "clean_machine_run.install.outcome_or_blocked_reason" in output
    assert "Next helper command template:" in output
    assert "-ArtifactUnderTest <candidate artifact>" in output
    assert "-InstallOutcome <observed install outcome>" in output
    assert "evidence artifact label:" in output
    assert "This remains NOT a clean-machine pass" in output
    assert str(tmp_path) not in output

    packet_text = next(evidence_root.rglob("local-model-clean-machine-evidence.redacted.json")).read_text(
        encoding="utf-8-sig"
    )
    markdown_text = next(evidence_root.rglob("local-model-clean-machine-evidence.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
    packet = json.loads(packet_text)

    assert packet["summary"]["template_status"] == "blocked_missing_required_fields"
    assert packet["summary"]["missing_required_fields_count"] == 11
    assert packet["summary"]["clean_machine_signoff"] is False
    assert packet["summary"]["local_model_install_pass"] is False
    assert packet["summary"]["local_model_start_pass"] is False
    assert packet["summary"]["local_model_pull_pass"] is False
    assert packet["summary"]["local_model_task_smoke_pass"] is False
    assert packet["summary"]["release_candidate_signoff"] is False

    handoff = packet["evidence_template"]["actionable_handoff"]
    assert handoff["status"] == "blocked_missing_required_fields"
    assert handoff["pass_defaults_remain_false"] is True
    assert handoff["missing_evidence_artifacts_status"] == "missing_redacted_artifact_labels"
    assert "clean-machine local-model readiness" in handoff["not_a_pass"]
    assert "release-candidate sign-off" in handoff["not_a_pass"]
    assert "-TaskSmokeOutcome <observed task-smoke outcome>" in handoff["next_helper_command_template"]
    assert "-BlockedReason <why the clean-machine run is blocked>" in handoff["blocked_run_helper_command_template"]

    missing = {item["field"]: item for item in handoff["missing_now"]}
    assert len(missing) == 11
    assert missing["artifact_build_profile.artifact_under_test"]["helper_argument"].startswith("-ArtifactUnderTest")
    assert (
        "manual clean-machine install outcome"
        in missing["clean_machine_run.install.outcome_or_blocked_reason"]["missing_artifact"]
    )
    assert (
        "TaskSmokeBlockedReason" in missing["clean_machine_run.task_smoke.outcome_or_blocked_reason"]["helper_argument"]
    )
    assert handoff["missing_evidence_artifacts"] == [
        "redacted screenshot/log labels for the manual clean-machine install/start/pull/task-smoke run; add with -Artifact <reviewed screenshot or log label>"
    ]
    assert "## Missing Now" in markdown_text
    assert "## Next Helper Command Template" in markdown_text


def test_local_model_clean_machine_evidence_template_outputs_redacted_json_and_markdown(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "local-model-clean-machine-evidence"
    runtime_path = tmp_path / "private-runtime" / "ollama.exe"
    artifact_path = tmp_path / "qa artifacts" / "manual local model evidence.log"
    blocked_reason = (
        r"Manual run blocked by proxy token=local-model-secret and private path "
        r"C:\Users\Suli\private\models"
    )

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
            "-Candidate",
            "rc-2026-06-08",
            "-EvidenceMode",
            "clean-machine",
            "-Platform",
            "Windows x64",
            "-ArtifactUnderTest",
            str(tmp_path / "dist" / "mavris-local-model-candidate.exe"),
            "-BuildIdentifier",
            "build-2026.06.08",
            "-ProfileUnderTest",
            r"C:\Users\Suli\clean-machine-profile",
            "-Runtime",
            "Ollama",
            "-RuntimeVersion",
            "0.5.7",
            "-RuntimeSource",
            str(runtime_path),
            "-Model",
            "qwen2.5:3b",
            "-ModelVersion",
            "sha256:abc123",
            "-ModelSource",
            "https://models.example.test/private?q=token-secret",
            "-InstallOutcome",
            "candidate artifact installed without starting a cloud fallback",
            "-StartOutcome",
            "runtime start screen reached",
            "-PullOutcome",
            "model pull or model availability screen reached",
            "-TaskSmokeBlockedReason",
            blocked_reason,
            "-Artifact",
            str(artifact_path),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "Local model clean-machine evidence template" in output
    assert "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS" in output
    assert str(tmp_path) not in output
    assert "local-model-secret" not in output
    assert "C:\\Users\\Suli" not in output

    json_outputs = list(evidence_root.rglob("local-model-clean-machine-evidence.redacted.json"))
    markdown_outputs = list(evidence_root.rglob("local-model-clean-machine-evidence.redacted.md"))
    assert len(json_outputs) == 1
    assert len(markdown_outputs) == 1

    packet_text = json_outputs[0].read_text(encoding="utf-8-sig")
    markdown_text = markdown_outputs[0].read_text(encoding="utf-8-sig")
    assert str(tmp_path) not in packet_text
    assert str(tmp_path) not in markdown_text
    assert "local-model-secret" not in packet_text
    assert "local-model-secret" not in markdown_text
    assert "C:\\Users\\Suli" not in packet_text
    assert "C:\\Users\\Suli" not in markdown_text

    packet = json.loads(packet_text)
    assert packet["marker"] == "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    assert packet["summary"]["template_status"] == "blocked_reason_recorded"
    assert packet["summary"]["clean_machine_signoff"] is False
    assert packet["summary"]["local_model_install_pass"] is False
    assert packet["summary"]["local_model_start_pass"] is False
    assert packet["summary"]["local_model_pull_pass"] is False
    assert packet["summary"]["local_model_task_smoke_pass"] is False
    assert packet["summary"]["real_install_start_pull_pass"] is False
    assert packet["summary"]["template_is_clean_machine_pass"] is False
    assert packet["summary"]["dev_smoke_is_clean_machine_pass"] is False
    assert packet["summary"]["artifact_build_profile_status"] == "recorded_unverified_by_this_helper"
    assert packet["summary"]["required_run_step_outcomes_recorded"] is True
    assert packet["readonly_scope"]["starts_product_processes"] is False
    assert packet["readonly_scope"]["performs_network_requests"] is False
    assert packet["readonly_scope"]["installs_runtime"] is False
    assert packet["readonly_scope"]["starts_runtime"] is False
    assert packet["readonly_scope"]["pulls_models"] is False
    assert packet["readonly_scope"]["runs_model_inference"] is False
    assert packet["evidence_template"]["template_status"] == "manual_clean_machine_local_model_evidence_required"
    artifact_build_profile = packet["evidence_template"]["artifact_build_profile"]
    assert artifact_build_profile["status"] == "recorded_unverified_by_this_helper"
    assert artifact_build_profile["artifact"]["label"] == "mavris-local-model-candidate.exe"
    assert artifact_build_profile["artifact"]["status"] == "unverified_by_this_helper"
    assert artifact_build_profile["build"]["identifier"] == "build-2026.06.08"
    assert artifact_build_profile["build"]["status"] == "unverified_by_this_helper"
    assert artifact_build_profile["profile"]["label"] == "clean-machine-profile"
    assert artifact_build_profile["profile"]["status"] == "unverified_by_this_helper"
    assert packet["evidence_template"]["runtime"]["name"] == "Ollama"
    assert packet["evidence_template"]["runtime"]["version"] == "0.5.7"
    assert packet["evidence_template"]["runtime"]["source"] == "ollama.exe"
    assert packet["evidence_template"]["model"]["name"] == "qwen2.5:3b"
    assert packet["evidence_template"]["model"]["version"] == "sha256:abc123"
    assert packet["evidence_template"]["model"]["source"] == "https://[redacted-host]/[redacted-path]"
    clean_machine_run = packet["evidence_template"]["clean_machine_run"]
    assert clean_machine_run["install"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    assert clean_machine_run["start"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    assert clean_machine_run["pull"]["status"] == "manual_outcome_recorded_unverified_by_this_helper"
    assert clean_machine_run["task_smoke"]["status"] == "blocked_reason_recorded"
    assert clean_machine_run["task_smoke"]["clean_machine_pass"] is False
    assert clean_machine_run["task_smoke"]["pass_verified_by_this_helper"] is False
    assert clean_machine_run["task_smoke"]["blocked_reason_redacted"] == [
        "Manual run blocked by proxy token=[redacted] and private path [redacted-path]"
    ]
    assert packet["evidence_template"]["blocked_reason_redacted"] == []
    assert packet["evidence_template"]["observed_artifacts_redacted"] == ["manual local model evidence.log"]
    assert "true local model install pass" in packet["evidence_template"]["must_not_be_recorded_as"]
    assert "true local model task-smoke pass" in packet["evidence_template"]["must_not_be_recorded_as"]
    assert "template/dev smoke clean-machine pass" in packet["evidence_template"]["must_not_be_recorded_as"]
    assert "Marker: NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS" in markdown_text
    assert "true local model pull pass" in markdown_text
    assert "task_smoke: outcome=blocked; status=blocked_reason_recorded; clean_machine_pass=False" in markdown_text


def test_local_model_clean_machine_evidence_template_redacts_sensitive_labels(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "local-model-clean-machine-evidence"
    runtime_path = tmp_path / "runtime" / "Contoso-token-secret-sk-proj-RUNTIME1234.exe"
    artifact_path = tmp_path / "qa artifacts" / "Contoso-token=artifact-secret-sk-proj-ARTIFACT1234.log"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_local_model_clean_machine_evidence_template.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
            "-Candidate",
            "rc-Contoso-token-secret-sk-proj-CANDIDATE1234",
            "-EvidenceMode",
            "clean-machine",
            "-Platform",
            "Windows x64",
            "-ArtifactUnderTest",
            str(tmp_path / "dist" / "Contoso-token=artifact-secret-sk-proj-CANDIDATE1234.exe"),
            "-BuildIdentifier",
            "build-Contoso-token=build-secret-sk-proj-BUILD1234",
            "-ProfileUnderTest",
            str(tmp_path / "profiles" / "Contoso-token=profile-secret-sk-proj-PROFILE1234"),
            "-Runtime",
            "Ollama",
            "-RuntimeVersion",
            "0.5.7",
            "-RuntimeSource",
            str(runtime_path),
            "-Model",
            "qwen2.5:3b",
            "-ModelVersion",
            "sha256:abc123",
            "-ModelSource",
            "https://models.example.test/Contoso/token=local-model-secret/private-model",
            "-BlockedReason",
            "Manual run blocked by token=local-model-secret",
            "-InstallOutcome",
            "Install outcome recorded with token=install-secret",
            "-StartOutcome",
            "Start outcome recorded with token=start-secret",
            "-PullOutcome",
            "Pull outcome recorded with token=pull-secret",
            "-TaskSmokeOutcome",
            "Task smoke outcome recorded with token=task-secret",
            "-Artifact",
            str(artifact_path),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet_text = next(evidence_root.rglob("local-model-clean-machine-evidence.redacted.json")).read_text(
        encoding="utf-8-sig"
    )
    markdown_text = next(evidence_root.rglob("local-model-clean-machine-evidence.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
    combined = "\n".join((output, packet_text, markdown_text))
    for raw in (
        str(tmp_path),
        "Contoso",
        "token-secret",
        "sk-proj-CANDIDATE1234",
        "sk-proj-BUILD1234",
        "sk-proj-PROFILE1234",
        "sk-proj-RUNTIME1234",
        "sk-proj-ARTIFACT1234",
        "build-secret",
        "profile-secret",
        "artifact-secret",
        "local-model-secret",
        "install-secret",
        "start-secret",
        "pull-secret",
        "task-secret",
        "private-model",
        "models.example.test",
    ):
        assert raw not in combined

    packet = json.loads(packet_text)
    assert "[redacted" in packet["evidence_template"]["candidate"]
    assert "[redacted" in packet["evidence_template"]["artifact_build_profile"]["artifact"]["label"]
    assert "[redacted" in packet["evidence_template"]["artifact_build_profile"]["build"]["identifier"]
    assert "[redacted" in packet["evidence_template"]["artifact_build_profile"]["profile"]["label"]
    assert "[redacted" in packet["evidence_template"]["runtime"]["source"]
    assert packet["evidence_template"]["model"]["source"] == "https://[redacted-host]/[redacted-path]"
    assert "token=[redacted]" in packet["evidence_template"]["clean_machine_run"]["install"]["outcome"]
    assert "[redacted" in packet["evidence_template"]["observed_artifacts_redacted"][0]
    assert packet["summary"]["clean_machine_signoff"] is False
    assert packet["summary"]["real_install_start_pull_pass"] is False
    assert packet["summary"]["local_model_task_smoke_pass"] is False
    assert packet["summary"]["template_is_clean_machine_pass"] is False
    assert packet["summary"]["dev_smoke_is_clean_machine_pass"] is False


def test_release_gate_recommends_release_evidence_packet_without_overclaim(project_root: Path) -> None:
    release_gate = _release_gate_text(project_root)

    assert r".\scripts\collect_release_evidence_packet.ps1" in release_gate
    assert r".tmp\release-evidence-packet\...\release-evidence-packet.redacted.json" in release_gate
    assert r".tmp\release-evidence-packet\...\release-evidence-packet.redacted.md" in release_gate
    assert "Ollama/local-model contract count" in release_gate
    assert "support_package_redaction.external_review" in release_gate
    assert "public_safe=false" in release_gate
    assert "latest portable first-screen/read-only/natural-language status-log summary" in release_gate
    assert (
        "Portable status-log coverage is packaged window/backend/local-only diagnostics plus command-dock submission/task-evidence coverage only"
        in release_gate
    )
    assert "latest local-model clean-machine handoff template status" in release_gate
    assert "latest natural-language result-quality review packet status" in release_gate
    assert "Settings local-model smoke artifact paths" in release_gate
    assert "not clean-machine local-model readiness" in release_gate
    assert "not true local model install/start/pull evidence" in release_gate
    assert "not real-device mobile evidence" in release_gate
    assert "not release-candidate sign-off" in release_gate
    assert "not completed task-result sign-off" in release_gate
    assert "not natural-language result-quality sign-off" in release_gate
    assert "`rc_handoff_requirements.status=manual_rc_handoff_required`" in release_gate
    assert "`release_candidate_signoff=false`" in release_gate
    assert "`packet_is_rc_signoff=false`" in release_gate
    assert "candidate commit or build id" in release_gate
    assert "exact release gate commands and full exit status" in release_gate
    assert "waivers with owner/reason/expiry/follow-up" in release_gate
    assert "Do not tag, publish, announce, or call an RC passed from `npm run evidence:release`" in release_gate
    assert (
        "release evidence packet RC handoff requirements (`manual_rc_handoff_required` is not sign-off)" in release_gate
    )
    assert r".\scripts\collect_local_model_clean_machine_evidence_template.ps1" in release_gate
    assert (
        r".tmp\local-model-clean-machine-evidence\...\local-model-clean-machine-evidence.redacted.json" in release_gate
    )
    assert r".tmp\local-model-clean-machine-evidence\...\local-model-clean-machine-evidence.redacted.md" in release_gate
    assert "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS" in release_gate
    assert "artifact/build/profile, runtime/model/version/status" in release_gate
    assert "install/start/pull/task-smoke outcome or blocked reason fields" in release_gate
    assert "not template/dev smoke clean-machine pass evidence" in release_gate
    assert "not true local model install/start/pull evidence" in release_gate


def test_real_device_mobile_matrix_documents_redacted_template_without_overclaim(project_root: Path) -> None:
    matrix = _real_device_mobile_matrix_text(project_root)

    assert "manual_real_device_evidence_template" in matrix
    assert "`template_status` stays `manual_real_device_evidence_required`" in matrix
    assert "`real_device_result` stays `uncollected`" in matrix
    assert "`claim_controls.real_device_pass_claim_allowed` stays `false`" in matrix
    assert "`must_not_be_recorded_as` stays `real-device pass evidence`" in matrix
    assert "`blocked_reason_redacted`" in matrix
    assert "real-device-evidence-checklist.redacted.md" in matrix
    assert "`artifact_collection_rules`" in matrix
    assert "`operator_collection_order`" in matrix
    assert "`remote_screen_wss_origin_redacted`" in matrix
    assert "`remote_input_grant_revoke_evidence`, `remote_input_grant_expiry_evidence`" in matrix
    assert "`real_device_collection_checklist.camera_qr`" in matrix
    assert "`real_device_collection_checklist.actual_https_wss`" in matrix
    assert "`real_device_collection_checklist.certificate_trust`" in matrix
    assert "`real_device_collection_checklist.remote_input_grant_revoke_expiry`" in matrix
    assert "`real_device_collection_checklist.screenshot_log_review`" in matrix
    assert "Leave as `uncollected` unless real camera/QR and device HTTPS/WSS evidence is attached" in matrix
    assert "do not paste token-bearing URLs" in matrix
    assert "raw LAN IPs/hostnames" in matrix
    assert "real_device_pass_claim_allowed=false" in matrix


def test_mobile_lan_wss_preflight_accepts_valid_https_tls_without_overclaiming(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    cert, key = write_lan_tls_material(tmp_path)
    sensitive_cert = tmp_path / ("lan-token=cert-" + "secret-sk-proj-CERT1234.crt")
    sensitive_key = tmp_path / ("lan-secret=key-" + "secret-sk-proj-KEY1234.key")
    cert.rename(sensitive_cert)
    key.rename(sensitive_key)
    cert = sensitive_cert
    key = sensitive_key
    evidence_root = tmp_path / "mobile-lan-wss-preflight"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1"),
            "-Root",
            str(project_root),
            "-BackendHost",
            "192.168.56.10",
            "-BackendPort",
            "9443",
            "-PublicBaseUrl",
            "https://lengrvis.local:9443",
            "-EnableLanTls",
            "-TlsCertFile",
            str(cert),
            "-TlsKeyFile",
            str(key),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        env=_preflight_clean_env(),
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "[ready] LAN/WSS prerequisites are ready for manual Android/emulator evidence collection." in output
    assert "This is still not a real-device pass; record it only as prereq/config evidence." in output
    assert "Redacted real-device checklist path:" in output
    assert str(cert) not in output
    assert str(key) not in output

    summaries = list(evidence_root.rglob("evidence-summary.redacted.json"))
    assert len(summaries) == 1
    summary_text = summaries[0].read_text(encoding="utf-8-sig")
    assert str(cert) not in summary_text
    assert str(key) not in summary_text
    checklists = list(evidence_root.rglob("real-device-evidence-checklist.redacted.md"))
    assert len(checklists) == 1
    checklist_text = checklists[0].read_text(encoding="utf-8-sig")
    assert str(cert) not in checklist_text
    assert str(key) not in checklist_text
    for raw_secret in (
        "cert-secret",
        "key-secret",
        "sk-proj-CERT1234",
        "sk-proj-KEY1234",
        "token=cert-secret",
        "secret=key-secret",
    ):
        assert raw_secret not in output
        assert raw_secret not in summary_text
        assert raw_secret not in checklist_text

    summary = json.loads(summary_text)
    assert summary["result"] == "ready_for_manual_real_device_collection_only"
    assert "must not be recorded as real-device pass evidence" in summary["non_evidence_warning"]
    assert "Manual real-device evidence remains uncollected" in summary["manual_evidence_checklist_warning"]
    assert summary["backend"]["public_base_url_redacted"] == "https://[redacted-host]:9443"
    assert summary["backend"]["websocket_approvals_url_redacted"].startswith("wss://[redacted-host]:9443/")
    assert summary["backend"]["websocket_remote_screen_url_redacted"].startswith("wss://[redacted-host]:9443/")
    assert summary["lan_tls"]["tls_material_valid"] is True
    assert summary["lan_tls"]["tls_host_valid"] is True
    assert summary["lan_tls"]["cert_file_label"] != cert.name
    assert summary["lan_tls"]["key_file_label"] != key.name
    assert "[redacted" in summary["lan_tls"]["cert_file_label"]
    assert "[redacted" in summary["lan_tls"]["key_file_label"]
    assert summary["qr_payload_shape"]["transport_security_status"] == "https_ready_preflight"
    assert summary["qr_payload_shape"]["transport_security_tls_ready"] is True
    assert summary["qr_payload_shape"]["websocket_remote_screen_url_redacted"].startswith("wss://[redacted-host]:9443/")
    assert summary["redacted_evidence_checklist_path"].endswith("real-device-evidence-checklist.redacted.md")
    assert "Actual HTTPS/WSS connection from that device" in summary["next_manual_evidence_needed"]
    template = summary["manual_real_device_evidence_template"]
    assert template["template_status"] == "manual_real_device_evidence_required"
    assert template["real_device_result"] == "uncollected"
    assert template["preflight_blocked"] is False
    assert template["may_be_recorded_as"] == "preflight/config evidence only"
    assert template["must_not_be_recorded_as"] == "real-device pass evidence"
    assert template["blocked_reason_redacted"] == []
    assert template["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert template["claim_controls"]["preflight_ready_is_pass"] is False
    assert template["artifact_collection_rules"]["review_required_before_pass_claim"] is True
    assert "Never paste token-bearing URLs" in template["artifact_collection_rules"]["token_bearing_urls"]
    assert "raw LAN IPs, hostnames, device names" in template["artifact_collection_rules"]["local_only_raw_values"]
    assert any(
        "Keep claim_controls.real_device_pass_claim_allowed=false" in step
        for step in template["operator_collection_order"]
    )
    assert "mobile token" in template["required_redactions"]
    assert "hostnames/IP addresses unless explicitly local-only" in template["required_redactions"]
    assert template["fields"]["https_origin_redacted"] == "https://[redacted-host]:9443"
    assert template["fields"]["approval_wss_origin_redacted"].startswith("wss://[redacted-host]:9443/")
    assert template["fields"]["remote_screen_wss_origin_redacted"].startswith("wss://[redacted-host]:9443/")
    assert template["fields"]["camera_qr_path_evidence"] == "uncollected"
    assert template["fields"]["actual_device_https_wss_evidence"] == "uncollected"
    assert template["fields"]["remote_input_grant_revoke_evidence"] == "uncollected"
    assert template["fields"]["remote_input_grant_expiry_evidence"] == "uncollected"
    assert template["fields"]["artifact_redaction_review"] == "uncollected"
    checklist = template["real_device_collection_checklist"]
    assert checklist["camera_qr"]["status"] == "uncollected"
    assert checklist["actual_https_wss"]["status"] == "uncollected"
    assert checklist["certificate_trust"]["status"] == "uncollected"
    assert checklist["remote_input_grant_revoke_expiry"]["status"] == "uncollected"
    assert checklist["screenshot_log_review"]["status"] == "uncollected"
    assert "Remote screen WebSocket connected over WSS from the device" in checklist["actual_https_wss"]["must_attach"]
    assert (
        "Grant expiry disables input and cannot reconnect with the expired grant"
        in checklist["remote_input_grant_revoke_expiry"]["must_attach"]
    )
    assert "real_device_result: uncollected" in checklist_text
    assert "real_device_pass_claim_allowed=false" in checklist_text
    assert "preflight_ready_is_pass=false" in checklist_text
    assert "This checklist is preflight/config evidence only" in checklist_text


def test_mobile_lan_wss_preflight_blocks_certificate_host_mismatch(project_root: Path, tmp_path: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    cert, key = write_lan_tls_material(tmp_path)
    evidence_root = tmp_path / "mobile-lan-wss-preflight"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1"),
            "-Root",
            str(project_root),
            "-BackendHost",
            "192.168.56.10",
            "-BackendPort",
            "9443",
            "-PublicBaseUrl",
            "https://wrong-host.local:9443",
            "-EnableLanTls",
            "-TlsCertFile",
            str(cert),
            "-TlsKeyFile",
            str(key),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        env=_preflight_clean_env(),
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "[ready]" not in output
    assert "certificate does not cover advertised public host" in output
    assert str(cert) not in output
    assert str(key) not in output

    summaries = list(evidence_root.rglob("evidence-summary.redacted.json"))
    assert len(summaries) == 1
    assert len(list(evidence_root.rglob("real-device-evidence-checklist.redacted.md"))) == 1
    summary_text = summaries[0].read_text(encoding="utf-8-sig")
    assert str(cert) not in summary_text
    assert str(key) not in summary_text
    summary = json.loads(summary_text)
    assert summary["result"] == "blocked"
    assert summary["lan_tls"]["tls_material_validation_attempted"] is True
    assert summary["lan_tls"]["tls_material_valid"] is False
    assert summary["lan_tls"]["tls_host_valid"] is False
    assert summary["qr_payload_shape"]["transport_security_status"] == "https_wss_preflight_blocked"
    template = summary["manual_real_device_evidence_template"]
    assert template["real_device_result"] == "uncollected"
    assert template["preflight_blocked"] is True
    assert template["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert any(
        "certificate does not cover advertised public host" in reason for reason in template["blocked_reason_redacted"]
    )
    assert template["fields"]["actual_device_https_wss_evidence"] == "uncollected"
    assert template["real_device_collection_checklist"]["certificate_trust"]["status"] == "uncollected"


def test_mobile_lan_wss_preflight_default_blocked_summary_is_not_real_device_evidence(
    project_root: Path, tmp_path: Path
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    evidence_root = tmp_path / "mobile-lan-wss-preflight"
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        env=_preflight_clean_env(),
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "[blocked] Mobile LAN/WSS prerequisites are not safe to use for release evidence:" in output
    assert "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection" in output
    assert "[ready]" not in output

    summaries = list(evidence_root.rglob("evidence-summary.redacted.json"))
    assert len(summaries) == 1
    checklists = list(evidence_root.rglob("real-device-evidence-checklist.redacted.md"))
    assert len(checklists) == 1
    checklist_text = checklists[0].read_text(encoding="utf-8-sig")
    assert "real_device_result: uncollected" in checklist_text
    assert "real_device_pass_claim_allowed=false" in checklist_text
    assert "Blocked Reasons Redacted" in checklist_text
    summary = json.loads(summaries[0].read_text(encoding="utf-8-sig"))
    assert summary["result"] == "blocked"
    assert "must not be recorded as real-device pass evidence" in summary["non_evidence_warning"]
    assert summary["lan_tls"]["tls_material_validation_attempted"] is False
    assert summary["lan_tls"]["tls_host_valid"] is False
    assert summary["qr_payload_shape"]["transport_security_status"] == "https_wss_preflight_blocked"
    assert summary["qr_payload_shape"]["transport_security_tls_ready"] is False
    template = summary["manual_real_device_evidence_template"]
    assert template["template_status"] == "manual_real_device_evidence_required"
    assert template["real_device_result"] == "uncollected"
    assert template["preflight_blocked"] is True
    assert template["must_not_be_recorded_as"] == "real-device pass evidence"
    assert template["claim_controls"]["preflight_ready_is_pass"] is False
    assert any("loopback-only" in reason for reason in template["blocked_reason_redacted"])
    assert template["fields"]["camera_qr_path_evidence"] == "uncollected"
    assert template["real_device_collection_checklist"]["camera_qr"]["status"] == "uncollected"


def test_start_app_recent_log_summary_redacts_secrets(project_root: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "backend.65535.redaction-test.err.log"
    raw_secrets = [
        "sk-test-startup-1234567890abcdef",
        "bearer-startup-secret-1234567890",
        "cookie-startup-secret-1234567890",
        "url-startup-token-1234567890",
        "startup-oauth-code-1234567890",
        "startup-client-secret-1234567890",
        "startup-desktop-token-1234567890",
    ]
    log_path.write_text(
        "\n".join(
            [
                f"api_key={raw_secrets[0]}",
                f"Authorization: Bearer {raw_secrets[1]}",
                f"Cookie: session={raw_secrets[2]}",
                "callback=https://example.test/oauth"
                f"?token={raw_secrets[3]}&code={raw_secrets[4]}&client_secret={raw_secrets[5]}",
                f"X-Lengrvis-Desktop-Token={raw_secrets[6]}",
            ]
        ),
        encoding="utf-8",
    )

    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(project_root / "scripts" / "start_app.ps1"),
                "-PrintRecentLogs",
            ],
            cwd=project_root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=20,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        for secret in raw_secrets:
            assert secret not in output
        assert "[redacted]" in output
    finally:
        log_path.unlink(missing_ok=True)
