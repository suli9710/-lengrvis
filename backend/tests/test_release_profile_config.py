from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_profile_example_enables_required_fail_closed_flags() -> None:
    profile = (REPO_ROOT / "config.release.example.yaml").read_text(encoding="utf-8")

    assert "allow_mock_fallback: false" in profile
    assert "strict_state_machine: true" in profile


def test_readme_and_release_workflows_install_single_hashed_development_lock() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    dev_install = "python -m pip install --require-hashes -r requirements-dev-lock.txt"

    assert dev_install in readme
    assert "python -m pip install -r requirements-dev.txt" not in readme

    for workflow in ("release-candidate.yml", "release-publish.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "python -m pip install --require-hashes -r requirements-dev-lock.txt" in text
        assert "python -m pip install -r requirements-dev.txt" not in text


def test_development_lock_inherits_every_backend_runtime_pin() -> None:
    requirements = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    backend_lock = (REPO_ROOT / "backend" / "requirements-lock.txt").read_text(encoding="utf-8")
    development_lock = (REPO_ROOT / "requirements-dev-lock.txt").read_text(encoding="utf-8")

    assert "-r backend/requirements-lock.txt" in requirements
    assert "--python-version 3.12 --universal --generate-hashes" in development_lock

    pin_pattern = re.compile(r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\;]+)")
    backend_pins = {name.lower().replace("_", "-"): version for name, version in pin_pattern.findall(backend_lock)}
    development_pins = {name.lower().replace("_", "-"): version for name, version in pin_pattern.findall(development_lock)}

    assert backend_pins
    assert {name: development_pins.get(name) for name in backend_pins} == backend_pins


def test_all_development_python_install_entrypoints_use_the_hashed_development_lock() -> None:
    for relative_path in (
        "scripts/setup_dev.ps1",
        "scripts/dev.ps1",
        "scripts/install_backend_browsers.ps1",
        ".github/workflows/lint.yml",
    ):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "requirements-dev-lock.txt" in text, relative_path
        assert "--require-hashes" in text, relative_path
