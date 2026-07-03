from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_broad_exception_boundaries_are_reviewed() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/check_exception_boundaries.py"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
