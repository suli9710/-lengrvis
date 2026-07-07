from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


def _powershell() -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")
    return powershell


def _run_powershell(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            *args,
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )


def _latest_json(root: Path, pattern: str) -> dict[str, object]:
    matches = list(root.rglob(pattern))
    assert len(matches) == 1
    return json.loads(matches[0].read_text(encoding="utf-8-sig"))


def test_android_release_gate_preflight_subgates_are_not_passed(project_root: Path, tmp_path: Path) -> None:
    output_root = tmp_path / "android-release-gate"
    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-PreflightOnly",
        "-OutputRoot",
        str(output_root),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    assert packet["status"] == "preflight_ready_not_release"
    assert packet["release_ready"] is False
    assert packet["preflight_only"] is True
    assert packet["source_config"]["passed"] is True
    assert packet["source_config"]["issues"] == []
    assert packet["artifact_gate"]["evaluated"] is False
    assert packet["artifact_gate"]["passed"] is False
    assert packet["real_device_gate"]["evaluated"] is False
    assert packet["real_device_gate"]["passed"] is False
    assert packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False


def test_android_real_device_template_cannot_satisfy_strict_gate(project_root: Path, tmp_path: Path) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00" + (b"\0" * 600_000))
        archive.writestr("classes.dex", b"dex\n" + (b"\0" * 600_000))
    artifact_sha = hashlib.sha256(apk.read_bytes()).hexdigest()

    template_root = tmp_path / "android-real-device-template"
    template_result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "collect_android_real_device_evidence_template.ps1"),
        "-Root",
        str(project_root),
        "-EvidenceRoot",
        str(template_root),
        "-ArtifactLabel",
        "lengrvis-preview.apk",
        "-ArtifactSha256",
        artifact_sha,
        "-DeviceLabel",
        "pixel-emulator-redacted",
        "-BackendBuildLabel",
        "backend-build-redacted",
        "-BlockedReason",
        f"EAS preview build blocked at {tmp_path}\\private token=build-secret",
    )
    template_output = template_result.stdout + template_result.stderr
    assert template_result.returncode == 0, template_output
    assert str(tmp_path) not in template_output
    assert "build-secret" not in template_output

    template_packet = _latest_json(template_root, "android-real-device-evidence.redacted.template.json")
    template_text = json.dumps(template_packet, ensure_ascii=False)
    assert str(tmp_path) not in template_text
    assert "build-secret" not in template_text
    assert template_packet["real_device_result"] == "uncollected"
    assert "[redacted" in template_packet["blocked_reason"]
    build_environment = template_packet["build_environment"]
    assert isinstance(build_environment["java_available"], bool)
    assert isinstance(build_environment["adb_available"], bool)
    assert isinstance(build_environment["android_sdk_env_present"], bool)
    assert isinstance(build_environment["native_android_project_present"], bool)
    assert isinstance(build_environment["local_apk_build_ready"], bool)
    assert build_environment["local_eas_cli_declared"] is True
    assert build_environment["local_eas_cli_declared_version"]
    assert build_environment["eas_cloud_auth_verified"] is False
    assert "whoami" in build_environment["eas_cloud_auth_verification"]
    assert template_packet["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert template_packet["claim_controls"]["apk_installed"] is False
    assert template_packet["claim_controls"]["https_wss_verified"] is False
    assert template_packet["claim_controls"]["remote_input_verified"] is False
    assert template_packet["claim_controls"]["artifact_redaction_reviewed"] is False
    assert template_packet["redaction"]["tokens_absent"] is False

    gate_root = tmp_path / "android-release-gate"
    evidence_path = next(template_root.rglob("android-real-device-evidence.redacted.template.json"))
    gate_result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-RealDeviceEvidencePath",
        str(evidence_path),
        "-OutputRoot",
        str(gate_root),
    )
    gate_output = gate_result.stdout + gate_result.stderr

    assert gate_result.returncode == 1, gate_output
    gate_packet = _latest_json(gate_root, "android-release-gate.redacted.json")
    assert gate_packet["status"] == "blocked"
    assert gate_packet["release_ready"] is False
    assert gate_packet["artifact_gate"]["evaluated"] is True
    assert gate_packet["artifact_gate"]["passed"] is True
    assert gate_packet["real_device_gate"]["evaluated"] is True
    assert gate_packet["real_device_gate"]["passed"] is False
    assert gate_packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert gate_packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False

    issue_codes = {issue["code"] for issue in gate_packet["real_device_gate"]["issues"]}
    assert "real_device_result_not_passed" in issue_codes
    assert "review_status_not_passed" in issue_codes
    assert "real_device_claim_flag_missing" in issue_codes
    assert "redaction_flag_missing" in issue_codes


def test_android_pr_ci_has_manifest_and_connected_tls_regression_contract(project_root: Path) -> None:
    ci = (project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package = json.loads((project_root / "mobile" / "package.json").read_text(encoding="utf-8"))
    lan_tls_smoke = (project_root / "mobile" / "scripts" / "android-lan-tls-smoke.cjs").read_text(encoding="utf-8")

    scripts = package["scripts"]
    assert scripts["smoke:android-manifest-resources"] == "node scripts/android-manifest-resources-smoke.cjs"
    assert scripts["smoke:android-lan-tls"] == "node scripts/android-lan-tls-smoke.cjs"
    assert scripts["gate:android-instrumentation-compile"] == (
        "node scripts/android-lan-tls-smoke.cjs --compile-instrumentation"
    )
    assert scripts["gate:android-connected-lan-tls"] == "node scripts/android-lan-tls-smoke.cjs --connected"

    assert "npm --prefix mobile run smoke:android-hardening-plugin" in ci
    assert "npm --prefix mobile run smoke:android-manifest-resources" in ci
    assert "npm --prefix mobile run smoke:android-lan-tls" in ci
    assert ":app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace" in ci
    assert "connectedDebugAndroidTest" not in ci

    assert "connectedDebugAndroidTest" in lan_tls_smoke
    assert "LENGRVIS_ANDROID_LAN_TLS_BASE_URL" in lan_tls_smoke
    assert "LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256" in lan_tls_smoke
    assert "This release/evidence gate is intentionally not run by PR CI." in lan_tls_smoke
