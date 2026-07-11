from __future__ import annotations

import json
from pathlib import Path


def _text(project_root: Path, relative: str) -> str:
    return (project_root / relative).read_text(encoding="utf-8")


def test_mobile_gate_commands_stay_in_ci_evidence_and_local_runner(project_root: Path) -> None:
    ci = _text(project_root, ".github/workflows/ci.yml")
    evidence = _text(project_root, "scripts/generate_current_release_evidence.ps1")
    current_evidence = _text(project_root, "docs/release/current-release-evidence.md")
    local_runner = _text(project_root, "scripts/run_tests.ps1")
    required_markers = (
        "npm exec expo -- install --check",
        "npm --prefix mobile run smoke:consent",
        "npm --prefix mobile run smoke:session-lifecycle",
        "npm --prefix mobile run smoke:push-notifications",
        "npm --prefix mobile run smoke:push-subscription-lifecycle",
        "npm --prefix mobile run smoke:approval-status-label",
        "npm --prefix mobile run smoke:android-hardening-plugin",
        "npm --prefix mobile run smoke:android-lan-tls",
        ":app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace",
    )

    for marker in required_markers:
        assert marker in ci
        assert marker in evidence
        assert marker in current_evidence
        assert marker in local_runner


def test_mobile_expo_patch_dependencies_match_the_supported_sdk_line(project_root: Path) -> None:
    package = json.loads((project_root / "mobile" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((project_root / "mobile" / "package-lock.json").read_text(encoding="utf-8"))

    expected = {
        "expo": "56.0.15",
        "expo-notifications": "56.0.20",
        "expo-router": "56.2.14",
    }
    assert {name: package["dependencies"][name] for name in expected} == expected
    assert {name: lock["packages"][""]["dependencies"][name] for name in expected} == expected


def test_android_prebuild_network_security_smoke_is_wired_into_release_gates(
    project_root: Path,
) -> None:
    command = "npm --prefix mobile run smoke:android-prebuild-network-security"
    package = json.loads((project_root / "mobile" / "package.json").read_text(encoding="utf-8"))

    assert (
        package["scripts"]["smoke:android-prebuild-network-security"]
        == "node scripts/android-prebuild-network-security-smoke.cjs"
    )
    for relative in (
        ".github/workflows/ci.yml",
        "scripts/run_tests.ps1",
        "scripts/generate_current_release_evidence.ps1",
        "docs/release/current-release-evidence.md",
        "scripts/verify_android_release_gate.ps1",
    ):
        assert command in _text(project_root, relative)


def test_gradle_wrapper_distribution_is_checksum_pinned_and_checked_by_lock_gate(
    project_root: Path,
) -> None:
    wrapper = _text(project_root, "mobile/android/gradle/wrapper/gradle-wrapper.properties")
    lock_gate = _text(project_root, "scripts/verify_dependency_locks.ps1")
    expected_sha256 = "b266d5ff6b90eada6dc3b20cb090e3731302e553a27c5d3e4df1f0d76beaff06"

    assert "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.3.1-bin.zip" in wrapper
    assert f"distributionSha256Sum={expected_sha256}" in wrapper
    assert "Test-GradleWrapperDistribution" in lock_gate
    assert "distributionSha256Sum" in lock_gate
    assert "gradle-9.3.1-bin.zip" in lock_gate


def test_codeql_scans_custom_android_kotlin_security_code(project_root: Path) -> None:
    codeql = _text(project_root, ".github/workflows/codeql.yml")

    assert "languages: python,javascript-typescript,java-kotlin" in codeql
