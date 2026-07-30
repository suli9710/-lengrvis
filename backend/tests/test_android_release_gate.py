from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SIGNER_SHA256 = "ab" * 32
PACKAGE_NAME = "com.lengrvis.approval"
VERSION_NAME = "0.1.2"
VERSION_CODE = 2


def _powershell() -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")
    return powershell


def _run_powershell(
    project_root: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            *args,
        ],
        cwd=project_root,
        env=env,
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


def _write_fake_apk(path: Path) -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00" + (b"\0" * 600_000))
        archive.writestr("classes.dex", b"dex\n" + (b"\0" * 600_000))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_android_sdk_tool_shims(
    root: Path,
    *,
    signer_sha256: str = SIGNER_SHA256,
    v2_verified: bool = True,
    v3_verified: bool = True,
    package_name: str = PACKAGE_NAME,
    version_name: str = VERSION_NAME,
    version_code: int = VERSION_CODE,
    debuggable: bool = False,
    test_only: bool = False,
    allow_backup: bool = False,
    uses_cleartext_traffic: bool = False,
    unsafe_exported_service: bool = False,
) -> tuple[Path, Path]:
    apksigner = root / "apksigner-test-shim.ps1"
    apksigner.write_text(
        "\n".join(
            (
                'Write-Output "Verifies"',
                f'Write-Output "Verified using v2 scheme (APK Signature Scheme v2): {str(v2_verified).lower()}"',
                f'Write-Output "Verified using v3 scheme (APK Signature Scheme v3): {str(v3_verified).lower()}"',
                'Write-Output "Number of signers: 1"',
                f'Write-Output "Signer #1 certificate SHA-256 digest: {signer_sha256}"',
                "exit 0",
            )
        ),
        encoding="utf-8",
    )
    aapt = root / "aapt-test-shim.ps1"
    xmltree_lines = [
        "Write-Output 'E: manifest'",
        "Write-Output '  E: application'",
        f"Write-Output '    A: android:debuggable=\"{str(debuggable).lower()}\"'",
        f"Write-Output '    A: android:testOnly=\"{str(test_only).lower()}\"'",
        f"Write-Output '    A: android:allowBackup=\"{str(allow_backup).lower()}\"'",
        f"Write-Output '    A: android:usesCleartextTraffic=\"{str(uses_cleartext_traffic).lower()}\"'",
        "Write-Output '    E: activity'",
        "Write-Output '      A: android:name=\".MainActivity\"'",
        "Write-Output '      A: android:exported=\"true\"'",
        "Write-Output '      E: intent-filter'",
        "Write-Output '        E: action'",
        "Write-Output '          A: android:name=\"android.intent.action.MAIN\"'",
        "Write-Output '        E: category'",
        "Write-Output '          A: android:name=\"android.intent.category.LAUNCHER\"'",
    ]
    if unsafe_exported_service:
        xmltree_lines.extend(
            [
                "Write-Output '    E: service'",
                "Write-Output '      A: android:name=\".UnsafeService\"'",
                "Write-Output '      A: android:exported=\"true\"'",
            ]
        )
    aapt.write_text(
        "\n".join(
            [
                'if ($args -contains "badging") {',
                (
                    "  Write-Output \"package: name='"
                    f"{package_name}' versionCode='{version_code}' versionName='{version_name}'"
                    '"'
                ),
                *(['  Write-Output "application-debuggable"'] if debuggable else []),
                *(['  Write-Output "application-testOnly"'] if test_only else []),
                "  exit 0",
                "}",
                *xmltree_lines,
                "exit 0",
            ]
        ),
        encoding="utf-8",
    )
    return apksigner, aapt


