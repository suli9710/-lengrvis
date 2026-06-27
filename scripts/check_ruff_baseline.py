from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load_baseline(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Ruff baseline file is missing: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Ruff baseline file is invalid JSON: {path}: {exc}") from exc
    max_violations = payload.get("max_violations")
    if not isinstance(max_violations, int) or max_violations < 0:
        raise SystemExit("Ruff baseline must define a non-negative integer max_violations")
    return max_violations


def _run_ruff(target: str) -> list[dict[str, Any]]:
    command = [sys.executable, "-m", "ruff", "check", target, "--output-format=json"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode not in {0, 1}:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        sys.stderr.write(completed.stdout)
        raise SystemExit(f"Ruff JSON output was invalid: {exc}") from exc
    if not isinstance(payload, list):
        raise SystemExit("Ruff JSON output was not a list")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when full-tree Ruff debt grows beyond the checked-in budget.")
    parser.add_argument("target", help="Path to lint, for example backend")
    parser.add_argument("--baseline", required=True, type=Path, help="JSON file containing max_violations")
    args = parser.parse_args()

    baseline = _load_baseline(args.baseline)
    violations = _run_ruff(args.target)
    count = len(violations)
    print(f"Ruff full-tree debt: {count} violation(s), baseline budget {baseline}.")
    if count > baseline:
        print(
            "Ruff debt increased. Fix the new findings or intentionally update the baseline after review.",
            file=sys.stderr,
        )
        return 1
    if count < baseline:
        print("Ruff debt improved; consider lowering ci/ruff-baseline.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
