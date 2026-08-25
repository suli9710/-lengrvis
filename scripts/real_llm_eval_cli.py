"""Command-line contract for the real-LLM evaluation harness."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.real_llm_benchmark_catalog import MIN_REAL_LLM_BENCHMARK_CASES


def parse_args(
    argv: list[str] | None = None,
    *,
    default_report_dir: Path,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay golden tasks against the real configured LLM provider."
    )
    parser.add_argument("--report-dir", default=str(default_report_dir))
    parser.add_argument(
        "--max-tasks", type=int, default=0, help="0 = all eligible tasks"
    )
    parser.add_argument(
        "--categories", default="", help="comma-separated category filter"
    )
    parser.add_argument(
        "--task-ids", default="", help="comma-separated golden task id filter"
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="per-task wall clock budget",
    )
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="Fail non-zero when real LLM quality metrics miss release thresholds.",
    )
    parser.add_argument(
        "--release-evidence",
        action="store_true",
        help=(
            "Require the immutable full-corpus release profile and refuse to "
            "overwrite the formal report."
        ),
    )
    parser.add_argument("--min-task-success-rate", type=float, default=0.9)
    parser.add_argument("--min-intent-accuracy", type=float, default=0.9)
    parser.add_argument("--min-tool-overlap-rate", type=float, default=0.95)
    parser.add_argument("--min-risk-match-rate", type=float, default=1.0)
    parser.add_argument(
        "--min-task-count",
        type=int,
        default=100,
        help="Minimum real-LLM tasks that must run when --quality-gate is enabled.",
    )
    parser.add_argument(
        "--min-benchmark-task-count",
        type=int,
        default=MIN_REAL_LLM_BENCHMARK_CASES,
        help="Minimum versioned benchmark cases that must run for the release gate.",
    )
    parser.add_argument("--min-task-success-count", type=int, default=18)
    parser.add_argument("--min-intent-accuracy-count", type=int, default=14)
    parser.add_argument("--min-tool-overlap-count", type=int, default=14)
    parser.add_argument("--min-risk-match-count", type=int, default=9)
    parser.add_argument("--min-param-missing-count", type=int, default=14)
    parser.add_argument("--min-structured-failure-count", type=int, default=20)
    parser.add_argument("--min-unknown-tool-count", type=int, default=14)
    parser.add_argument("--min-plan-schema-valid-count", type=int, default=14)
    parser.add_argument("--max-param-missing-rate", type=float, default=0.05)
    parser.add_argument("--max-structured-failure-rate", type=float, default=0.0)
    parser.add_argument("--max-unknown-tool-rate", type=float, default=0.0)
    parser.add_argument("--min-plan-schema-valid-rate", type=float, default=1.0)
    return parser.parse_args(argv)