def _reviewed_app_identity(artifact_sha: str, candidate: dict[str, str]) -> dict[str, object]:
    return {
        "artifact_sha256": artifact_sha,
        "artifact_label_redacted": "lengrvis-preview.apk",
        "build_profile": "preview",
        "eas_build_label_redacted": "eas-preview-build-redacted",
        "package_name": PACKAGE_NAME,
        "version_name": VERSION_NAME,
        "version_code": VERSION_CODE,
        "signer_certificate_sha256": SIGNER_SHA256,
        "provenance": {
            "type": "reviewed-build-record/v1",
            "builder_id": "eas-build-production",
            "build_invocation_id": "eas-preview-build-redacted",
            "source_repository": candidate["repository"],
            "source_commit": candidate["commit"],
            "build_profile": "preview",
            "built_at_utc": "2026-07-10T10:00:00Z",
            "artifact_sha256": artifact_sha,
            "package_name": PACKAGE_NAME,
            "version_name": VERSION_NAME,
            "version_code": VERSION_CODE,
            "signer_certificate_sha256": SIGNER_SHA256,
        },
    }


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
    recommended = {entry["purpose"]: entry for entry in packet["recommended_commands"]}
    assert recommended["connected_lan_tls_gate"]["command"] == "npm --prefix mobile run gate:android-connected-lan-tls"
    assert "not a substitute" in recommended["connected_lan_tls_gate"]["claim_scope"]


