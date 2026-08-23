from __future__ import annotations

import json
from pathlib import Path

import yaml


def _text(project_root: Path, relative: str) -> str:
    return (project_root / relative).read_text(encoding="utf-8")


def test_mobile_gate_commands_stay_in_ci_evidence_and_local_runner(project_root: Path) -> None:
    ci = _text(project_root, ".github/workflows/ci.yml")
    evidence = _text(project_root, "scripts/generate_current_release_evidence.ps1")
    current_evidence = _text(project_root, "docs/release/current-release-evidence.md")
    local_runner = _text(project_root, "scripts/run_tests.ps1")
    required_markers = (
        "npm exec expo -- install --check",
        "npm --prefix mobile run smoke:eas-cli-compat",
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
    eas = json.loads((project_root / "mobile" / "eas.json").read_text(encoding="utf-8"))

    expected = {
        "expo": "56.0.20",
        "expo-notifications": "56.0.24",
        "expo-router": "56.2.19",
    }
    assert {name: package["dependencies"][name] for name in expected} == expected
    assert {name: lock["packages"][""]["dependencies"][name] for name in expected} == expected
    assert eas["cli"]["version"] == package["devDependencies"]["eas-cli"]
    assert package["overrides"]["eas-cli@22.2.0"] == {
        "minimatch@5.1.2": "5.1.9",
        "ts-deepmerge@6.2.0": "8.0.0",
    }
    assert lock["packages"]["node_modules/minimatch"]["version"] == "5.1.9"
    assert lock["packages"]["node_modules/@oclif/core/node_modules/minimatch"]["version"] == "10.2.6"
    assert lock["packages"]["node_modules/ts-deepmerge"]["version"] == "8.0.0"


def test_eas_cli_runtime_compatibility_patch_is_wired_into_install_and_smokes(
    project_root: Path,
) -> None:
    package = json.loads((project_root / "mobile" / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["postinstall"] == "node scripts/patch-eas-cli-runtime.cjs"
    assert package["scripts"]["smoke:eas-cli-compat"] == "node scripts/eas-cli-compat-smoke.cjs"
    assert package["scripts"]["preflight:android-release"].startswith("npm run smoke:eas-cli-compat && ")
    patch = _text(project_root, "mobile/scripts/patch-eas-cli-runtime.cjs")
    smoke = _text(project_root, "mobile/scripts/eas-cli-compat-smoke.cjs")
    assert "EXPECTED_EAS_VERSION" in patch
    assert "fs.realpathSync(expectedEasPackagePath)" in patch
    assert "outside mobile/node_modules" in patch
    assert "ts_deepmerge_1.merge" in patch
    assert 'process.argv.includes("--check")' in patch
    assert 'requireFromEas("minimatch")' in smoke
    assert "generateAppConfigAsync" in smoke


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
    codeql = yaml.safe_load(_text(project_root, ".github/workflows/codeql.yml"))
    analyze = codeql["jobs"]["analyze"]
    assert analyze["strategy"]["matrix"]["include"] == [
        {"language": "python", "build-mode": "none"},
        {"language": "javascript-typescript", "build-mode": "none"},
        {"language": "java-kotlin", "build-mode": "manual"},
    ]

    steps = analyze["steps"]
    node_setup = next(step for step in steps if step.get("name") == "Set up Node.js for the Android build")
    java_setup = next(step for step in steps if step.get("name") == "Set up JDK 17 for the Android build")
    mobile_install = next(step for step in steps if step.get("name") == "Install locked mobile dependencies")
    kotlin_compile = next(step for step in steps if step.get("name") == "Compile Android Kotlin sources")
    codeql_init = next(step for step in steps if "github/codeql-action/init@" in step.get("uses", ""))
    codeql_analyze = next(step for step in steps if "github/codeql-action/analyze@" in step.get("uses", ""))

    kotlin_only = "${{ matrix.language == 'java-kotlin' }}"
    assert node_setup["if"] == kotlin_only
    assert node_setup["with"]["node-version"] == "20"
    assert java_setup["if"] == kotlin_only
    assert java_setup["with"] == {"distribution": "temurin", "java-version": "17"}
    assert mobile_install["if"] == kotlin_only
    assert mobile_install["run"] == "npm --prefix mobile ci"
    assert codeql_init["with"]["languages"] == "${{ matrix.language }}"
    assert codeql_init["with"]["build-mode"] == "${{ matrix.build-mode }}"
    assert kotlin_compile["if"] == kotlin_only
    assert kotlin_compile["working-directory"] == "mobile/android"
    assert kotlin_compile["run"].startswith("bash ./gradlew ")
    assert ":app:compileDebugKotlin" in kotlin_compile["run"]
    assert ":app:compileDebugAndroidTestKotlin" in kotlin_compile["run"]
    assert codeql_analyze["with"]["category"] == "/language:${{ matrix.language }}"
