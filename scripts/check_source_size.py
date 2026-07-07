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
TEXT_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".ps1", ".css"}
SKIP_PARTS = {"__pycache__", "node_modules", "dist", "build", ".tmp"}
KNOWN_AREAS = {"backend", "desktop", "desktop_styles", "mobile", "scripts", "other"}


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
    if first == "desktop" and relative_path.suffix == ".css":
        return "desktop_styles"
    if first in {"backend", "desktop", "mobile", "scripts"}:
        return first
    return "other"


def parse_area_thresholds(values: list[str]) -> dict[str, int]:
    thresholds: dict[str, int] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError(
                f"area threshold must use AREA=LINES, got {value!r}"
            )
        area, raw_limit = value.split("=", 1)
        area = area.strip()
        if not area:
            raise argparse.ArgumentTypeError("area threshold area must not be empty")
        if area not in KNOWN_AREAS:
            allowed = ", ".join(sorted(KNOWN_AREAS))
            raise argparse.ArgumentTypeError(
                f"unknown source area {area!r}; expected one of: {allowed}"
            )
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"area threshold for {area!r} must be an integer"
            ) from exc
        if limit <= 0:
            raise argparse.ArgumentTypeError(
                f"area threshold for {area!r} must be positive"
            )
        thresholds[area] = limit
    return thresholds


def threshold_violations(
    sizes: list[SourceFileSize],
    summary: SourceSizeSummary,
    *,
    max_p95_lines: int = 0,
    max_area_max_lines: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    if max_p95_lines and summary.p95_lines > max_p95_lines:
        violations.append(
            {
                "kind": "p95_lines",
                "actual": summary.p95_lines,
                "limit": max_p95_lines,
            }
        )
    area_thresholds = max_area_max_lines or {}
    for area, limit in sorted(area_thresholds.items()):
        area_files = [item for item in sizes if item.area == area]
        if not area_files:
            violations.append(
                {
                    "kind": "missing_area",
                    "area": area,
                    "limit": limit,
                }
            )
            continue
        max_file = max(area_files, key=lambda item: item.lines)
        if max_file.lines > limit:
            violations.append(
                {
                    "kind": "area_max_lines",
                    "area": area,
                    "path": max_file.path,
                    "actual": max_file.lines,
                    "limit": limit,
                }
            )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--max-lines", type=int, default=0)
    parser.add_argument("--max-p95-lines", type=int, default=0)
    parser.add_argument(
        "--max-area-max-lines",
        action="append",
        default=[],
        metavar="AREA=LINES",
        help="Fail if the largest file in an area exceeds the limit.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    try:
        area_thresholds = parse_area_thresholds(args.max_area_max_lines)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    sizes = scan_source_sizes(root)
    top = sizes[: max(args.top, 0)]
    violations = [item for item in sizes if args.max_lines and item.lines > args.max_lines]
    source_summary = summarize_source_sizes(sizes)
    threshold_failures = threshold_violations(
        sizes,
        source_summary,
        max_p95_lines=args.max_p95_lines,
        max_area_max_lines=area_thresholds,
    )
    summary = {
        "ok": not violations and not threshold_failures,
        **asdict(source_summary),
        "max_lines": args.max_lines,
        "max_p95_lines": args.max_p95_lines,
        "max_area_max_lines": area_thresholds,
        "largest": [asdict(item) for item in top],
        "violations": [asdict(item) for item in violations],
        "threshold_violations": threshold_failures,
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
        if args.max_p95_lines:
            print(f"Max P95 threshold: {args.max_p95_lines}")
        if area_thresholds:
            print("Area max-file thresholds:")
            for area, limit in sorted(area_thresholds.items()):
                print(f" - {area}: {limit} lines")
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
        if threshold_failures:
            print("Threshold violations:")
            for item in threshold_failures:
                if item["kind"] == "p95_lines":
                    print(f" - p95_lines: {item['actual']} > {item['limit']}")
                elif item["kind"] == "missing_area":
                    print(f" - {item['area']} has no scanned source files")
                else:
                    print(
                        f" - {item['area']} max file {item['path']}: "
                        f"{item['actual']} > {item['limit']}"
                    )

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