def test_strict_android_gate_requires_sealed_candidate_bound_reviewed_evidence(project_root: Path) -> None:
    gate = (project_root / "scripts" / "verify_android_release_gate.ps1").read_text(encoding="utf-8")
    package = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    reviewed_workflow = (project_root / ".github" / "workflows" / "release-reviewed-evidence.yml").read_text(
        encoding="utf-8"
    )
    publish_workflow = (project_root / ".github" / "workflows" / "release-publish.yml").read_text(encoding="utf-8")
    candidate_workflow = (project_root / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")

    assert "verify_android_reviewed_evidence.py" in gate
    assert "--require-candidate-binding" in gate
    assert "apksigner verify --verbose --print-certs" in gate
    assert "android_apk_v2_signature_missing" in gate
    assert "android_apk_v3_signature_missing" in gate
    assert "android_apk_signer_certificate_mismatch" in gate
    assert "android_apk_manifest_identity_mismatch" in gate
    assert "android_explicit_sdk_tool_path_forbidden" in gate
    assert "android_build_tools_root_missing" in gate
    assert "android_apksigner_sha256_mismatch" in gate
    assert "android_apksigner_jar_sha256_mismatch" in gate
    assert "android_aapt_sha256_mismatch" in gate
    assert "android_apk_manifest_xmltree_failed" in gate
    assert "android_apk_debuggable" in gate
    assert "android_apk_test_only" in gate
    assert "android_apk_allow_backup_not_disabled" in gate
    assert "android_apk_cleartext_traffic_not_disabled" in gate
    assert "android_apk_unsafe_exported_component" in gate
    assert "test_only_android_sdk_tools" in gate
    assert "android_artifact_provenance_not_verified" in gate
    assert "android_reviewed_evidence_contract_invalid" in gate
    assert "reviewed_evidence_contract" in gate
    assert "LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256" in reviewed_workflow
    assert "-ExpectedSignerCertificateSha256" in reviewed_workflow
    assert "LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256" in publish_workflow
    assert "LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY" in reviewed_workflow
    assert "LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY" in publish_workflow
    for workflow in (candidate_workflow, reviewed_workflow, publish_workflow):
        assert "LENGRVIS_REVIEWED_EVIDENCE_PRIVATE_KEY" not in workflow
        assert "LENGRVIS_RELEASE_EVIDENCE_HMAC_SECRET" not in workflow
    for variable in (
        "LENGRVIS_ANDROID_BUILD_TOOLS_VERSION",
        "LENGRVIS_ANDROID_APKSIGNER_SHA256",
        "LENGRVIS_ANDROID_APKSIGNER_JAR_SHA256",
        "LENGRVIS_ANDROID_AAPT_SHA256",
    ):
        assert variable in reviewed_workflow
        assert variable in publish_workflow
    assert package["scripts"]["evidence:android-real-device-verify"] == (
        "python scripts/verify_android_reviewed_evidence.py"
    )
    assert package["scripts"]["evidence:android-real-device-seal"] == (
        "python scripts/seal_android_real_device_evidence.py"
    )


def test_test_only_sdk_shims_can_parse_sealed_evidence_but_never_prove_release_readiness(
    project_root: Path,
    tmp_path: Path,
) -> None:
    if not (shutil.which("powershell") or shutil.which("pwsh")):
        pytest.skip("PowerShell is not available")

    apk = tmp_path / "lengrvis-preview.apk"
    artifact_sha = _write_fake_apk(apk)
    apksigner, aapt = _write_android_sdk_tool_shims(tmp_path)
    candidate = {
        "commit": "e" * 40,
        "build_identifier": f"rc-98765-1-{'e' * 40}",
        "repository": "lengrvis/mavris",
        "ci_run_id": "98765",
        "ci_run_attempt": "1",
    }
    artifact_manifest_entries = [
        {
            "kind": kind,
            "label_redacted": label,
            "sha256": digest_character * 64,
            "size_bytes": size_bytes,
        }
        for kind, label, digest_character, size_bytes in (
            ("adb_install_status", "adb-install.redacted.txt", "1", 101),
            ("backend_log", "backend-session.redacted.log", "2", 202),
            ("device_screenshot", "device-session.redacted.png", "3", 303),
            ("device_video", "device-session.redacted.mp4", "4", 404),
            ("mobile_log", "mobile-session.redacted.log", "5", 505),
        )
    ]
    draft = {
        "artifact_type": "android-real-device-remote-control-evidence",
        "real_device_result": "passed",
        "candidate": candidate,
        "review_status": "reviewed_passed",
        "review": {
            "status": "reviewed_passed",
            "reviewer_label": "qa-reviewer-redacted",
            "reviewed_at_utc": "2026-07-10T12:00:00Z",
            "redaction_reviewed": True,
            "evidence_artifacts_reviewed": True,
        },
        "device": {"kind": "android_emulator", "profile_label_redacted": "pixel-qa-profile"},
        "transport": {
            "https_origin_redacted": "https://[redacted-host]:9443",
            "approval_wss_origin_redacted": "wss://[redacted-host]:9443/ws/mobile/approvals",
            "remote_screen_wss_origin_redacted": "wss://[redacted-host]:9443/ws/remote/screen",
            "remote_input_wss_origin_redacted": "wss://[redacted-host]:9443/ws/remote/input",
        },
        "certificate": {"trust_path_label_redacted": "android-user-ca-redacted"},
        "evidence_artifact_manifest": {
            "version": "sha256-manifest/v1",
            "entries": artifact_manifest_entries,
        },
        "evidence_artifacts_redacted": [entry["label_redacted"] for entry in artifact_manifest_entries],
        "app": _reviewed_app_identity(artifact_sha, candidate),
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
            key: "passed"
            for key in (
                "apk_installed",
                "camera_qr_pairing",
                "https_api_reachability",
                "certificate_trust_path",
                "approval_wss",
                "remote_screen_wss",
                "remote_input_wss",
                "click_input_approval",
                "text_input_approval",
                "key_pagedown_approval",
                "mobile_end_control_readonly",
                "desktop_revoke_readonly",
                "grant_expiry_readonly",
                "background_or_lockscreen_privacy",
                "artifact_redaction_review",
            )
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
    draft_path = tmp_path / "android-review.draft.json"
    sealed_path = tmp_path / "android-review.sealed.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "LENGRVIS_REVIEWED_EVIDENCE_PRIVATE_KEY": ("ed25519:x3U7zbLaWsyVcab-Pj54poMm9ypKbnkIuQHRXidX07w"),
            "LENGRVIS_REVIEWED_EVIDENCE_PUBLIC_KEY": ("ed25519:hfUGqnZ1cdK0uy_TvWPLi8k-wRkMHsd7DPWGGhdLmJE"),
            "LENGRVIS_RELEASE_CANDIDATE_COMMIT": candidate["commit"],
            "LENGRVIS_RELEASE_BUILD_IDENTIFIER": candidate["build_identifier"],
            "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY": candidate["repository"],
            "LENGRVIS_RELEASE_CANDIDATE_RUN_ID": candidate["ci_run_id"],
            "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT": candidate["ci_run_attempt"],
        }
    )
    seal = subprocess.run(
        [
            sys.executable,
            "scripts/seal_android_real_device_evidence.py",
            "--input",
            str(draft_path),
            "--output",
            str(sealed_path),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert seal.returncode == 0, seal.stdout + seal.stderr

    output_root = tmp_path / "android-release-gate"
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_android_release_gate.ps1"),
            "-Root",
            str(project_root),
            "-ArtifactPath",
            str(apk),
            "-RealDeviceEvidencePath",
            str(sealed_path),
            "-RequireCandidateBinding",
            "-ExpectedSignerCertificateSha256",
            SIGNER_SHA256,
            "-ApkSignerPath",
            str(apksigner),
            "-AaptPath",
            str(aapt),
            "-TestOnlyAllowUntrustedSdkTools",
            "-OutputRoot",
            str(output_root),
        ],
        cwd=project_root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    assert packet["status"] == "blocked"
    assert packet["release_ready"] is False
    assert packet["reviewed_evidence_contract"] == {
        "evaluated": True,
        "valid_hash": True,
        "valid_signature": True,
        "candidate_binding_valid": True,
        "artifact_identity_valid": True,
        "artifact_provenance_valid": True,
        "artifact_manifest_valid": True,
        "signing_key_fingerprint_bound": True,
    }
    assert packet["android_artifact"]["apk_signing"]["v2_verified"] is True
    assert packet["android_artifact"]["apk_signing"]["v3_verified"] is True
    assert packet["android_artifact"]["apk_signing"]["signer_identity_verified"] is True
    assert packet["android_artifact"]["manifest_identity"]["matches_source_config"] is True
    assert packet["android_artifact"]["manifest_identity"]["hardening_verified"] is True
    assert packet["android_artifact"]["provenance"]["verified"] is True
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert "test_only_android_sdk_tools" in issue_codes


def test_strict_android_gate_fails_closed_on_v3_or_controlled_signer_mismatch(
    project_root: Path,
    tmp_path: Path,
) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    _write_fake_apk(apk)
    apksigner, aapt = _write_android_sdk_tool_shims(
        tmp_path,
        signer_sha256="cd" * 32,
        v3_verified=False,
    )
    output_root = tmp_path / "android-release-gate"

    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-ApkSignerPath",
        str(apksigner),
        "-AaptPath",
        str(aapt),
        "-TestOnlyAllowUntrustedSdkTools",
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(output_root),
    )

    assert result.returncode == 1, result.stdout + result.stderr
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert "android_apk_v3_signature_missing" in issue_codes
    assert "android_apk_signer_certificate_mismatch" in issue_codes
    assert "android_artifact_provenance_not_verified" in issue_codes
    assert packet["android_artifact"]["apk_signing"]["v2_verified"] is True
    assert packet["android_artifact"]["apk_signing"]["v3_verified"] is False
    assert packet["android_artifact"]["apk_signing"]["signer_identity_verified"] is False


