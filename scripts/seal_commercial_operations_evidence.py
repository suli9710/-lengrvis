#!/usr/bin/env python3
"""Seal reviewed commercial operations evidence with a reviewer Ed25519 key."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    EVIDENCE_PRIVATE_KEY_ENV,
    load_json,
    print_result,
    seal_evidence_payload_signature,
)
from verify_commercial_operations_evidence import ARTIFACT_TYPE, validate_payload


def seal_payload(payload: dict[str, Any], *, private_key_text: str) -> dict[str, Any]:
    if payload.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"artifact_type must be {ARTIFACT_TYPE!r}")
    if "template" in str(payload.get("artifact_type", "")).lower() or payload.get(
        "template_mode"
    ):
        raise ValueError("template evidence cannot be sealed")

    return seal_evidence_payload_signature(payload, private_key_text=private_key_text)


def write_sealed_evidence(
    *,
    input_path: Path,
    output_path: Path,
    repo_root: Path,
    force: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    payload, errors = load_json(input_path)
    if payload is None:
        return None, errors
    if output_path.exists() and not force:
        return None, [
            f"output already exists: {output_path}; pass --force to overwrite"
        ]
    try:
        sealed = seal_payload(
            payload,
            private_key_text=str(os.getenv(EVIDENCE_PRIVATE_KEY_ENV) or ""),
        )
    except ValueError as exc:
        return None, [str(exc)]

    validation_errors = validate_payload(sealed, repo_root=repo_root)
    if validation_errors:
        return None, validation_errors

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sealed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return sealed, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Completed reviewed commercial operations draft JSON.",
    )
    parser.add_argument(
        "--output", default="build/commercial-operations-evidence-reviewed.json"
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    sealed, errors = write_sealed_evidence(
        input_path=Path(args.input),
        output_path=output_path,
        repo_root=Path(args.repo_root).resolve(),
        force=bool(args.force),
    )
    print_result(
        {
            "ok": not errors,
            "input": args.input,
            "output": str(output_path),
            "artifact_type": ARTIFACT_TYPE,
            "sealed": sealed is not None and not errors,
            "errors": errors,
        }
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
