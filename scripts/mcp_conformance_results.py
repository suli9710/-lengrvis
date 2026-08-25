"""Strict parsing contract for official MCP client conformance results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

RESULTS_SUMMARY_VERSION = "mcp-conformance-results-summary/v1"
EXPECTED_MCP_SPEC_VERSION = "2025-11-25"
MAX_CHECKS_BYTES = 2 * 1024 * 1024

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESULTS_SUMMARY_FIELDS = frozenset({"schema_version", "scenarios"})
_SCENARIO_SUMMARY_FIELDS = frozenset(
    {
        "scenario",
        "report_id",
        "checks_sha256",
        "check_count",
        "check_sequence",
        "info_count",
        "success_check_ids",
    }
)


class ConformanceResultsError(ValueError):
    """Raised when raw official conformance results violate the pinned contract."""


@dataclass(frozen=True)
class ScenarioContract:
    scenario: str
    directory_name: str
    report_prefix: str
    expected_checks: tuple[tuple[str, str], ...]

    @property
    def check_count(self) -> int:
        return len(self.expected_checks)

    @property
    def success_check_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                check_id
                for status_value, check_id in self.expected_checks
                if status_value == "SUCCESS"
            )
        )


SCENARIO_CONTRACTS = (
    ScenarioContract(
        scenario="initialize",
        directory_name="initialize",
        report_prefix="initialize-",
        expected_checks=(
            ("SUCCESS", "mcp-client-initialization"),
            ("INFO", "server-info"),
        ),
    ),
    ScenarioContract(
        scenario="tools_call",
        directory_name="tools-call",
        report_prefix="tools_call-",
        expected_checks=(
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-response"),
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-response"),
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-response"),
            ("INFO", "incoming-request"),
            ("SUCCESS", "tool-add-numbers"),
            ("INFO", "outgoing-response"),
        ),
    ),
    ScenarioContract(
        scenario="sse-retry",
        directory_name="sse-retry",
        report_prefix="sse-retry-",
        expected_checks=(
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-response"),
            ("INFO", "incoming-request"),
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-response"),
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-sse-event"),
            ("INFO", "outgoing-stream-close"),
            ("INFO", "incoming-request"),
            ("INFO", "outgoing-sse-event"),
            ("INFO", "outgoing-sse-event"),
            ("SUCCESS", "client-sse-graceful-reconnect"),
            ("SUCCESS", "client-sse-retry-timing"),
            ("SUCCESS", "client-sse-last-event-id"),
        ),
    ),
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConformanceResultsError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_nonstandard_number(value: str) -> None:
    raise ConformanceResultsError(f"non-standard JSON number is forbidden: {value}")


def _load_checks(content: bytes, *, scenario: str) -> Any:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConformanceResultsError(f"{scenario} checks must be UTF-8 JSON") from exc
    try:
        checks = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_number,
        )
    except json.JSONDecodeError as exc:
        raise ConformanceResultsError(
            f"{scenario} checks is not valid JSON: {exc}"
        ) from exc
    pending = [checks]
    while pending:
        value = pending.pop()
        if isinstance(value, float) and not math.isfinite(value):
            raise ConformanceResultsError(
                f"{scenario} checks contains a non-finite JSON number"
            )
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return checks


def _validate_report_id(report_id: str, contract: ScenarioContract) -> None:
    suffix = report_id.removeprefix(contract.report_prefix)
    if (
        not report_id.startswith(contract.report_prefix)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{3}Z",
            suffix,
        )
        is None
    ):
        raise ConformanceResultsError(
            f"{contract.scenario} report directory has an invalid official run id"
        )
    try:
        datetime.strptime(suffix, "%Y-%m-%dT%H-%M-%S-%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ConformanceResultsError(
            f"{contract.scenario} report directory has an invalid timestamp"
        ) from exc


def _require_detail_values(
    check: Mapping[str, Any],
    *,
    scenario: str,
    expected: Mapping[str, Any],
) -> None:
    details = check.get("details")
    if not isinstance(details, dict):
        raise ConformanceResultsError(f"{scenario} success check is missing details")
    for key, expected_value in expected.items():
        actual = details.get(key)
        if type(actual) is not type(expected_value) or actual != expected_value:
            raise ConformanceResultsError(
                f"{scenario} success check has an unexpected {key} result"
            )


def _validate_success_details(
    contract: ScenarioContract,
    success_checks: Mapping[str, Mapping[str, Any]],
) -> None:
    if contract.scenario == "initialize":
        _require_detail_values(
            success_checks["mcp-client-initialization"],
            scenario=contract.scenario,
            expected={
                "protocolVersionSent": EXPECTED_MCP_SPEC_VERSION,
                "expectedSpecVersion": EXPECTED_MCP_SPEC_VERSION,
                "versionMatch": True,
            },
        )
        return
    if contract.scenario == "tools_call":
        _require_detail_values(
            success_checks["tool-add-numbers"],
            scenario=contract.scenario,
            expected={"a": 2, "b": 3, "result": 5},
        )
        return

    _require_detail_values(
        success_checks["client-sse-graceful-reconnect"],
        scenario=contract.scenario,
        expected={"getConnectionCount": 1},
    )
    _require_detail_values(
        success_checks["client-sse-retry-timing"],
        scenario=contract.scenario,
        expected={
            "expectedRetryMs": 500,
            "withinTolerance": True,
            "tooEarly": False,
            "veryLate": False,
            "getConnectionCount": 1,
        },
    )
    _require_detail_values(
        success_checks["client-sse-last-event-id"],
        scenario=contract.scenario,
        expected={
            "hasLastEventId": True,
            "lastEventIds": ["event-1"],
            "getConnectionCount": 1,
        },
    )


def summarize_checks(
    content: bytes,
    *,
    contract: ScenarioContract,
    report_id: str,
) -> dict[str, Any]:
    _validate_report_id(report_id, contract)
    checks = _load_checks(content, scenario=contract.scenario)
    if not isinstance(checks, list):
        raise ConformanceResultsError(
            f"{contract.scenario} checks root must be a JSON array"
        )
    if len(checks) != contract.check_count:
        raise ConformanceResultsError(
            f"{contract.scenario} must contain exactly {contract.check_count} checks"
        )

    success_checks: dict[str, Mapping[str, Any]] = {}
    actual_checks: list[tuple[str, str]] = []
    info_count = 0
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ConformanceResultsError(
                f"{contract.scenario} check {index} must be a JSON object"
            )
        check_id = check.get("id")
        status_value = check.get("status")
        if not isinstance(check_id, str) or not check_id:
            raise ConformanceResultsError(
                f"{contract.scenario} check {index} must have a non-empty id"
            )
        if not isinstance(status_value, str):
            raise ConformanceResultsError(
                f"{contract.scenario} check {check_id} has an invalid status"
            )
        actual_checks.append((status_value, check_id))
        if status_value == "INFO":
            info_count += 1
            continue
        if status_value != "SUCCESS":
            raise ConformanceResultsError(
                f"{contract.scenario} check {check_id} is not successful: {status_value}"
            )
        if check_id in success_checks:
            raise ConformanceResultsError(
                f"{contract.scenario} contains duplicate successful check {check_id}"
            )
        success_checks[check_id] = check

    if tuple(actual_checks) != contract.expected_checks:
        raise ConformanceResultsError(
            f"{contract.scenario} ordered checks do not match the expected contract"
        )
    if tuple(sorted(success_checks)) != contract.success_check_ids:
        raise ConformanceResultsError(
            f"{contract.scenario} successful checks do not match the expected contract"
        )
    if info_count != contract.check_count - len(contract.success_check_ids):
        raise ConformanceResultsError(
            f"{contract.scenario} informational check count is incomplete"
        )
    _validate_success_details(contract, success_checks)
    return {
        "scenario": contract.scenario,
        "report_id": report_id,
        "checks_sha256": hashlib.sha256(content).hexdigest(),
        "check_count": contract.check_count,
        "check_sequence": [
            f"{status_value}:{check_id}"
            for status_value, check_id in contract.expected_checks
        ],
        "info_count": info_count,
        "success_check_ids": list(contract.success_check_ids),
    }


def canonical_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_results_summary(summary: Any) -> list[str]:
    if not isinstance(summary, dict):
        return ["results_summary must be a JSON object"]
    if set(summary) != _RESULTS_SUMMARY_FIELDS:
        return ["results_summary fields must exactly match its v1 schema"]
    if summary.get("schema_version") != RESULTS_SUMMARY_VERSION:
        return [f"results_summary.schema_version must be {RESULTS_SUMMARY_VERSION}"]
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, list):
        return ["results_summary.scenarios must be an array"]
    if len(scenarios) != len(SCENARIO_CONTRACTS):
        return ["results_summary.scenarios must contain exactly three scenarios"]

    errors: list[str] = []
    for index, (scenario, contract) in enumerate(zip(scenarios, SCENARIO_CONTRACTS)):
        prefix = f"results_summary.scenarios[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be a JSON object")
            continue
        if set(scenario) != _SCENARIO_SUMMARY_FIELDS:
            errors.append(f"{prefix} fields must exactly match the v1 schema")
            continue
        if scenario.get("scenario") != contract.scenario:
            errors.append(f"{prefix}.scenario must be {contract.scenario}")
        report_id = scenario.get("report_id")
        if not isinstance(report_id, str):
            errors.append(f"{prefix}.report_id must be a string")
        else:
            try:
                _validate_report_id(report_id, contract)
            except ConformanceResultsError as exc:
                errors.append(str(exc))
        checks_sha256 = scenario.get("checks_sha256")
        if (
            not isinstance(checks_sha256, str)
            or _SHA256_RE.fullmatch(checks_sha256) is None
        ):
            errors.append(f"{prefix}.checks_sha256 must be a lowercase SHA256 digest")
        if type(scenario.get("check_count")) is not int:
            errors.append(f"{prefix}.check_count must be an integer")
        elif scenario["check_count"] != contract.check_count:
            errors.append(f"{prefix}.check_count does not match the expected contract")
        expected_sequence = [
            f"{status_value}:{check_id}"
            for status_value, check_id in contract.expected_checks
        ]
        check_sequence = scenario.get("check_sequence")
        if not isinstance(check_sequence, list) or not all(
            isinstance(item, str) for item in check_sequence
        ):
            errors.append(f"{prefix}.check_sequence must be a string array")
        elif check_sequence != expected_sequence:
            errors.append(
                f"{prefix}.check_sequence does not match the expected contract"
            )
        expected_info_count = contract.check_count - len(contract.success_check_ids)
        if type(scenario.get("info_count")) is not int:
            errors.append(f"{prefix}.info_count must be an integer")
        elif scenario["info_count"] != expected_info_count:
            errors.append(f"{prefix}.info_count does not match the expected contract")
        success_ids = scenario.get("success_check_ids")
        if not isinstance(success_ids, list) or not all(
            isinstance(item, str) for item in success_ids
        ):
            errors.append(f"{prefix}.success_check_ids must be a string array")
        elif success_ids != list(contract.success_check_ids):
            errors.append(
                f"{prefix}.success_check_ids do not match the expected contract"
            )
    return errors