def test_strict_android_gate_rejects_individual_sdk_tool_paths_without_executing_them(
    project_root: Path,
    tmp_path: Path,
) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    _write_fake_apk(apk)
    apksigner, aapt = _write_android_sdk_tool_shims(tmp_path)
    output_root = tmp_path / "android-release-gate"

    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-ApkSignerPath",
        str(apksigner),
        "-AaptPath",
        str(aapt),
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(output_root),
    )

    assert result.returncode == 1, result.stdout + result.stderr
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert "android_explicit_sdk_tool_path_forbidden" in issue_codes
    assert "android_build_tools_root_missing" in issue_codes
    assert packet["android_artifact"]["apk_signing"]["evaluated"] is False
    assert packet["android_artifact"]["manifest_identity"]["evaluated"] is False
    assert packet["release_ready"] is False


def test_strict_android_gate_ignores_path_sdk_tool_shims(project_root: Path, tmp_path: Path) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    _write_fake_apk(apk)
    apksigner_source, aapt_source = _write_android_sdk_tool_shims(tmp_path)
    (tmp_path / "apksigner.ps1").write_text(apksigner_source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "aapt2.ps1").write_text(aapt_source.read_text(encoding="utf-8"), encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    output_root = tmp_path / "android-release-gate"

    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(output_root),
        env=env,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert "android_build_tools_root_missing" in issue_codes
    assert packet["android_artifact"]["apk_signing"]["evaluated"] is False
    assert packet["android_artifact"]["manifest_identity"]["evaluated"] is False


def test_strict_android_gate_requires_build_tools_version_structure_and_protected_digests(
    project_root: Path,
    tmp_path: Path,
) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    _write_fake_apk(apk)
    version = "35.0.0"
    build_tools = tmp_path / "sdk" / "build-tools" / version
    (build_tools / "lib").mkdir(parents=True)
    (build_tools / "source.properties").write_text(
        f"Pkg.UserSrc=false\nPkg.Revision={version}\n",
        encoding="utf-8",
    )
    (build_tools / "apksigner.bat").write_text(
        "@echo off\njava.exe -jar %~dp0\\lib\\apksigner.jar %*\n",
        encoding="utf-8",
    )
    (build_tools / "lib" / "apksigner.jar").write_bytes(b"not-an-sdk-jar")
    (build_tools / "aapt2.exe").write_bytes(b"MZnot-a-real-sdk-tool")
    output_root = tmp_path / "android-release-gate"

    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-AndroidBuildToolsRoot",
        str(build_tools),
        "-ExpectedBuildToolsVersion",
        version,
        "-ExpectedApkSignerSha256",
        "00" * 32,
        "-ExpectedApkSignerJarSha256",
        "11" * 32,
        "-ExpectedAaptSha256",
        "22" * 32,
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(output_root),
    )

    assert result.returncode == 1, result.stdout + result.stderr
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert {
        "android_apksigner_jar_invalid",
        "android_apksigner_sha256_mismatch",
        "android_apksigner_jar_sha256_mismatch",
        "android_aapt_sha256_mismatch",
    }.issubset(issue_codes)
    toolchain = packet["android_artifact"]["sdk_toolchain"]
    assert toolchain["expected_version"] == version
    assert toolchain["source_properties_version"] == version
    assert toolchain["provenance_verified"] is False
    assert packet["android_artifact"]["apk_signing"]["evaluated"] is False
    assert packet["android_artifact"]["manifest_identity"]["evaluated"] is False


