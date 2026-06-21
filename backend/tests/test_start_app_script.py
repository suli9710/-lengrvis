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
    assignment_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("$env:LENGRVIS_ENV =")
    ]

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


def test_start_app_does_not_stop_workspace_owned_full_backend(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Stop-FullBackendIfWorkspaceOwned")
    function_end = text.index("function Stop-WorkspaceProcessOnPort", function_start)
    function_body = text[function_start:function_end]

    assert "Stop-Process" not in function_body
    assert "Stop-VerifiedListenProcess" not in function_body
    assert "为避免误关用户手动启动的服务" in function_body
    assert ".Contains(\"backend.main:full_app\")" not in function_body


def test_start_app_main_backend_reuses_or_blocks_existing_listener(project_root: Path) -> None:
    text = _start_app_text(project_root)
    function_start = text.index("function Start-Backend")
    function_end = text.index("function Start-DesktopShell", function_start)
    function_body = text[function_start:function_end]

    assert "elseif ((Test-WorkspaceProcess $commandLine) -or (Test-UvicornLengrvisBackend $commandLine))" not in function_body
    assert "Stop-VerifiedListenProcess -Port $BackendPort -Process $existing" not in function_body
    assert "if (Test-Health)" in function_body
    assert "为避免误关用户手动启动的服务" in function_body


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
    assert frontend_body.index("if (Test-LengrvisFrontendProcess $commandLine)") < frontend_body.index("Invoke-WebRequest -Uri $FrontendUrl")
    assert "界面服务端口 $FrontendPort 已被占用，但无法复用" in frontend_body


def test_debug_launcher_prints_redacted_summary_not_raw_logs(project_root: Path) -> None:
    text = _debug_cmd_text(project_root)

    assert "-PrintRecentLogs" in text
    assert "\ntype " not in text.lower()


def test_user_launch_docs_point_to_settings_and_debug_not_env_config(project_root: Path) -> None:
    text = _readme_text(project_root)
    quick_start = _markdown_section(text, "## 普通用户快速开始")
    user_entry = _markdown_section(text, "## 普通用户配置与诊断入口")

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
        assert scripts[script_name] == (
            f"powershell -ExecutionPolicy Bypass -File ./scripts/{helper_name}"
        )
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
    assert "needs: [hygiene, backend, desktop, mobile, supply-chain, extension-security]" in ci
    assert "if: always()" in ci
    assert "RELEASE_EVIDENCE_NEEDS_JSON: $ toJson(needs) " in ci
    assert "npm run evidence:current-release" in ci
    assert "name: current-release-evidence" in ci
    assert "path: docs/release/current-release-evidence.md" in ci

    for marker in (
        'docs\\release\\current-release-evidence.md',
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


def test_current_release_evidence_requires_every_ci_gate_success(
    project_root: Path,
    tmp_path: Path,
) -> None:
    needs = {
        gate: {"result": "success"}
        for gate in (
            "hygiene",
            "backend",
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
    assert "| Backend pytest + golden task gate | Backend pytest suite and golden task regression gate | failure |" in text


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
    verify_script = (
        project_root / "desktop" / "scripts" / "verify-signed-build-config.cjs"
    ).read_text(encoding="utf-8")

    assert scripts["verify:signed-build-config"] == "node scripts/verify-signed-build-config.cjs"
    assert "verify:signed-build-config && npm run verify:backend-signature" in scripts["dist:signed"]
    assert "verify:signed-build-config && npm run verify:backend-signature" in scripts["dist:publish"]
    assert "electron-builder.signed.js" in scripts["dist:signed"]
    assert "electron-builder.signed.js --publish always" in scripts["dist:publish"]
    assert "verify:signed-build-config" not in scripts["dist"]
    assert "electron-builder.yml" in scripts["dist"]
    assert not (project_root / "desktop" / "electron-builder.signed.yml").exists()

    assert "REPLACE_" not in signed_config
    assert "endpoint: process.env.AZURE_TRUSTED_SIGNING_ENDPOINT" in signed_config
    assert "codeSigningAccountName: process.env.AZURE_TRUSTED_SIGNING_ACCOUNT_NAME" in signed_config
    assert "certificateProfileName: process.env.AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME" in signed_config
    assert "azureSignOptions" in signed_config
    assert "publisherName" in signed_config
    assert "publisherName: [publisherName]" in signed_config
    assert "verifyUpdateCodeSignature: true" in signed_config
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
    assert "REPLACE_" in verify_script
    assert "Signed Windows distribution configuration is incomplete" in verify_script
    assert "verify the backend binary signature before packaging" in verify_script
    assert "win.azureSignOptions.publisherName" in verify_script
    assert "win.publisherName[0]" in verify_script


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
    assert "Missing non-placeholder environment variable: AZURE_TRUSTED_SIGNING_PUBLISHER_NAME" in output
    assert "Unsigned local builds must use `npm --prefix desktop run dist`" in output
    assert "Signed Windows distribution configuration verified" not in output


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
    settings_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SettingsPanel.tsx").read_text(encoding="utf-8")
    system_info_text = (project_root / "desktop" / "src" / "renderer" / "components" / "SystemInfoPanel.tsx").read_text(encoding="utf-8")

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
    assert "page.locator(\"button\").filter({ hasText: systemCheckPattern })" in text
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
    assert 'function isApiEndpoint(endpoint)' in text
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
    assert "backend evidence observed after renderer bridge submission attempt, but no packaged /api/chat or /api/runs POST was observed; keeping natural-language evidence unsupported" in text
    assert "inferNaturalLanguagePostFromBackend" not in text
    assert "inferred: true" not in text
    assert "$messages.Count -gt 0 -or" not in text
    assert "natural-language command dock displayed clear visible safe failure before submit; no packaged task submission was possible" in text
    assert "visible safe failure is not accepted as natural-language task evidence" in text
    assert "natural-language visible safe failure side-effect check failed" in text
    assert "function Get-CompletionEvidenceSummary" in text
    assert "function Get-ResultQualitySummary" in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/tasks/$taskId/explain"' in text
    assert 'Get-SmokeJson -Url "$BackendUrl/api/tasks/$runTaskId/explain"' in text
    assert "completion_evidence.level=$level result_verified=$resultVerifiedText completion_evidence.signoff=$signoffText" in text
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
    assert "natural-language prompt produced clear visible safe failure copy in the packaged command dock, but no /api/chat or /api/runs POST was observed" in text
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
    assert "Any POST/PUT/PATCH/DELETE, unknown API mutation, diagnostics export, or settings/files/apps mutation during the read-only click fails the smoke" in release_gate
    assert "that pass requires a packaged renderer `/api/chat` or `/api/runs` POST plus backend read-only/system diagnostics task or run evidence" in release_gate
    assert "Visible safe-failure copy is still useful safety evidence when paired with zero side effects, but it is not accepted as natural-language task evidence" in release_gate
    assert "This is submission/task-evidence coverage, not release-candidate completion sign-off" in release_gate
    assert "observes `/api/chat` or `/api/runs` and a related task/run" in release_gate
    assert "If CDP or the packaged renderer cannot be automated, the strict script exits 2 with `[unsupported]`" in release_gate
    assert "packaged renderer DOM automation to click the read-only" in parity
    assert "observed packaged renderer `POST /api/runs`" in parity
    assert "Record this as packaged natural-language command-dock submission plus read-only/system diagnostics task evidence" in parity
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

    assert "`submission`, `task_created`, and `visible_progress` levels are not completed-result evidence" in release_gate
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
    assert "does not replace a real phone/emulator camera/QR path, actual WSS connection, or explicit Android/emulator certificate trust evidence" in release_gate
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
        "docs/qa/backend-test-runtime.md": (
            project_root / "docs" / "qa" / "backend-test-runtime.md"
        ).read_text(encoding="utf-8"),
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
        else:
            assert "132 passed" in text, doc_path
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
        "evidence:mobile-lan-wss",
        "evidence:android-real-device-template",
        "evidence:local-model-template",
        "evidence:diagnostics-review",
        "evidence:distribution-template",
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
            context = text[max(0, match.start() - 700): match.end() + 700].lower()
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
    assert "must_not_claim = \"release-candidate pass\"" in text
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
    assert '$naturalLanguageCompletionLevel -eq "completed_result" -and $naturalLanguageResultVerified -and -not $naturalLanguageSignoff' in text
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


def test_android_release_gate_preflight_is_not_release_pass(
    project_root: Path, tmp_path: Path
) -> None:
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
    packet = json.loads(
        next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(
            encoding="utf-8-sig"
        )
    )
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


def test_android_release_gate_redacts_missing_private_paths(
    project_root: Path, tmp_path: Path
) -> None:
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
    packet_text = next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(
        encoding="utf-8-sig"
    )
    markdown_text = next(evidence_root.rglob("android-release-gate.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
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
        issue["message"]
        for section in ("artifact_gate", "real_device_gate")
        for issue in packet[section]["issues"]
    )
    assert "missing.apk" in issue_messages
    assert "missing-evidence.json" in issue_messages


def test_android_release_gate_rejects_fake_apk_even_with_reviewed_evidence(
    project_root: Path, tmp_path: Path
) -> None:
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
    packet = json.loads(
        next(evidence_root.rglob("android-release-gate.redacted.json")).read_text(
            encoding="utf-8-sig"
        )
    )
    assert packet["status"] == "blocked"
    assert packet["release_ready"] is False
    assert packet["android_artifact"]["provided"] is True
    assert packet["android_artifact"]["installable_apk"] is False
    assert packet["android_artifact"]["apk_zip_header_valid"] is False
    assert packet["artifact_gate"]["passed"] is False
    assert packet["real_device_gate"]["passed"] is True
    assert packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False
    assert any(
        issue["code"] == "artifact_not_apk_zip"
        for issue in packet["artifact_gate"]["issues"]
    )


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
        len(re.findall(r"^(?:async\s+def|def)\s+test_", (project_root / path).read_text(encoding="utf-8"), flags=re.MULTILINE))
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
    assert rc_template["latest_redacted_handoff_template"]["handoff_status"] == (
        "not_collected_by_this_packet"
    )
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
    assert active_grant_contract["automated_scope"] == "static source contract markers in mobile UI/client/smoke sources"
    assert active_grant_contract["verify_command"] == "npm --prefix mobile run smoke:remote-input-grant"
    assert active_grant_contract["latest_execution_status"] == "not_run_by_this_packet"
    assert "not evidence that the smoke command was executed by this packet" in active_grant_contract["not_signoff"]
    assert "not proof of a live desktop-to-mobile remote input session" in active_grant_contract["not_signoff"]
    assert "not backend TestClient, desktop smoke, packaged, or clean-machine evidence by itself" in active_grant_contract["not_signoff"]
    assert "Mobile remote-input active-grant contract: fail_closed_source_contract_present" in markdown_text
    assert "latest_execution=not_run_by_this_packet" in markdown_text
    assert "not_signoff=source/client contract only, not live device/WSS" in markdown_text
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["result"] == "ready_for_manual_real_device_collection_only"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["real_device_evidence_status"] == "uncollected_fail_closed"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["real_device_evidence_collected"] is False
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["no_phone_preflight_claim"] == "not_real_device_pass"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["backend"]["public_base_url_redacted"] == "https://[redacted-host]:9443"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["backend"]["websocket_remote_screen_url_redacted"] == "wss://[redacted-host]:9443/ws/remote/screen"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"]["transport_security_status"] == "https_ready_preflight"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"]["websocket_approvals_url_redacted"] == "wss://[redacted-host]:9443/ws/mobile/approvals"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"]["websocket_remote_screen_url_redacted"] == "wss://[redacted-host]:9443/ws/remote/screen"
    assert packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["qr_payload_shape"]["websocket_remote_input_url_redacted"] == "wss://[redacted-host]:9443/ws/remote/input"
    mobile_template = packet["evidence"]["mobile_lan_wss_preflight"]["latest_redacted_summary"]["manual_real_device_evidence_template"]
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
    assert "strict gate remains blocked without installable APK and reviewed real-device evidence" in android_gate["not_signoff"]
    assert "Android release gate: entry_available; latest status=not_collected_by_this_packet" in markdown_text
    assert "not an APK/install/WSS pass created by this packet" in markdown_text
    assert packet["evidence"]["ollama_local_model_contracts"]["contract_count"] == expected_ollama_contracts
    assert packet["evidence"]["ollama_local_model_contracts"]["latest_execution_status"] == "not_run_by_this_packet"
    local_model_template = packet["evidence"]["local_model_clean_machine_template"]["latest_redacted_clean_machine_template"]
    assert local_model_template["found"] is False
    assert local_model_template["clean_machine_signoff"] is False
    assert local_model_template["local_model_install_pass"] is False
    assert local_model_template["local_model_start_pass"] is False
    assert local_model_template["local_model_pull_pass"] is False
    assert local_model_template["local_model_task_smoke_pass"] is False
    assert local_model_template["template_is_clean_machine_pass"] is False
    assert local_model_template["dev_smoke_is_clean_machine_pass"] is False
    assert packet["evidence"]["diagnostics_external_review"]["expected_external_review_status"] == "manual_review_required"
    assert packet["evidence"]["diagnostics_external_review"]["expected_public_safe"] is False
    assert packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["found"] is False
    assert packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["public_safe"] is False
    assert packet["evidence"]["diagnostics_external_review"]["latest_redacted_review_packet"]["external_sharing_allowed"] is False
    assert packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["found"] is False
    assert packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["result_quality_signoff"] is False
    assert packet["evidence"]["result_quality_review"]["latest_redacted_review_packet"]["completed_result_evidence"] is False
    assert packet["evidence"]["settings_local_model_smoke"]["present_artifact_count"] == 4
    assert (
        "It does not create installable Android APK pass or real-device Android remote-control pass"
        in "\n".join(packet["not_clean_machine_or_signoff"])
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
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(
        encoding="utf-8-sig"
    )
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
    packet = json.loads(packet_text)
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {
        item["id"]: item
        for item in packet["release_readiness_blockers"]
    }["android_installable_remote_control"]

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
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(
            encoding="utf-8-sig"
        )
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {
        item["id"]: item
        for item in packet["release_readiness_blockers"]
    }["android_installable_remote_control"]

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
    assert (
        "It does not create installable Android APK pass or real-device Android remote-control pass"
        in "\n".join(packet["not_clean_machine_or_signoff"])
    )


def test_release_evidence_packet_rejects_forged_passed_android_gate_summary(
    project_root: Path, tmp_path: Path
) -> None:
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
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(
            encoding="utf-8-sig"
        )
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {
        item["id"]: item
        for item in packet["release_readiness_blockers"]
    }["android_installable_remote_control"]
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
        next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(
            encoding="utf-8-sig"
        )
    )
    latest = packet["evidence"]["android_release_gate"]["latest_redacted_summary"]
    blocker = {
        item["id"]: item
        for item in packet["release_readiness_blockers"]
    }["android_installable_remote_control"]
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
    assert "non-passed Android gate must include installable Android app release pass in must_not_claim" in mismatch_reasons
    assert "non-passed Android gate must include real-device Android remote-control pass in must_not_claim" in mismatch_reasons
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
    packet_text = next(evidence_root.rglob("release-evidence-packet.redacted.json")).read_text(
        encoding="utf-8-sig"
    )
    markdown_text = next(evidence_root.rglob("release-evidence-packet.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
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
