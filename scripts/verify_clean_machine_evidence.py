#!/usr/bin/env python3
"""Validate reviewed clean-machine evidence for install/runtime readiness."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from evidence_contracts import (
    get_path,
    load_json,
    print_result,
    require_artifact_type,
    require_false,
    require_iso_datetime,
    require_nonempty,
    require_passed,
    require_true,
    result_payload,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "clean-machine-release-evidence-reviewed"
DEFAULT_EVIDENCE = "build/clean-machine-release-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_CLEAN_MACHINE_EVIDENCE_PATH"


def validate_payload(payload: dict[str, Any], *, require_local_model: bool = False) -> list[str]:
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    require_nonempty(payload, "candidate.artifact_label", errors)
    require_nonempty(payload, "machine.profile_label_redacted", errors)
    require_nonempty(payload, "machine.os_label_redacted", errors)
    for check in (
        "checks.install",
        "checks.launch",
        "checks.backend_health",
        "checks.first_read_only_task",
        "checks.diagnostics_export",
        "checks.uninstall_or_rollback",
        "checks.screenshot_log_redaction_review",
    ):
        require_passed(payload, check, errors)
    require_passed(payload, "review.status", errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_true(payload, "summary.clean_machine_pass", errors)
    require_false(payload, "summary.release_signoff", errors)

    local_model_claimed = get_path(payload, "claims.privacy_mode_or_local_model") is True
    if require_local_model or local_model_claimed:
        _validate_local_model(payload, errors)

    errors.extend(validate_redacted_payload(payload))
    return errors


def _validate_local_model(payload: dict[str, Any], errors: list[str]) -> None:
    for path in (
        "local_model.runtime",
        "local_model.runtime_version",
        "local_model.model",
        "local_model.model_version",
    ):
        require_nonempty(payload, path, errors)
    for check in (
        "local_model.install",
        "local_model.start",
        "local_model.pull",
        "local_model.privacy_task_smoke",
    ):
        require_passed(payload, check, errors)
    require_true(payload, "summary.local_model_pass", errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default=os.getenv(ENV_VAR, DEFAULT_EVIDENCE))
    parser.add_argument("--require-local-model", action="store_true")
    args = parser.parse_args()

    evidence_path = Path(args.evidence)
    payload, errors = load_json(evidence_path)
    if payload is not None:
        errors.extend(validate_payload(payload, require_local_model=args.require_local_model))
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
