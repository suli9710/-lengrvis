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
    security_audit = (project_root / ".github" / "workflows" / "security-audit.yml").read_text(
        encoding="utf-8"
    )
    audit_script = (project_root / "scripts" / "run_dependency_audit.ps1").read_text(encoding="utf-8")

    assert "npm --prefix desktop audit --audit-level=high" in ci
    assert "npm --prefix mobile audit --audit-level=high" in ci
    assert "npm run audit:deps" in security_audit
    assert "npm audit --audit-level=$AuditLevel" in audit_script


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
