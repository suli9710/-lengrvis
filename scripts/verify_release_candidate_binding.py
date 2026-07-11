#!/usr/bin/env python3
"""Fail closed unless strict release context identifies the checked-out candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from evidence_contracts import CandidateBinding, candidate_binding_from_environment


def validate_checkout_commit(
    binding: CandidateBinding,
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Ensure an operator cannot bind old evidence to a different checkout."""

    root = repo_root or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [f"unable to resolve checked-out candidate commit: {exc}"]
    if result.returncode != 0:
        return ["unable to resolve checked-out candidate commit"]
    checkout_commit = result.stdout.strip().lower()
    if checkout_commit != binding.commit:
        return ["checkout_commit_mismatch"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-checkout-match",
        action="store_true",
        help="Require the explicit candidate commit to equal git rev-parse HEAD.",
    )
    args = parser.parse_args()

    binding, errors = candidate_binding_from_environment()
    if binding is not None and args.require_checkout_match:
        errors.extend(validate_checkout_commit(binding))
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
