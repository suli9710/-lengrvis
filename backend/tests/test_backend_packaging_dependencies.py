from __future__ import annotations

import re
from pathlib import Path


def _text(project_root: Path, relative_path: str) -> str:
    return (project_root / relative_path).read_text(encoding="utf-8")


def test_backend_build_requirements_pin_pyinstaller(project_root: Path) -> None:
    text = _text(project_root, "backend/requirements-build.txt")
    requirement_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(requirement_lines) == 1
    assert re.fullmatch(r"pyinstaller==\d+\.\d+\.\d+", requirement_lines[0])


def test_backend_packaging_scripts_install_only_pinned_pyinstaller(project_root: Path) -> None:
    scripts = {
        "scripts/build_backend.ps1": _text(project_root, "scripts/build_backend.ps1"),
        "scripts/build_backend_mac.sh": _text(project_root, "scripts/build_backend_mac.sh"),
    }

    for path, text in scripts.items():
        assert "requirements-build.txt" in text
        assert re.search(r"pip\s+install\s+-r", text), path
        assert not re.search(r"pip\s+install\s+pyinstaller\b", text, flags=re.IGNORECASE), path
        assert "pip show pyinstaller" not in text.lower()
        assert "Failed to install pinned backend build dependencies" in text
