"""Verify scoped scanner exceptions preserve detection of literal secrets."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest


def test_generated_butler_fixture_keys_do_not_hide_literal_keys(tmp_path: Path) -> None:
    scanner = shutil.which("gitleaks")
    if scanner is None:
        pytest.skip("gitleaks is required for the scanner behavior regression")
    root = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    key = "LENGRVIS_" + "AUDIT_HMAC_SECRET"
    generated = key + "=secrets.token_hex(32),\n"
    literal = key + '="' + secrets.token_hex(32) + '"\n'
    (scripts / "run_butler_browser_flow.py").write_text(generated + literal, encoding="utf-8")
    (scripts / "other.py").write_text(generated, encoding="utf-8")
    report = tmp_path / "findings.json"
    empty_ignore = tmp_path / "empty-ignore"
    empty_ignore.touch()
    result = subprocess.run(
        [
            scanner,
            "dir",
            "--config",
            str(root / ".gitleaks-ci.toml"),
            "--gitleaks-ignore-path",
            str(empty_ignore),
            "--redact",
            "--no-banner",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            str(scripts),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    findings = json.loads(report.read_text(encoding="utf-8"))
    assert sorted((Path(f["File"]).name, f["StartLine"]) for f in findings) == [
        ("other.py", 1),
        ("run_butler_browser_flow.py", 2),
    ]