def test_final_binary_manifest_hardening_rejects_unsafe_merged_values(
    project_root: Path,
    tmp_path: Path,
) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    _write_fake_apk(apk)
    apksigner, aapt = _write_android_sdk_tool_shims(
        tmp_path,
        debuggable=True,
        test_only=True,
        allow_backup=True,
        uses_cleartext_traffic=True,
        unsafe_exported_service=True,
    )
    output_root = tmp_path / "android-release-gate"

    result = _run_powershell(
        project_root,
        "-File",
        str(project_root / "scripts" / "verify_android_release_gate.ps1"),
        "-Root",
        str(project_root),
        "-ArtifactPath",
        str(apk),
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-ApkSignerPath",
        str(apksigner),
        "-AaptPath",
        str(aapt),
        "-TestOnlyAllowUntrustedSdkTools",
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(output_root),
    )

    assert result.returncode == 1, result.stdout + result.stderr
    packet = _latest_json(output_root, "android-release-gate.redacted.json")
    issue_codes = {issue["code"] for issue in packet["artifact_gate"]["issues"]}
    assert {
        "android_apk_debuggable",
        "android_apk_test_only",
        "android_apk_allow_backup_not_disabled",
        "android_apk_cleartext_traffic_not_disabled",
        "android_apk_unsafe_exported_component",
    }.issubset(issue_codes)
    manifest = packet["android_artifact"]["manifest_identity"]
    assert manifest["xmltree_inspection_succeeded"] is True
    assert manifest["hardening_verified"] is False
    assert manifest["debuggable"] is True
    assert manifest["test_only"] is True
    assert manifest["allow_backup"] is True
    assert manifest["uses_cleartext_traffic"] is True
    assert manifest["unsafe_exported_components"] == ["service:.UnsafeService"]


