from __future__ import annotations

import json
import re
from pathlib import Path


def _version_tuple(version: str) -> tuple[int, ...]:
    release = re.split(r"[-+]", version, maxsplit=1)[0]
    return tuple(int(part) for part in release.split(".") if part.isdigit())


def _assert_minimum_version(package_name: str, version: str, minimum: str) -> None:
    assert _version_tuple(version) >= _version_tuple(minimum), (
        f"{package_name} must stay at or above {minimum}; lockfile has {version}"
    )


def _normalize_python_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_requirement_name_and_version(line: str) -> tuple[str, str] | None:
    clean_line = line.split("#", 1)[0].strip()
    if not clean_line or clean_line.startswith("-"):
        return None
    clean_line = clean_line.split(";", 1)[0].strip()
    match = re.match(
        r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]+\])?\s*==\s*(?P<version>[^\s]+)",
        clean_line,
    )
    if not match:
        return None
    return (_normalize_python_package_name(match.group("name")), match.group("version"))


def _python_direct_pins(requirements_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in requirements_text.splitlines():
        parsed = _python_requirement_name_and_version(line)
        if parsed is not None:
            pins[parsed[0]] = parsed[1]
    return pins


def _assert_version_satisfies(package_name: str, version: str, specifier: str) -> None:
    for operator, boundary in re.findall(r"(>=|<=|==|>|<)\s*([0-9][^,\s]*)", specifier):
        version_tuple = _version_tuple(version)
        boundary_tuple = _version_tuple(boundary)
        assert {
            ">=": version_tuple >= boundary_tuple,
            "<=": version_tuple <= boundary_tuple,
            ">": version_tuple > boundary_tuple,
            "<": version_tuple < boundary_tuple,
            "==": version_tuple == boundary_tuple,
        }[operator], f"{package_name}=={version} must satisfy {specifier}"


def _locked_versions(lockfile: dict[str, object], package_name: str) -> list[str]:
    packages = lockfile["packages"]
    assert isinstance(packages, dict)
    versions: list[str] = []
    for path, metadata in packages.items():
        normalized_path = str(path).replace("\\", "/")
        if normalized_path.split("/")[-1] != package_name:
            continue
        assert isinstance(metadata, dict)
        version = metadata.get("version")
        assert isinstance(version, str), f"{normalized_path} is missing a package version"
        versions.append(version)
    return versions


def test_desktop_high_severity_audit_gate_stays_in_ci_and_security_audit(
    project_root: Path,
) -> None:
    ci = (project_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    security_audit = (project_root / ".github" / "workflows" / "security-audit.yml").read_text(encoding="utf-8")
    audit_script = (project_root / "scripts" / "run_dependency_audit.ps1").read_text(encoding="utf-8")

    assert "npm --prefix desktop audit --audit-level=high" in ci
    assert "npm --prefix mobile audit --audit-level=high" in ci
    assert "npm run audit:deps" in security_audit
    assert "npm audit --audit-level=$AuditLevel" in audit_script
    for python_lock in (
        "backend/requirements-lock.txt",
        "requirements-dev-lock.txt",
        "backend/requirements-build-lock.txt",
        "scripts/acceleration-requirements-lock.txt",
    ):
        assert python_lock in audit_script


def test_secret_scan_uses_strict_config_and_bypasses_line_fingerprint_ignore(
    project_root: Path,
) -> None:
    package_json = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    pre_commit = (project_root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    security_audit = (project_root / ".github" / "workflows" / "security-audit.yml").read_text(encoding="utf-8")
    secret_scan = (project_root / "scripts" / "secret_scan.ps1").read_text(encoding="utf-8")

    scripts = package_json["scripts"]
    assert scripts["security:secrets"] == "powershell -ExecutionPolicy Bypass -File ./scripts/secret_scan.ps1"
    assert "repo: local" in pre_commit
    assert "scripts/secret_scan.ps1" in pre_commit
    assert "scripts/secret_scan.ps1 -Staged" in pre_commit
    assert ".gitleaks-ci.toml" in secret_scan
    assert ".gitleaks.toml" not in secret_scan
    assert "[switch]$Staged" in secret_scan
    assert '$gitleaksVersion = "8.30.1"' in secret_scan
    assert "gitleaks $gitleaksVersion is required" in secret_scan
    assert "github.com/zricethezav/gitleaks/v8@v$gitleaksVersion" in secret_scan
    assert '"--gitleaks-ignore-path", $emptyIgnore' in secret_scan
    assert "core.quotepath=false" in secret_scan
    assert "ls-files -z --cached" in secret_scan
    assert "checkout-index --all --prefix=$checkoutPrefix" in secret_scan
    assert "ls-files -z --others --exclude-standard" in secret_scan
    assert "lengrvis-gitleaks-source" in secret_scan
    assert '@("dir") + $commonArgs + @($sourceSnapshot)' in secret_scan
    assert "gitleaks-empty-ignore" in security_audit
    assert 'dir --config .gitleaks-ci.toml --gitleaks-ignore-path "$empty_ignore"' in security_audit
    assert "github.com/zricethezav/gitleaks/v8@v8.30.1" in security_audit
    assert ".gitleaks-ci.toml" in security_audit
    assert ".gitleaks.toml" not in security_audit
    assert '--log-opts="--all"' in security_audit
    strict_config = (project_root / ".gitleaks-ci.toml").read_text(encoding="utf-8")
    historical_ignore = (project_root / ".gitleaksignore").read_text(encoding="utf-8")
    assert "[allowlist]" not in strict_config
    assert "[[allowlists]]" in strict_config
    assert 'condition = "AND"' in strict_config
    assert "^backend/tests/" in strict_config
    assert "^desktop/scripts/.*smoke.*\\.cjs$" in strict_config
    assert "^test_data/" in strict_config
    assert "ci-" + "audit-hmac-secret" not in strict_config
    assert "token=abcdef12" + "34567890" not in strict_config
    assert "sk-" + "proposedapikeyvalue1234567890" not in strict_config
    assert "hunter2-" + "proposed-secret" not in strict_config
    assert "private-key" in historical_ignore
    assert "Historical test-only TLS fixture private key" in strict_config
    assert "Historical task-result redaction fixture" in strict_config
    assert "19671fb2c05d65d9ea1628e4afe2e1ec72af57ad" in strict_config
    assert r"^desktop/src/renderer/features/task-results/taskResultTimeline\.test\.ts$" in strict_config
    # Keep the historical exception tied to the exact redaction fixture; a
    # path- or commit-only exception would make history scanning permissive.
    historical_fixture_pattern = r"token=abc123[4]56789"
    assert historical_fixture_pattern in strict_config
    assert re.search(historical_fixture_pattern, "token=" + "abc123456789")
    assert "-----BEGIN RSA PRIVATE KEY-----" not in (project_root / "backend/tests/tls_test_material.py").read_text(
        encoding="utf-8"
    )


def test_dev_toolchain_uses_the_patched_pytest_release(project_root: Path) -> None:
    requirements = (project_root / "requirements-dev.txt").read_text(encoding="utf-8")
    lock = (project_root / "requirements-dev-lock.txt").read_text(encoding="utf-8")

    # Keep the advisory-fixed pytest release reproducible while the repository
    # validates its plugin suite against pytest 9.  A floating <10 constraint
    # can silently move release CI to a newer, unqualified minor release.
    assert "pytest==9.0.3" in requirements
    assert _python_direct_pins(lock)["pytest"] == "9.0.3"


def test_backend_dev_requirements_delegate_to_the_patched_hashed_lock(
    project_root: Path,
) -> None:
    backend_requirements = (
        project_root / "backend" / "requirements-dev.txt"
    ).read_text(encoding="utf-8")

    assert "-r ../requirements-dev-lock.txt" in backend_requirements
    assert "pytest>=8.0,<9.0" not in backend_requirements

    lock_verifier = (
        project_root / "scripts" / "verify_dependency_locks.ps1"
    ).read_text(encoding="utf-8")
    assert "Test-BackendDevRequirementsAlias" in lock_verifier
    assert '"backend\\requirements-dev.txt"' in lock_verifier


def test_desktop_generic_bridge_blocks_sensitive_backend_routes(project_root: Path) -> None:
    shared_ipc = (project_root / "desktop/src/shared/ipc.ts").read_text(encoding="utf-8")
    generic_allowlist = (project_root / "desktop/src/shared/apiRequestAllowlist.ts").read_text(encoding="utf-8")
    typed_handlers = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (project_root / "desktop/src/main").glob("ipc*Handlers.ts")
    )

    for exact_path in (
        "/api/browser/observe",
        "/api/browser/read",
        "/api/browser/read-page",
        "/api/browser/summarize-page",
        "/api/browser/links",
        "/api/browser/extract-links",
        "/api/browser/replay-export",
        "/api/commerce/license/activate",
        "/api/commerce/license/install",
        "/api/commerce/policy/import",
        "/api/context/compact",
        "/api/memories/recall",
        "/api/settings/test-llm-provider",
        "/api/settings/onnx/warmup",
        "/api/settings/onnx/test-generate",
        "/api/settings/onnx/test-embedding",
        "/api/settings/onnx/test-ocr",
        "/api/settings/onnx/test-image-embedding",
    ):
        assert f'"{exact_path}"' not in generic_allowlist

    for typed_path in (
        "/api/browser/observe",
        "/api/browser/replay-export",
        "/api/commerce/license/activate",
        "/api/commerce/license/install",
        "/api/commerce/policy/import",
        "/api/memories/recall",
        "/api/settings/test-llm-provider",
        "/api/settings/onnx/warmup",
        "/api/settings/onnx/test-generate",
        "/api/settings/onnx/test-embedding",
        "/api/settings/onnx/test-ocr",
        "/api/settings/onnx/test-image-embedding",
    ):
        assert f'"{typed_path}"' in typed_handlers

    for channel_name in (
        "perceptionSuggestionLaunch",
        "hardwareAccelerationSmoke",
        "browserObserve",
        "browserReplayExport",
        "commerceLicenseInstall",
        "commerceLicenseActivate",
        "commercePolicyImport",
        "memoriesSave",
        "memoriesRecall",
        "memoriesForget",
        "settingsTestLlmProvider",
    ):
        assert channel_name in shared_ipc


def test_sbom_includes_all_python_locks_and_npm_locks(project_root: Path) -> None:
    sbom_generator = (project_root / "scripts" / "generate_sbom.py").read_text(encoding="utf-8")

    for source in (
        "backend/requirements-lock.txt",
        "requirements-dev-lock.txt",
        "backend/requirements-build-lock.txt",
        "scripts/acceleration-requirements-lock.txt",
        "desktop/package-lock.json",
        "mobile/package-lock.json",
    ):
        assert source in sbom_generator


def test_acceleration_lock_stays_aligned_with_backend_optional_extras(
    project_root: Path,
) -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
        import tomli as tomllib  # type: ignore[no-redef]

    pyproject = tomllib.loads((project_root / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    acceleration_requirements = (project_root / "scripts" / "acceleration-requirements.txt").read_text(encoding="utf-8")
    acceleration_pins = _python_direct_pins(acceleration_requirements)

    for package_name in ("pip", "wheel", "setuptools"):
        assert package_name not in acceleration_pins, (
            f"{package_name} is a package-management tool, not an acceleration runtime dependency"
        )

    expected_specs = {}
    for extra_name in ("acceleration", "winml", "directml", "openvino"):
        for requirement in extras[extra_name]:
            match = re.match(r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)(?P<specifier>[^;]*)", requirement)
            assert match is not None
            expected_specs[_normalize_python_package_name(match.group("name"))] = match.group("specifier")

    direct_runtime_packages = {
        name: version
        for name, version in acceleration_pins.items()
        if name not in {"huggingface-hub", "numpy", "pillow"}
    }
    for package_name, version in direct_runtime_packages.items():
        assert package_name in expected_specs
        _assert_version_satisfies(package_name, version, expected_specs[package_name])


def test_desktop_lockfile_pins_recent_vite_undici_and_form_data_fixes(
    project_root: Path,
) -> None:
    lockfile = json.loads((project_root / "desktop" / "package-lock.json").read_text(encoding="utf-8"))

    vite_versions = _locked_versions(lockfile, "vite")
    form_data_versions = _locked_versions(lockfile, "form-data")
    undici_versions = _locked_versions(lockfile, "undici")

    assert vite_versions, "desktop lockfile must include vite"
    assert form_data_versions, "desktop lockfile must include form-data"
    assert undici_versions, "desktop lockfile must include undici"

    for version in vite_versions:
        _assert_minimum_version("vite", version, "6.4.3")
    for version in form_data_versions:
        _assert_minimum_version("form-data", version, "4.0.5")
    for version in undici_versions:
        major = _version_tuple(version)[0]
        assert major in {6, 7}, f"undici must stay on an audited major; lockfile has {version}"
        if major == 6:
            _assert_minimum_version("undici", version, "6.25.0")
        else:
            _assert_minimum_version("undici", version, "7.24.4")
