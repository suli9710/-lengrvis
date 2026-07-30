#!/usr/bin/env python3
"""Validate sealed Android real-device evidence before a strict release gate."""

from __future__ import annotations

import argparse
import hmac
import os
import re
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from evidence_contracts import (
    CandidateBinding,
    EVIDENCE_SIGNATURE_ENV,
    EVIDENCE_SIGNATURE_PAYLOAD_V2,
    candidate_binding_from_environment,
    get_path,
    is_sha256_hex,
    load_json,
    print_result,
    require_artifact_type,
    require_iso_datetime,
    require_nonempty,
    require_sha256_hex,
    validate_candidate_binding,
    validate_evidence_signature,
    validate_evidence_signature_secret,
)

ARTIFACT_TYPE = "android-real-device-remote-control-evidence"
DEFAULT_EVIDENCE = "build/android-real-device-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH"
ANDROID_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
PROVENANCE_TYPE = "reviewed-build-record/v1"
PLACEHOLDER_VALUES = {"todo", "tbd", "pending", "unknown", "uncollected", "placeholder", "n/a"}
ARTIFACT_MANIFEST_VERSION = "sha256-manifest/v1"
REQUIRED_ARTIFACT_KINDS = frozenset(
    {
        "adb_install_status",
        "backend_log",
        "device_screenshot",
        "device_video",
        "mobile_log",
    }
)
ALLOWED_ARTIFACT_KINDS = REQUIRED_ARTIFACT_KINDS | frozenset(
    {"certificate_record", "command_log", "proxy_trace", "review_note"}
)
ARTIFACT_MANIFEST_ENTRY_KEYS = frozenset({"kind", "label_redacted", "sha256", "size_bytes"})
MAX_ARTIFACT_MANIFEST_ENTRIES = 64


def _validate_artifact_manifest(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    manifest = get_path(payload, "evidence_artifact_manifest")
    if not isinstance(manifest, dict):
        return ["evidence_artifact_manifest must be an object"]
    unknown_manifest_keys = sorted(set(manifest) - {"version", "entries"})
    if unknown_manifest_keys:
        errors.append(
            "evidence_artifact_manifest contains unsupported fields: "
            + ", ".join(unknown_manifest_keys)
        )
    if manifest.get("version") != ARTIFACT_MANIFEST_VERSION:
        errors.append(f"evidence_artifact_manifest.version must be {ARTIFACT_MANIFEST_VERSION}")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return [*errors, "evidence_artifact_manifest.entries must be an array"]
    if not entries:
        errors.append("evidence_artifact_manifest.entries must not be empty")
    if len(entries) > MAX_ARTIFACT_MANIFEST_ENTRIES:
        errors.append(
            "evidence_artifact_manifest.entries must contain at most "
            f"{MAX_ARTIFACT_MANIFEST_ENTRIES} artifacts"
        )

    labels: list[str] = []
    digests: list[str] = []
    kinds: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"evidence_artifact_manifest.entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{path} must be an object")
            continue
        unknown_keys = sorted(set(entry) - ARTIFACT_MANIFEST_ENTRY_KEYS)
        if unknown_keys:
            errors.append(f"{path} contains unsupported fields: {', '.join(unknown_keys)}")

        kind = str(entry.get("kind") or "").strip()
        if kind not in ALLOWED_ARTIFACT_KINDS:
            errors.append(f"{path}.kind must be an allowed reviewed artifact kind")
        else:
            kinds.add(kind)

        label = str(entry.get("label_redacted") or "").strip()
        if (
            not label
            or label.casefold() in PLACEHOLDER_VALUES
            or len(label) > 160
            or "/" in label
            or "\\" in label
        ):
            errors.append(f"{path}.label_redacted must be a non-path redacted label")
        else:
            labels.append(label)
        if not is_sha256_hex(entry.get("sha256")):
            errors.append(f"{path}.sha256 must be a 64-character SHA256 hex digest")
        else:
            digests.append(str(entry["sha256"]).strip().lower())
        size_bytes = entry.get("size_bytes")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
            errors.append(f"{path}.size_bytes must be a positive integer")

    if len(labels) != len({label.casefold() for label in labels}):
        errors.append("evidence_artifact_manifest labels must be unique")
    if len(digests) != len(set(digests)):
        errors.append("evidence_artifact_manifest SHA256 digests must be unique")
    missing_kinds = sorted(REQUIRED_ARTIFACT_KINDS - kinds)
    if missing_kinds:
        errors.append(
            "evidence_artifact_manifest is missing required artifact kinds: "
            + ", ".join(missing_kinds)
        )

    redacted_labels = payload.get("evidence_artifacts_redacted")
    if not isinstance(redacted_labels, list) or any(not isinstance(item, str) for item in redacted_labels):
        errors.append("evidence_artifacts_redacted must be an array of labels")
    elif len(redacted_labels) != len(set(redacted_labels)) or set(redacted_labels) != set(labels):
        errors.append(
            "evidence_artifacts_redacted must exactly match the unique labels in evidence_artifact_manifest"
        )
    return errors


def _validate_signature_payload_version(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if get_path(payload, "evidence.signature_payload_version") != EVIDENCE_SIGNATURE_PAYLOAD_V2:
        errors.append(
            "evidence.signature_payload_version must bind the signing key fingerprint using "
            f"{EVIDENCE_SIGNATURE_PAYLOAD_V2}"
        )
    if not is_sha256_hex(get_path(payload, "evidence.signing_key_fingerprint")):
        errors.append("evidence.signing_key_fingerprint must be a full SHA256 hex digest")
    else:
        try:
            secret = validate_evidence_signature_secret(
                str(os.getenv(EVIDENCE_SIGNATURE_ENV) or "")
            )
        except ValueError:
            # validate_evidence_signature reports the canonical secret error.
            pass
        else:
            fingerprint = str(
                get_path(payload, "evidence.signing_key_fingerprint") or ""
            ).strip().lower()
            expected_fingerprint = sha256(secret.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(fingerprint, expected_fingerprint):
                errors.append(
                    "evidence.signing_key_fingerprint does not match the configured signing key"
                )
    return errors


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
    artifact_manifest_errors = _validate_artifact_manifest(payload)
    signature_payload_errors = _validate_signature_payload_version(payload)
    errors.extend(identity_errors)
    errors.extend(provenance_errors)
    errors.extend(artifact_manifest_errors)
    errors.extend(signature_payload_errors)

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
        "artifact_manifest_valid": not artifact_manifest_errors,
        "signing_key_fingerprint_bound": not signature_payload_errors
        and signature["valid_hash"]
        and signature["valid_signature"],
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
        "artifact_manifest_valid": False,
        "signing_key_fingerprint_bound": False,
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
