from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_release_profile_example_enables_required_fail_closed_flags() -> None:
    profile = (REPO_ROOT / "config.release.example.yaml").read_text(encoding="utf-8")

    assert "allow_mock_fallback: false" in profile
    assert "strict_state_machine: true" in profile


def test_release_workflows_use_locked_python_and_supported_node_lts() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    setup = (REPO_ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")
    dev_install = "python -m pip install --require-hashes -r requirements-dev-lock.txt"
    workspace_install = "npm ci --ignore-scripts --engine-strict"

    assert dev_install in readme
    assert "python -m pip install -r requirements-dev.txt" not in readme
    assert package["engines"]["node"] == ">=22"
    assert "`Node.js 22+`" in readme
    assert workspace_install in readme
    assert workspace_install in setup

    for workflow in ("release-candidate.yml", "release-publish.yml"):
        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "python -m pip install --require-hashes -r requirements-dev-lock.txt" in text
        assert "python -m pip install -r requirements-dev.txt" not in text
        assert workspace_install in text

    assert workspace_install in (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for workflow in (
        "ci.yml",
        "codeql.yml",
        "release-candidate.yml",
        "release-publish.yml",
        "release-readiness.yml",
        "security-audit.yml",
    ):
        text = (REPO_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert 'node-version: "24"' in text
        assert 'node-version: "20"' not in text

        config = yaml.safe_load(text)
        for job_name, job in config["jobs"].items():
            steps = job.get("steps", [])
            if not any("npm " in str(step.get("run", "")) for step in steps):
                continue
            node_steps = [step for step in steps if "actions/setup-node@" in str(step.get("uses", ""))]
            assert node_steps, f"{workflow}:{job_name} executes npm without setup-node"
            expected_node = (
                "24.11.1"
                if workflow == "release-candidate.yml" and job_name in {"mcp-conformance", "mcp-conformance-evidence"}
                else "24"
            )
            assert {str(step["with"]["node-version"]) for step in node_steps} == {expected_node}


def test_development_lock_inherits_every_backend_runtime_pin() -> None:
    requirements = (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    backend_lock = (REPO_ROOT / "backend" / "requirements-lock.txt").read_text(encoding="utf-8")
    development_lock = (REPO_ROOT / "requirements-dev-lock.txt").read_text(encoding="utf-8")

    assert "-r backend/requirements-lock.txt" in requirements
    assert "--python-version 3.12 --universal --generate-hashes" in development_lock

    pin_pattern = re.compile(r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\;]+)")
    backend_pins = {name.lower().replace("_", "-"): version for name, version in pin_pattern.findall(backend_lock)}
    development_pins = {
        name.lower().replace("_", "-"): version for name, version in pin_pattern.findall(development_lock)
    }

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
