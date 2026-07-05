"""Fail closed on unreviewed broad exception boundaries.

Python has a few places where a process, task, plugin, or optional-runtime
boundary must normalize arbitrary failures into a structured status. JavaScript
`catch` clauses have the same language-level shape. Those boundaries are allowed
only when they are explicitly marked so future reviews can distinguish an
intentional recovery boundary from accidental error swallowing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKER = "broad-exception-boundary"

SCAN_ROOTS = (
    ROOT / "backend" / "app",
    ROOT / "desktop" / "src",
    ROOT / "mobile" / "src",
    ROOT / "mobile" / "app",
)

TEXT_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"}
PY_BROAD_RE = re.compile(r"\bexcept\s+Exception\b")
JS_BROAD_RE = re.compile(r"\bcatch\s*\(\s*(error|err|e)\s*\)")


@dataclass
class ExceptionBoundaryScanResult:
    source_files: int = 0
    marked_boundaries: int = 0
    marked_boundaries_by_area: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)


def scan_roots_for(root: Path) -> tuple[Path, ...]:
    return (
        root / "backend" / "app",
        root / "desktop" / "src",
        root / "mobile" / "src",
        root / "mobile" / "app",
    )


def iter_source_files(scan_roots: tuple[Path, ...] = SCAN_ROOTS) -> list[Path]:
    files: list[Path] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TEXT_EXTENSIONS:
                files.append(path)
    return sorted(files)


def scan_exception_boundaries(
    root: Path = ROOT,
    scan_roots: tuple[Path, ...] | None = None,
) -> ExceptionBoundaryScanResult:
    result = ExceptionBoundaryScanResult()
    for path in iter_source_files(scan_roots or scan_roots_for(root)):
        result.source_files += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            result.violations.append(f"{path.relative_to(root)}: unreadable as UTF-8")
            continue
        for line_number, line in enumerate(lines, start=1):
            next_line = lines[line_number] if line_number < len(lines) else ""
            if PY_BROAD_RE.search(line) and MARKER not in line and MARKER not in next_line:
                result.violations.append(f"{path.relative_to(root)}:{line_number}: unmarked Python broad exception")
            elif PY_BROAD_RE.search(line):
                record_marked_boundary(result, path, root)
            if JS_BROAD_RE.search(line) and MARKER not in line:
                result.violations.append(f"{path.relative_to(root)}:{line_number}: unmarked JS catch boundary")
            elif JS_BROAD_RE.search(line):
                record_marked_boundary(result, path, root)
    return result


def record_marked_boundary(result: ExceptionBoundaryScanResult, path: Path, root: Path) -> None:
    result.marked_boundaries += 1
    area = boundary_area(path, root)
    result.marked_boundaries_by_area[area] = result.marked_boundaries_by_area.get(area, 0) + 1


def boundary_area(path: Path, root: Path) -> str:
    try:
        first = path.relative_to(root).parts[0]
    except ValueError:
        return "external"
    if first in {"backend", "desktop", "mobile"}:
        return first
    return "other"


def main() -> int:
    result = scan_exception_boundaries(ROOT, SCAN_ROOTS)

    if result.violations:
        print("Unreviewed broad exception boundaries found:")
        for violation in result.violations:
            print(f" - {violation}")
        print(f"Add '{MARKER}' only after confirming the boundary converts failures safely.")
        return 1

    print("Exception boundary check passed: no unreviewed broad catches.")
    print(f"Scanned source files: {result.source_files}")
    print(f"Marked broad boundaries: {result.marked_boundaries}")
    print("Marked broad boundaries by area:")
    for area in ("backend", "desktop", "mobile", "other", "external"):
        if area in result.marked_boundaries_by_area:
            print(f" - {area}: {result.marked_boundaries_by_area[area]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
