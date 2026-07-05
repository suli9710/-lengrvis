from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import check_exception_boundaries as checker


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
    assert "Scanned source files:" in result.stdout
    assert "Marked broad boundaries:" in result.stdout
    assert "Marked broad boundaries by area:" in result.stdout


def test_exception_boundary_summary_counts_marked_boundaries(tmp_path: Path) -> None:
    backend_file = tmp_path / "backend" / "app" / "service.py"
    desktop_file = tmp_path / "desktop" / "src" / "bridge.ts"
    mobile_file = tmp_path / "mobile" / "src" / "screen.ts"
    backend_file.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    mobile_file.parent.mkdir(parents=True)
    backend_file.write_text(
        "try:\n"
        "    pass\n"
        "except Exception:  # broad-exception-boundary\n"
        "    pass\n",
        encoding="utf-8",
    )
    desktop_file.write_text(
        "try {\n"
        "  run();\n"
        "} catch (error) { // broad-exception-boundary\n"
        "  report(error);\n"
        "}\n",
        encoding="utf-8",
    )
    mobile_file.write_text("export const ok = true;\n", encoding="utf-8")

    result = checker.scan_exception_boundaries(tmp_path)

    assert result.violations == []
    assert result.source_files == 3
    assert result.marked_boundaries == 2
    assert result.marked_boundaries_by_area == {"backend": 1, "desktop": 1}


def test_exception_boundary_scan_reports_unmarked_fixture(tmp_path: Path) -> None:
    source_file = tmp_path / "backend" / "app" / "unsafe.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    pass\n",
        encoding="utf-8",
    )

    result = checker.scan_exception_boundaries(tmp_path)

    assert result.marked_boundaries == 0
    assert [item.replace("\\", "/") for item in result.violations] == [
        "backend/app/unsafe.py:3: unmarked Python broad exception"
    ]
