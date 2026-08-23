#!/usr/bin/env python3
"""Seal completed Android real-device evidence for a specific release candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    EVIDENCE_PRIVATE_KEY_ENV,
    candidate_binding_from_environment,
    load_json,
    paths_refer_to_same_file as _paths_refer_to_same_file,
    print_result,
    seal_evidence_payload_signature,
    write_text_atomically as _write_output_atomically,
)
from verify_android_reviewed_evidence import ARTIFACT_TYPE, validate_payload


def seal_payload(payload: dict[str, Any], *, private_key_text: str) -> dict[str, Any]:
    if payload.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"artifact_type must be {ARTIFACT_TYPE!r}")
    if payload.get("template_status") or payload.get("template_mode"):
        raise ValueError("template evidence cannot be sealed")
    if payload.get("real_device_result") != "passed":
        raise ValueError(
            "real_device_result must be passed before evidence can be sealed"
        )

    return seal_evidence_payload_signature(payload, private_key_text=private_key_text)


def write_sealed_evidence(
    *,
    input_path: Path,
    output_path: Path,
    force: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    payload, errors = load_json(input_path)
    if payload is None:
        return None, errors
    if _paths_refer_to_same_file(input_path, output_path):
        return None, ["input and output paths must be different"]
    if output_path.exists() and not force:
        return None, [
            f"output already exists: {output_path}; pass --force to overwrite"
        ]
    binding, binding_errors = candidate_binding_from_environment()
    if binding is None:
        return None, binding_errors
    try:
        sealed = seal_payload(
            payload,
            private_key_text=str(os.getenv(EVIDENCE_PRIVATE_KEY_ENV) or ""),
        )
    except ValueError as exc:
        return None, [str(exc)]

    validation_errors = validate_payload(sealed, expected_candidate_binding=binding)
    if validation_errors:
        return None, validation_errors

    serialized = json.dumps(sealed, ensure_ascii=False, indent=2) + "\n"
    try:
        _write_output_atomically(output_path, serialized, force=force)
    except FileExistsError:
        return None, [
            f"output already exists: {output_path}; pass --force to overwrite"
        ]
    except OSError as exc:
        return None, [f"unable to write sealed evidence: {exc}"]
    return sealed, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        required=True,
        help="Completed reviewed Android real-device draft JSON.",
    )
    parser.add_argument(
        "--output", default="build/android-real-device-evidence-reviewed.json"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    sealed, errors = write_sealed_evidence(
        input_path=Path(args.input),
        output_path=output_path,
        force=bool(args.force),
    )
    print_result(
        {
            "ok": not errors,
            "artifact_type": ARTIFACT_TYPE,
            "sealed": sealed is not None and not errors,
            "errors": errors,
        }
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