def test_android_real_device_template_cannot_satisfy_strict_gate(project_root: Path, tmp_path: Path) -> None:
    apk = tmp_path / "lengrvis-preview.apk"
    artifact_sha = _write_fake_apk(apk)
    apksigner, aapt = _write_android_sdk_tool_shims(tmp_path)

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
    assert template_packet["candidate"] == {
        "commit": "uncollected",
        "build_identifier": "uncollected",
        "repository": "uncollected",
        "ci_run_id": "uncollected",
        "ci_run_attempt": "uncollected",
    }
    assert template_packet["evidence"]["payload_sha256"] == ""
    assert template_packet["evidence"]["signature"] == ""
    assert template_packet["app"]["package_name"] == PACKAGE_NAME
    assert template_packet["app"]["version_name"] == VERSION_NAME
    assert template_packet["app"]["version_code"] == VERSION_CODE
    assert template_packet["app"]["provenance"]["type"] == "reviewed-build-record/v1"
    assert template_packet["app"]["provenance"]["builder_id"] == "uncollected"
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
        "-ExpectedSignerCertificateSha256",
        SIGNER_SHA256,
        "-ApkSignerPath",
        str(apksigner),
        "-AaptPath",
        str(aapt),
        "-TestOnlyAllowUntrustedSdkTools",
        "-RequireCandidateBinding",
        "-OutputRoot",
        str(gate_root),
    )
    gate_output = gate_result.stdout + gate_result.stderr

    assert gate_result.returncode == 1, gate_output
    gate_packet = _latest_json(gate_root, "android-release-gate.redacted.json")
    assert gate_packet["status"] == "blocked"
    assert gate_packet["release_ready"] is False
    assert gate_packet["artifact_gate"]["evaluated"] is True
    assert gate_packet["artifact_gate"]["passed"] is False
    assert gate_packet["real_device_gate"]["evaluated"] is True
    assert gate_packet["real_device_gate"]["passed"] is False
    assert gate_packet["claim_controls"]["installable_android_app_claim_allowed"] is False
    assert gate_packet["claim_controls"]["real_device_remote_control_claim_allowed"] is False

    issue_codes = {issue["code"] for issue in gate_packet["real_device_gate"]["issues"]}
    assert "real_device_result_not_passed" in issue_codes
    assert "review_status_not_passed" in issue_codes
    assert "real_device_claim_flag_missing" in issue_codes
    assert "redaction_flag_missing" in issue_codes
    artifact_issue_codes = {issue["code"] for issue in gate_packet["artifact_gate"]["issues"]}
    assert "android_artifact_provenance_not_verified" in artifact_issue_codes


def test_android_pr_ci_has_manifest_and_connected_tls_regression_contract(project_root: Path) -> None:
    ci = (project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    package = json.loads((project_root / "mobile" / "package.json").read_text(encoding="utf-8"))
    lan_tls_smoke = (project_root / "mobile" / "scripts" / "android-lan-tls-smoke.cjs").read_text(encoding="utf-8")
    current_evidence_generator = (project_root / "scripts" / "generate_current_release_evidence.ps1").read_text(
        encoding="utf-8"
    )
    full_test_gate = (project_root / "scripts" / "run_tests.ps1").read_text(encoding="utf-8")

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
    assert "npm --prefix mobile run smoke:android-manifest-resources" in current_evidence_generator
    assert "npm --prefix mobile run smoke:android-lan-tls" in current_evidence_generator
    assert "npm --prefix mobile run smoke:android-manifest-resources" in full_test_gate
    assert ":app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace" in ci
    assert "connectedDebugAndroidTest" not in ci

    assert "connectedDebugAndroidTest" in lan_tls_smoke
    assert "LENGRVIS_ANDROID_LAN_TLS_BASE_URL" in lan_tls_smoke
    assert "LENGRVIS_ANDROID_LAN_TLS_FINGERPRINT_SHA256" in lan_tls_smoke
    assert "LENGRVIS_ANDROID_LAN_TLS_PAIR_CLAIM_SECRET" in lan_tls_smoke
    assert "lengrvisPairClaimSecret" in lan_tls_smoke
    assert "This release/evidence gate is intentionally not run by PR CI." in lan_tls_smoke


def test_android_native_version_name_matches_expo_config_and_is_checked_by_release_gate(
    project_root: Path,
) -> None:
    app_config = json.loads((project_root / "mobile" / "app.json").read_text(encoding="utf-8"))
    android_gradle = (project_root / "mobile" / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    gate = (project_root / "scripts" / "verify_android_release_gate.ps1").read_text(encoding="utf-8")

    version = app_config["expo"]["version"]
    assert f'versionName "{version}"' in android_gradle
    assert "enableV2Signing true" in android_gradle
    assert "enableV3Signing true" in android_gradle
    assert "android_native_version_name_mismatch" in gate
    assert "android_native_version_code_mismatch" in gate
