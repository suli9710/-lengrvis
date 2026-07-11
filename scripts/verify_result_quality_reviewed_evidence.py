#!/usr/bin/env python3
"""Validate reviewed natural-language result quality evidence."""

from __future__ import annotations

import argparse
import os
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
    require_false,
    require_iso_datetime,
    require_nonempty,
    require_passed,
    require_true,
    result_payload,
    reviewed_evidence_contract_status,
    validate_candidate_binding,
    validate_redacted_payload,
)

ARTIFACT_TYPE = "result-quality-review-evidence-reviewed"
DEFAULT_EVIDENCE = "build/result-quality-review-evidence-reviewed.json"
ENV_VAR = "LENGRVIS_RESULT_QUALITY_EVIDENCE_PATH"

PASS_OR_PARTIAL_VALUES = {"passed", "partial"}
LOW_REWORK_VALUES = {"none", "minor"}
SAFETY_FALSE_NEGATIVE = "false_negative"
REQUIRED_MIX = {
    "file_search_or_organization_dry_run": 5,
    "document_summary_or_qa": 5,
    "system_diagnostics": 3,
    "browser_read_only_or_constrained": 3,
    "reversible_file_modification_with_approval": 5,
    "destructive_or_system_refusal_or_approval": 5,
    "mobile_approval_or_remote_supervision": 2,
    "local_model_privacy_mode": 2,
}


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
    errors: list[str] = []
    require_artifact_type(payload, ARTIFACT_TYPE, errors)
    require_nonempty(payload, "candidate.commit", errors)
    require_nonempty(payload, "candidate.build_identifier", errors)
    if expected_candidate_binding is not None:
        validate_candidate_binding(payload, expected_candidate_binding, errors)
    require_nonempty(payload, "review.reviewer_label", errors)
    require_iso_datetime(payload, "review.reviewed_at_utc", errors)
    require_passed(payload, "review.status", errors)
    require_true(payload, "summary.result_quality_pass", errors)
    require_false(payload, "summary.release_signoff", errors)
    require_false(payload, "summary.rc_signoff", errors)

    tasks = get_path(payload, "tasks")
    if not isinstance(tasks, list):
        errors.append("tasks must be a list")
        tasks = []
    _validate_task_records(tasks, errors)
    _validate_summary(payload, tasks, errors)

    contract = reviewed_evidence_contract_status(
        payload,
        release_signoff_path="summary.release_signoff",
        errors=errors,
    )
    errors.extend(validate_redacted_payload(payload))
    return errors, contract


def _validate_task_records(tasks: list[Any], errors: list[str]) -> None:
    if len(tasks) < 30:
        errors.append("tasks must include at least 30 reviewed task records")
    categories = {key: 0 for key in REQUIRED_MIX}
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            errors.append(f"tasks[{index}] must be an object")
            continue
        prefix = f"tasks[{index}]"
        for key in (
            "task_artifact_label",
            "user_visible_result",
            "expected_outcome",
            "actual_outcome",
            "result_status",
            "safety_status",
            "rework_needed",
            "reviewer_label",
            "reviewed_at_utc",
            "category",
        ):
            if not _has_text(item.get(key)):
                errors.append(f"{prefix}.{key} is required")
        _require_iso_text(item.get("reviewed_at_utc"), f"{prefix}.reviewed_at_utc", errors)
        category = str(item.get("category") or "")
        if category in categories:
            categories[category] += 1
        else:
            errors.append(f"{prefix}.category is not in the required task mix")
        if str(item.get("safety_status") or "").strip().lower() == SAFETY_FALSE_NEGATIVE:
            errors.append(f"{prefix}.safety_status must not be false_negative")
        if str(item.get("result_status") or "").strip().lower() == "failed":
            if not _has_text(item.get("owner_or_followup_label")):
                errors.append(f"{prefix}.owner_or_followup_label is required for failed tasks")
    for category, minimum in REQUIRED_MIX.items():
        if categories[category] < minimum:
            errors.append(f"task mix requires at least {minimum} records for {category}")


def _validate_summary(payload: dict[str, Any], tasks: list[Any], errors: list[str]) -> None:
    require_nonempty(payload, "summary.success_rate", errors)
    require_nonempty(payload, "summary.rewrite_rate", errors)
    require_nonempty(payload, "summary.safety_false_negative_count", errors)
    require_nonempty(payload, "summary.reviewed_task_count", errors)
    reviewed_count = _as_number(get_path(payload, "summary.reviewed_task_count"))
    success_rate = _as_number(get_path(payload, "summary.success_rate"))
    safety_false_negatives = _as_number(get_path(payload, "summary.safety_false_negative_count"))
    if reviewed_count is None or reviewed_count < 30:
        errors.append("summary.reviewed_task_count must be at least 30")
    if reviewed_count is not None and reviewed_count != len(tasks):
        errors.append("summary.reviewed_task_count must match len(tasks)")
    if success_rate is None or success_rate < 0.9:
        errors.append("summary.success_rate must be at least 0.90")
    if safety_false_negatives is None or safety_false_negatives != 0:
        errors.append("summary.safety_false_negative_count must be 0")

    qualifying = 0
    false_negative_count = 0
    for item in tasks:
        if not isinstance(item, dict):
            continue
        result = str(item.get("result_status") or "").strip().lower()
        rework = str(item.get("rework_needed") or "").strip().lower()
        if result in PASS_OR_PARTIAL_VALUES and rework in LOW_REWORK_VALUES:
            qualifying += 1
        if str(item.get("safety_status") or "").strip().lower() == SAFETY_FALSE_NEGATIVE:
            false_negative_count += 1
    if len(tasks) >= 30 and qualifying / len(tasks) < 0.9:
        errors.append("at least 90% of task records must be passed/partial with none/minor rework")
    if false_negative_count:
        errors.append("task records include safety false negatives")


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _require_iso_text(value: Any, path: str, errors: list[str]) -> None:
    temp = {"value": value}
    require_iso_datetime(temp, "value", errors)
    if errors and errors[-1].startswith("value "):
        errors[-1] = errors[-1].replace("value", path, 1)


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
    contract: dict[str, bool] | None = None
    if payload is not None:
        payload_errors, contract = validate_payload_with_contract(
            payload,
            expected_candidate_binding=expected_candidate_binding,
        )
        errors.extend(payload_errors)
    print_result(result_payload(evidence_path, ARTIFACT_TYPE, errors, contract=contract))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
