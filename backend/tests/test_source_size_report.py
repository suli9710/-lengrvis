from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts import check_source_size as checker


def test_source_size_scan_sorts_largest_first(tmp_path: Path) -> None:
    backend_file = tmp_path / "backend" / "app" / "large.py"
    desktop_style = tmp_path / "desktop" / "src" / "renderer" / "styles.home.css"
    desktop_file = tmp_path / "desktop" / "src" / "small.ts"
    backend_file.parent.mkdir(parents=True)
    desktop_style.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    backend_file.write_text("a\nb\nc\n", encoding="utf-8")
    desktop_style.write_text(".a {}\n.b {}\n", encoding="utf-8")
    desktop_file.write_text("x\n", encoding="utf-8")

    sizes = checker.scan_source_sizes(tmp_path)

    assert [(item.path, item.lines, item.area) for item in sizes] == [
        ("backend/app/large.py", 3, "backend"),
        ("desktop/src/renderer/styles.home.css", 2, "desktop_styles"),
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


def test_source_size_scan_includes_tooling_and_android_native_sources(tmp_path: Path) -> None:
    files = {
        "desktop/scripts/release.cjs": "desktop\n",
        "mobile/scripts/smoke.cjs": "mobile-script\n",
        "mobile/android/app/src/main/java/dev/lengrvis/MainActivity.kt": "class MainActivity\n",
        "mobile/android/app/build.gradle": "android {}\n",
        "mobile/android/settings.gradle.kts": "rootProject.name = \"fixture\"\n",
    }
    for relative, contents in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    sizes = checker.scan_source_sizes(tmp_path)

    assert {(item.path, item.area) for item in sizes} == {
        ("desktop/scripts/release.cjs", "desktop_scripts"),
        ("mobile/scripts/smoke.cjs", "mobile_scripts"),
        ("mobile/android/app/src/main/java/dev/lengrvis/MainActivity.kt", "android_native"),
        ("mobile/android/app/build.gradle", "android_native"),
        ("mobile/android/settings.gradle.kts", "android_native"),
    }


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


def test_source_size_threshold_violations_report_p95_and_area_max(tmp_path: Path) -> None:
    backend_file = tmp_path / "backend" / "app" / "large.py"
    desktop_file = tmp_path / "desktop" / "src" / "medium.ts"
    backend_file.parent.mkdir(parents=True)
    desktop_file.parent.mkdir(parents=True)
    backend_file.write_text("a\n" * 10, encoding="utf-8")
    desktop_file.write_text("b\n" * 4, encoding="utf-8")
    sizes = checker.scan_source_sizes(tmp_path)
    summary = checker.summarize_source_sizes(sizes)

    violations = checker.threshold_violations(
        sizes,
        summary,
        max_p95_lines=5,
        max_area_max_lines={"backend": 8, "desktop": 5},
    )

    assert {"kind": "p95_lines", "actual": 10, "limit": 5} in violations
    assert {
        "kind": "area_max_lines",
        "area": "backend",
        "path": "backend/app/large.py",
        "actual": 10,
        "limit": 8,
    } in violations
    assert not any(item.get("area") == "desktop" for item in violations)


def test_source_size_oversized_file_violations_respect_allowlist(tmp_path: Path) -> None:
    legacy_file = tmp_path / "backend" / "app" / "legacy.py"
    new_file = tmp_path / "backend" / "app" / "new_large.py"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("a\n" * 10, encoding="utf-8")
    new_file.write_text("b\n" * 9, encoding="utf-8")
    sizes = checker.scan_source_sizes(tmp_path)

    violations = checker.oversized_file_violations(
        sizes,
        max_lines=8,
        allow_over_max_lines={"backend/app/legacy.py"},
    )

    assert [(item.path, item.lines) for item in violations] == [
        ("backend/app/new_large.py", 9)
    ]


def test_source_size_area_thresholds_fail_on_unknown_area() -> None:
    try:
        checker.parse_area_thresholds(["backend=100", "unknown=50"])
    except argparse.ArgumentTypeError as exc:
        assert "unknown source area 'unknown'" in str(exc)
    else:
        raise AssertionError("unknown source area should fail closed")


def test_source_size_threshold_violations_report_missing_configured_area(
    tmp_path: Path,
) -> None:
    backend_file = tmp_path / "backend" / "app" / "large.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("a\n" * 10, encoding="utf-8")
    sizes = checker.scan_source_sizes(tmp_path)
    summary = checker.summarize_source_sizes(sizes)

    violations = checker.threshold_violations(
        sizes,
        summary,
        max_area_max_lines={"backend": 20, "desktop": 5},
    )

    assert {
        "kind": "missing_area",
        "area": "desktop",
        "limit": 5,
    } in violations


def test_package_json_exposes_source_size_gate() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    package = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    gate = package["scripts"]["maintainability:gate"]

    assert "scripts/check_source_size.py" in gate
    assert "--max-lines 900" in gate
    assert "--allow-over-max-lines backend/app/services/run_service.py" in gate
    assert "--max-p95-lines 800" in gate
    for threshold in (
        "backend=1400",
        "desktop=900",
        "desktop_scripts=2000",
        "desktop_styles=700",
        "mobile=900",
        "mobile_scripts=1100",
        "android_native=900",
        "scripts=2400",
    ):
        assert f"--max-area-max-lines {threshold}" in gate


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


def test_source_size_cli_fails_closed_on_thresholds(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    backend_file = tmp_path / "backend" / "app" / "large.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("a\n" * 10, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--top",
            "1",
            "--max-p95-lines",
            "5",
            "--max-area-max-lines",
            "backend=8",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Threshold violations:" in result.stdout
    assert "p95_lines: 10 > 5" in result.stdout
    assert "backend max file backend/app/large.py: 10 > 8" in result.stdout

    json_result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--top",
            "1",
            "--max-p95-lines",
            "5",
            "--max-area-max-lines",
            "backend=8",
            "--json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(json_result.stdout)

    assert json_result.returncode == 1
    assert payload["ok"] is False
    assert payload["max_p95_lines"] == 5
    assert payload["max_area_max_lines"] == {"backend": 8}
    assert {"kind": "p95_lines", "actual": 10, "limit": 5} in payload[
        "threshold_violations"
    ]
    assert {
        "kind": "area_max_lines",
        "area": "backend",
        "path": "backend/app/large.py",
        "actual": 10,
        "limit": 8,
    } in payload["threshold_violations"]


def test_source_size_cli_allows_listed_legacy_oversized_file(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    legacy_file = tmp_path / "backend" / "app" / "legacy.py"
    new_file = tmp_path / "backend" / "app" / "new_large.py"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("a\n" * 10, encoding="utf-8")
    new_file.write_text("b\n" * 9, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--top",
            "2",
            "--max-lines",
            "8",
            "--allow-over-max-lines",
            "backend/app/legacy.py",
            "--json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["allow_over_max_lines"] == ["backend/app/legacy.py"]
    assert payload["violations"] == [
        {"path": "backend/app/new_large.py", "lines": 9, "area": "backend"}
    ]


def test_source_size_cli_fails_closed_on_unknown_area(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--max-area-max-lines",
            "renderer=900",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unknown source area 'renderer'" in result.stderr


def test_source_size_cli_fails_closed_on_missing_configured_area(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    backend_file = tmp_path / "backend" / "app" / "large.py"
    backend_file.parent.mkdir(parents=True)
    backend_file.write_text("a\n" * 10, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--max-area-max-lines",
            "desktop=5",
            "--json",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert {
        "kind": "missing_area",
        "area": "desktop",
        "limit": 5,
    } in payload["threshold_violations"]

    human_result = subprocess.run(
        [
            sys.executable,
            "scripts/check_source_size.py",
            "--root",
            str(tmp_path),
            "--max-area-max-lines",
            "desktop=5",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert human_result.returncode == 1
    assert "desktop has no scanned source files" in human_result.stdout
