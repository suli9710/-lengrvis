"""Report source-file size hotspots with optional fail-closed thresholds."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    ROOT / "backend" / "app",
    ROOT / "desktop" / "src",
    ROOT / "mobile" / "src",
    ROOT / "mobile" / "app",
    ROOT / "scripts",
)
TEXT_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".ps1"}
SKIP_PARTS = {"__pycache__", "node_modules", "dist", "build", ".tmp"}


@dataclass(frozen=True)
class SourceFileSize:
    path: str
    lines: int
    area: str


@dataclass(frozen=True)
class SourceAreaSizeSummary:
    files: int
    lines: int
    max_lines: int


@dataclass(frozen=True)
class SourceSizeSummary:
    source_files: int
    total_lines: int
    p95_lines: int
    max_file: SourceFileSize | None
    by_area: dict[str, SourceAreaSizeSummary]


def iter_source_files(root: Path, source_roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for source_root in source_roots:
        if not source_root.exists():
            continue
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_EXTENSIONS:
                continue
            if any(part in SKIP_PARTS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return sorted(files)


def scan_source_sizes(
    root: Path = ROOT,
    source_roots: tuple[Path, ...] | None = None,
) -> list[SourceFileSize]:
    roots = source_roots or source_roots_for(root)
    sizes: list[SourceFileSize] = []
    for path in iter_source_files(root, roots):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        relative = path.relative_to(root)
        sizes.append(
            SourceFileSize(
                path=relative.as_posix(),
                lines=line_count,
                area=source_area(relative),
            )
        )
    return sorted(sizes, key=lambda item: (-item.lines, item.path))


def summarize_source_sizes(sizes: list[SourceFileSize]) -> SourceSizeSummary:
    sorted_line_counts = sorted(item.lines for item in sizes)
    p95_index = max(0, math.ceil(len(sorted_line_counts) * 0.95) - 1) if sorted_line_counts else 0
    area_items: dict[str, list[SourceFileSize]] = {}
    for item in sizes:
        area_items.setdefault(item.area, []).append(item)
    by_area = {
        area: SourceAreaSizeSummary(
            files=len(items),
            lines=sum(item.lines for item in items),
            max_lines=max((item.lines for item in items), default=0),
        )
        for area, items in sorted(area_items.items())
    }
    return SourceSizeSummary(
        source_files=len(sizes),
        total_lines=sum(item.lines for item in sizes),
        p95_lines=sorted_line_counts[p95_index] if sorted_line_counts else 0,
        max_file=sizes[0] if sizes else None,
        by_area=by_area,
    )


def source_roots_for(root: Path) -> tuple[Path, ...]:
    return (
        root / "backend" / "app",
        root / "desktop" / "src",
        root / "mobile" / "src",
        root / "mobile" / "app",
        root / "scripts",
    )


def source_area(relative_path: Path) -> str:
    first = relative_path.parts[0] if relative_path.parts else "other"
    if first in {"backend", "desktop", "mobile", "scripts"}:
        return first
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--max-lines", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    sizes = scan_source_sizes(root)
    top = sizes[: max(args.top, 0)]
    violations = [item for item in sizes if args.max_lines and item.lines > args.max_lines]
    source_summary = summarize_source_sizes(sizes)
    summary = {
        "ok": not violations,
        **asdict(source_summary),
        "max_lines": args.max_lines,
        "largest": [asdict(item) for item in top],
        "violations": [asdict(item) for item in violations],
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Source size check {'passed' if summary['ok'] else 'failed'}.")
        print(f"Scanned source files: {summary['source_files']}")
        print(f"Total source lines: {summary['total_lines']}")
        print(f"P95 file size: {summary['p95_lines']} lines")
        if source_summary.max_file:
            print(f"Largest file: {source_summary.max_file.path}: {source_summary.max_file.lines} lines")
        if args.max_lines:
            print(f"Max lines threshold: {args.max_lines}")
        if source_summary.by_area:
            print("Area summary:")
            for area, area_summary in source_summary.by_area.items():
                print(
                    f" - {area}: {area_summary.files} files, "
                    f"{area_summary.lines} lines, max {area_summary.max_lines}"
                )
        print("Largest source files:")
        for item in top:
            print(f" - {item.path}: {item.lines} lines")
        if violations:
            print("Files above threshold:")
            for item in violations:
                print(f" - {item.path}: {item.lines} lines")

    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
