"""Verify the actual frozen backend and bind runtime evidence to its digest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from build_backend import verify_frozen_sqlite  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(verify_frozen_sqlite(args.backend.resolve(strict=True), args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
