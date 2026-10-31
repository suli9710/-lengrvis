"""Renderer byte-budget wiring; runtime performance needs separate validation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"


def test_renderer_budget_runs_in_build_and_smoke():
    scripts = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "vite build && node scripts/verify-renderer-bundle-budget.cjs" == scripts["build:renderer"]
    assert "node scripts/renderer-bundle-budget-smoke.cjs" == scripts["smoke:renderer-budget"]
    assert "npm run smoke:renderer-budget" in scripts["smoke"]


def test_renderer_budget_provenance_is_emitted_by_build():
    config = (DESKTOP / "vite.config.ts").read_text(encoding="utf-8")
    assert 'name: "lengrvis-renderer-budget-provenance"' in config
    assert 'apply: "build"' in config
    assert "Object.keys(output.modules)" in config
    assert 'createHash("sha256").update(output.code).digest("hex")' in config
    assert 'fileName: "renderer-budget-modules.json"' in config
    assert "JSON.stringify({ schemaVersion: 1, chunks, hashes })" in config


def test_renderer_byte_budget_boundaries_and_ownership():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute renderer byte-budget smoke")
    result = subprocess.run(
        [node, str(DESKTOP / "scripts" / "renderer-bundle-budget-smoke.cjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "renderer byte budget smoke passed" in result.stdout
