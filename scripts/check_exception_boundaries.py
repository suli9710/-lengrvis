"""Fail closed on unreviewed broad exception boundaries.

Python has a few places where a process, task, plugin, or optional-runtime
boundary must normalize arbitrary failures into a structured status. JavaScript
`catch` clauses have the same language-level shape. Those boundaries are allowed
only when they are explicitly marked so future reviews can distinguish an
intentional recovery boundary from accidental error swallowing.
"""

from __future__ import annotations

import re
import sys
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


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TEXT_EXTENSIONS:
                files.append(path)
    return sorted(files)


def main() -> int:
    violations: list[str] = []
    for path in iter_source_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            violations.append(f"{path.relative_to(ROOT)}: unreadable as UTF-8")
            continue
        for line_number, line in enumerate(lines, start=1):
            next_line = lines[line_number] if line_number < len(lines) else ""
            if PY_BROAD_RE.search(line) and MARKER not in line and MARKER not in next_line:
                violations.append(f"{path.relative_to(ROOT)}:{line_number}: unmarked Python broad exception")
            if JS_BROAD_RE.search(line) and MARKER not in line:
                violations.append(f"{path.relative_to(ROOT)}:{line_number}: unmarked JS catch boundary")

    if violations:
        print("Unreviewed broad exception boundaries found:")
        for violation in violations:
            print(f" - {violation}")
        print(f"Add '{MARKER}' only after confirming the boundary converts failures safely.")
        return 1

    print("Exception boundary check passed: no unreviewed broad catches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
