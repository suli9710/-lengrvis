#!/usr/bin/env python3
"""Validate sealed Android real-device evidence before a strict release gate."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    CandidateBinding,
    candidate_binding_from_environment,
    get_path,
    load_json,
    print_result,
    require_artifact_type,
    require_iso_datetime,
    require_nonempty,
    require_sha256_hex,
    validate_candidate_binding,
    validate_evidence_signature,
)

ARTIFACT_TYPE = "android-real-device-remote-control-evidence"
DEFAULT_EVIDENCE = "build/android-real-device-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH"
ANDROID_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
PROVENANCE_TYPE = "reviewed-build-record/v1"
PLACEHOLDER_VALUES = {"todo", "tbd", "pending", "unknown", "uncollected", "placeholder", "n/a"}


def _validate_artifact_identity(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    require_sha256_hex(payload, "app.artifact_sha256", errors)
    require_sha256_hex(payload, "app.signer_certificate_sha256", errors)
    require_nonempty(payload, "app.package_name", errors)
    require_nonempty(payload, "app.version_name", errors)

    package_name = get_path(payload, "app.package_name")
    if isinstance(package_name, str) and package_name.strip() and not ANDROID_PACKAGE_RE.fullmatch(package_name.strip()):
        errors.append("app.package_name must be a valid Android application id")
    version_code = get_path(payload, "app.version_code")
    if isinstance(version_code, bool) or not isinstance(version_code, int) or version_code < 1:
        errors.append("app.version_code must be a positive integer")
    if str(get_path(payload, "app.build_profile") or "").strip() != "preview":
        errors.append("app.build_profile must be preview")
    return errors


def _validate_artifact_provenance(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for path in (
        "app.provenance.type",
        "app.provenance.builder_id",
        "app.provenance.build_invocation_id",
        "app.provenance.source_repository",
        "app.provenance.source_commit",
        "app.provenance.build_profile",
        "app.provenance.built_at_utc",
    ):
        require_nonempty(payload, path, errors)
    require_iso_datetime(payload, "app.provenance.built_at_utc", errors)
    require_sha256_hex(payload, "app.provenance.artifact_sha256", errors)
    require_sha256_hex(payload, "app.provenance.signer_certificate_sha256", errors)

    if get_path(payload, "app.provenance.type") != PROVENANCE_TYPE:
        errors.append(f"app.provenance.type must be {PROVENANCE_TYPE}")
    for path in ("app.provenance.builder_id", "app.provenance.build_invocation_id"):
        value = str(get_path(payload, path) or "").strip()
        if value.casefold() in PLACEHOLDER_VALUES:
            errors.append(f"{path} must be a reviewed non-placeholder label")
    built_at = str(get_path(payload, "app.provenance.built_at_utc") or "").strip()
    if built_at and not (built_at.endswith("Z") or built_at.endswith("+00:00")):
        errors.append("app.provenance.built_at_utc must use UTC")
    version_code = get_path(payload, "app.provenance.version_code")
    if isinstance(version_code, bool) or not isinstance(version_code, int) or version_code < 1:
        errors.append("app.provenance.version_code must be a positive integer")

    bindings = (
        ("app.provenance.source_repository", "candidate.repository"),
        ("app.provenance.source_commit", "candidate.commit"),
        ("app.provenance.build_profile", "app.build_profile"),
        ("app.provenance.artifact_sha256", "app.artifact_sha256"),
        ("app.provenance.package_name", "app.package_name"),
        ("app.provenance.version_name", "app.version_name"),
        ("app.provenance.version_code", "app.version_code"),
        ("app.provenance.signer_certificate_sha256", "app.signer_certificate_sha256"),
    )
    for provenance_path, identity_path in bindings:
        if get_path(payload, provenance_path) != get_path(payload, identity_path):
            errors.append(f"{provenance_path} must match {identity_path}")
    return errors


def validate_payload(
    payload: dict[str, Any],
    *,
    expected_candidate_binding: CandidateBinding | None = None,
) -> list[str]:
    return validate_payload_with_contract(
        payload,
        expected_candidate_binding=expected_candidate_binding,
    )[0]


def validate_payload_with_contract(
    payload: dict[str, Any],
    *,
    expected_candidate_binding: CandidateBinding | None = None,
) -> tuple[list[str], dict[str, bool]]:
    """Validate the cryptographic contract; PowerShell validates device details."""

    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    if payload.get("real_device_result") != "passed":
        errors.append("real_device_result must be passed")
    for path in (
        "candidate.commit",
        "candidate.build_identifier",
        "candidate.repository",
        "candidate.ci_run_id",
        "candidate.ci_run_attempt",
        "review.reviewer_label",
        "review.reviewed_at_utc",
    ):
        require_nonempty(payload, path, errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    if str(payload.get("review", {}).get("status") or "").strip() != "reviewed_passed":
        errors.append("review.status must be reviewed_passed")
    identity_errors = _validate_artifact_identity(payload)
    provenance_errors = _validate_artifact_provenance(payload)
    errors.extend(identity_errors)
    errors.extend(provenance_errors)

    signature = validate_evidence_signature(payload, errors)
    binding_error_start = len(errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)
    candidate_binding_valid = expected_candidate_binding is not None and len(errors) == binding_error_start
    return errors, {
        **signature,
        "candidate_binding_valid": candidate_binding_valid,
        "artifact_identity_valid": not identity_errors,
        "artifact_provenance_valid": not provenance_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--require-candidate-binding", action="store_true")
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    payload, errors = load_json(evidence_path)
    expected_candidate_binding: CandidateBinding | None = None
    if args.require_candidate_binding:
        expected_candidate_binding, binding_errors = candidate_binding_from_environment()
        errors.extend(binding_errors)

    contract: dict[str, bool] = {
        "valid_hash": False,
        "valid_signature": False,
        "candidate_binding_valid": False,
        "artifact_identity_valid": False,
        "artifact_provenance_valid": False,
    }
    if payload is not None:
        payload_errors, contract = validate_payload_with_contract(
            payload,
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result({"ok": not errors, "contract": contract, "errors": errors})
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
