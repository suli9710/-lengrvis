#!/usr/bin/env python3
"""Seal a completed reviewed release-evidence draft with Ed25519."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    CandidateBinding,
    EVIDENCE_PRIVATE_KEY_ENV,
    candidate_binding_from_environment,
    load_json,
    paths_refer_to_same_file as _paths_refer_to_same_file,
    print_result,
    seal_evidence_payload_signature,
    write_text_atomically as _write_output_atomically,
)
from verify_clean_machine_evidence import (
    ARTIFACT_TYPE as CLEAN_MACHINE_ARTIFACT_TYPE,
)
from verify_clean_machine_evidence import validate_payload as validate_clean_machine
from verify_diagnostics_external_reviewed_evidence import (
    ARTIFACT_TYPE as DIAGNOSTICS_ARTIFACT_TYPE,
)
from verify_diagnostics_external_reviewed_evidence import (
    validate_payload as validate_diagnostics,
)
from verify_distribution_release_evidence import (
    ARTIFACT_TYPE as DISTRIBUTION_ARTIFACT_TYPE,
)
from verify_distribution_release_evidence import (
    validate_payload as validate_distribution,
)
from verify_result_quality_reviewed_evidence import (
    ARTIFACT_TYPE as RESULT_QUALITY_ARTIFACT_TYPE,
)
from verify_result_quality_reviewed_evidence import (
    validate_payload as validate_result_quality,
)

SUPPORTED_KINDS: dict[str, tuple[str, str]] = {
    "distribution": (
        DISTRIBUTION_ARTIFACT_TYPE,
        "build/distribution-release-evidence-reviewed.json",
    ),
    "clean-machine": (
        CLEAN_MACHINE_ARTIFACT_TYPE,
        "build/clean-machine-release-evidence-reviewed.json",
    ),
    "result-quality": (
        RESULT_QUALITY_ARTIFACT_TYPE,
        "build/result-quality-review-evidence-reviewed.json",
    ),
    "diagnostics": (
        DIAGNOSTICS_ARTIFACT_TYPE,
        "build/diagnostics-external-review-evidence-reviewed.json",
    ),
}


def seal_payload(
    payload: dict[str, Any], *, kind: str, private_key_text: str
) -> dict[str, Any]:
    artifact_type, _ = _profile(kind)
    if payload.get("artifact_type") != artifact_type:
        raise ValueError(f"artifact_type must be {artifact_type!r}")
    if (
        payload.get("template_mode")
        or payload.get("template_status")
        or "template" in str(payload.get("artifact_type") or "").casefold()
    ):
        raise ValueError("template evidence cannot be sealed")
    return seal_evidence_payload_signature(
        payload,
        private_key_text=private_key_text,
    )


def write_sealed_evidence(
    *,
    kind: str,
    input_path: Path,
    output_path: Path,
    repo_root: Path,
    force: bool,
) -> tuple[dict[str, Any] | None, list[str]]:
    _profile(kind)
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
            kind=kind,
            private_key_text=str(os.getenv(EVIDENCE_PRIVATE_KEY_ENV) or ""),
        )
    except ValueError as exc:
        return None, [str(exc)]

    validation_errors = _validate_sealed_payload(
        kind,
        sealed,
        repo_root=repo_root,
        binding=binding,
    )
    if validation_errors:
        return None, validation_errors

    serialized = json.dumps(sealed, ensure_ascii=False, indent=2) + "\n"
    try:
        _write_output_atomically(
            output_path,
            serialized,
            force=force,
        )
    except FileExistsError:
        return None, [
            f"output already exists: {output_path}; pass --force to overwrite"
        ]
    except OSError as exc:
        return None, [f"unable to write sealed evidence: {exc}"]
    return sealed, []


def _validate_sealed_payload(
    kind: str,
    payload: dict[str, Any],
    *,
    repo_root: Path,
    binding: CandidateBinding,
) -> list[str]:
    if kind == "distribution":
        return validate_distribution(
            payload,
            repo_root=repo_root,
            expected_candidate_binding=binding,
        )
    if kind == "clean-machine":
        return validate_clean_machine(
            payload,
            repo_root=repo_root,
            expected_candidate_binding=binding,
        )
    if kind == "result-quality":
        return validate_result_quality(
            payload,
            expected_candidate_binding=binding,
        )
    if kind == "diagnostics":
        return validate_diagnostics(
            payload,
            expected_candidate_binding=binding,
        )
    raise ValueError(f"unsupported evidence kind: {kind}")


def _profile(kind: str) -> tuple[str, str]:
    try:
        return SUPPORTED_KINDS[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported evidence kind: {kind}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=sorted(SUPPORTED_KINDS))
    parser.add_argument("--input", required=True, help="Completed reviewed draft JSON.")
    parser.add_argument("--output")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    artifact_type, default_output = _profile(args.kind)
    output_path = Path(args.output or default_output)
    sealed, errors = write_sealed_evidence(
        kind=args.kind,
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
            "artifact_type": artifact_type,
            "sealed": sealed is not None and not errors,
            "errors": errors,
        }
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
