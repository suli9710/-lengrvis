from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import check_source_size as checker


def test_source_size_scan_sorts_largest_first(tmp_path: Path) -> None:
    backend_file = tmp_path / "backend" / "app" / "large.py"
    desktop_file = tmp_path / "desktop" / "src" / "small.ts"
    backend_file.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    backend_file.write_text("a\nb\nc\n", encoding="utf-8")
    desktop_file.write_text("x\n", encoding="utf-8")

    sizes = checker.scan_source_sizes(tmp_path)

    assert [(item.path, item.lines, item.area) for item in sizes] == [
        ("backend/app/large.py", 3, "backend"),
        ("desktop/src/small.ts", 1, "desktop"),
    ]


def test_source_size_scan_skips_build_artifacts(tmp_path: Path) -> None:
    source_file = tmp_path / "backend" / "app" / "service.py"
    cache_file = tmp_path / "backend" / "app" / "__pycache__" / "service.py"
    source_file.parent.mkdir(parents=True)
    cache_file.parent.mkdir(parents=True)
    source_file.write_text("pass\n", encoding="utf-8")
    cache_file.write_text("cached\ncached\n", encoding="utf-8")

    sizes = checker.scan_source_sizes(tmp_path)

    assert [item.path for item in sizes] == ["backend/app/service.py"]


def test_source_size_summary_tracks_trend_metrics(tmp_path: Path) -> None:
    backend_file = tmp_path / "backend" / "app" / "large.py"
    desktop_file = tmp_path / "desktop" / "src" / "medium.ts"
    script_file = tmp_path / "scripts" / "small.py"
    backend_file.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    script_file.parent.mkdir(parents=True)
    backend_file.write_text("a\n" * 10, encoding="utf-8")
    desktop_file.write_text("b\n" * 4, encoding="utf-8")
    script_file.write_text("c\n", encoding="utf-8")

    summary = checker.summarize_source_sizes(checker.scan_source_sizes(tmp_path))

    assert summary.source_files == 3
    assert summary.total_lines == 15
    assert summary.p95_lines == 10
    assert summary.max_file is not None
    assert summary.max_file.path == "backend/app/large.py"
    assert summary.by_area["backend"].files == 1
    assert summary.by_area["desktop"].max_lines == 4
    assert summary.by_area["scripts"].lines == 1


def test_source_size_cli_reports_trend_metrics(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    backend_file = tmp_path / "backend" / "app" / "large.py"
    desktop_file = tmp_path / "desktop" / "src" / "small.ts"
    backend_file.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    backend_file.write_text("a\nb\nc\n", encoding="utf-8")
    desktop_file.write_text("x\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/check_source_size.py", "--root", str(tmp_path), "--top", "1"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Scanned source files:" in result.stdout
    assert "Total source lines:" in result.stdout
    assert "P95 file size:" in result.stdout
    assert "Area summary:" in result.stdout
    assert "Largest source files:" in result.stdout

    json_result = subprocess.run(
        [sys.executable, "scripts/check_source_size.py", "--root", str(tmp_path), "--top", "1", "--json"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(json_result.stdout)

    assert json_result.returncode == 0, json_result.stdout + json_result.stderr
    assert payload["source_files"] == 2
    assert payload["total_lines"] == 4
    assert payload["p95_lines"] == 3
    assert payload["max_file"]["path"] == "backend/app/large.py"
    assert payload["by_area"]["backend"]["max_lines"] == 3
