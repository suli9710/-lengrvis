"""Sanitized fixture postconditions and observed usage for real-model evaluations.

These checks supplement plan/safety contracts; they do not prove human result
quality or replace those contracts. No fixture, path or tool output is exported.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

OUTCOME_SCOPE = "declared_fixture_postconditions"
POSTCONDITIONS = (
    "files_exist",
    "files_absent",
    "tool_output_has_keys",
    "tool_output_contains",
)


def empty_outcome_evidence(task: dict[str, Any]) -> dict[str, Any]:
    expect = task.get("expect") or {}
    phases = expect.get("phase") or (
        ["completed"] if expect.get("task_completed") else []
    )
    attack = (task.get("benchmark") or {}).get("attack_vector", "benign")
    # Surviving input files alone is a safety invariant, not a completed result.
    result_declared = any(expect.get(key) for key in POSTCONDITIONS[1:])
    eligible = "completed" in phases and attack in {"", "benign"} and result_declared
    count = sum(len(expect.get(key) or []) for key in POSTCONDITIONS) if eligible else 0
    return {
        "scope": OUTCOME_SCOPE,
        "eligible": eligible,
        "checks_expected": count,
        "checks_passed": 0,
        "verified": False if eligible else None,
        "verification_error": "",
    }


def measure_outcome_evidence(
    task: dict[str, Any],
    record: dict[str, Any],
    workspace: Path,
    task_id: str,
) -> dict[str, Any]:
    evidence = empty_outcome_evidence(task)
    if not evidence["eligible"]:
        return evidence
    evidence["verified"] = False
    if record.get("phase") != "completed" or not task_id:
        return evidence
    try:
        outputs = _reviewed_outputs(task_id)
        expect = task.get("expect") or {}
        checks: list[bool] = []
        root = workspace.resolve()
        for key in ("files_exist", "files_absent"):
            for relative in expect.get(key) or []:
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("postcondition outside fixture workspace")
                checks.append(
                    target.exists() if key == "files_exist" else not target.exists()
                )
        for spec in expect.get("tool_output_has_keys") or []:
            checks.append(
                any(
                    all(key in output for key in spec["keys"])
                    for output in outputs.get(spec["tool"], [])
                )
            )
        for spec in expect.get("tool_output_contains") or []:
            checks.append(
                any(
                    spec["text"] in json.dumps(output, ensure_ascii=False)
                    for output in outputs.get(spec["tool"], [])
                )
            )
        evidence["checks_passed"] = sum(checks)
        evidence["verified"] = bool(checks) and all(checks)
    except Exception:  # noqa: BLE001 - missing evidence must not become success or leak raw values.
        evidence["verification_error"] = "postcondition_verification_failed"
    return evidence


def _reviewed_outputs(task_id: str) -> dict[str, list[dict[str, Any]]]:
    from app.core import db

    # Join the physical journal state, not mutable JSON copies of its status.
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT tool_calls.data AS call_data, tool_results.data AS result_data
               FROM tool_calls JOIN tool_results ON tool_results.tool_call_id = tool_calls.id
               WHERE tool_calls.task_id = ? AND tool_calls.status = 'committed'""",
            (task_id,),
        ).fetchall()
    outputs: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        call, result = json.loads(row["call_data"]), json.loads(row["result_data"])
        if (
            result.get("ok") is True
            and result.get("runtime_review_completed") is True
            and result.get("runtime_review_verdict") == "allow"
            and isinstance(result.get("output"), dict)
        ):
            outputs.setdefault(call["tool_name"], []).append(result["output"])
    return outputs


def _number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def empty_usage_evidence() -> dict[str, Any]:
    return {
        "recorded_events": 0,
        "total_tokens": None,
        "total_cost_usd": None,
        "estimated": None,
        "verification_error": "",
    }


def collect_usage_evidence() -> dict[str, Any]:
    """Read the task's isolated DB, including failed-call reservations as unknown.

    No rows does not prove zero model calls. Unknown prices, incomplete rows and
    pending reservations are not treated as free calls or zero-token responses.
    """
    from app.core import db

    evidence = empty_usage_evidence()
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT total_tokens, total_cost_usd, estimated, data FROM llm_usage_events"
            ).fetchall()
        evidence["recorded_events"] = len(rows)
        if not rows:
            return evidence
        payloads = [json.loads(row["data"]) for row in rows]
        settled = all(
            payload.get("reservation", {}).get("state", "settled") == "settled"
            for payload in payloads
        )
        evidence["estimated"] = any(bool(row["estimated"]) for row in rows)
        if settled and all(
            type(row["total_tokens"]) is int and row["total_tokens"] >= 0
            for row in rows
        ):
            evidence["total_tokens"] = sum(row["total_tokens"] for row in rows)
        if settled and all(_number(row["total_cost_usd"]) for row in rows):
            evidence["total_cost_usd"] = round(
                sum(row["total_cost_usd"] for row in rows), 8
            )
    except Exception:  # noqa: BLE001 - report unknown measurements without provider/DB details.
        evidence = empty_usage_evidence()
        evidence["verification_error"] = "usage_collection_failed"
    return evidence


def product_metrics(
    records: list[dict[str, Any]], phase_count: int, phase_denominator: int
) -> dict[str, Any]:
    eligible = [
        r for r in records if (r.get("outcome_evidence") or {}).get("eligible") is True
    ]
    verified = [
        r
        for r in eligible
        if (r.get("outcome_evidence") or {}).get("verified") is True
        and r.get("phase") == "completed"
        and r.get("evaluation_passed") is True
    ]
    durations = sorted(
        r["duration_seconds"]
        for r in records
        if (r.get("ran") is True or bool(r.get("error")))
        and _number(r.get("duration_seconds"))
    )
    usage = [r.get("usage_evidence") or {} for r in records]
    tokens = [
        u["total_tokens"]
        for u in usage
        if type(u.get("total_tokens")) is int and u["total_tokens"] >= 0
    ]
    costs = [u["total_cost_usd"] for u in usage if _number(u.get("total_cost_usd"))]
    eligible_costs = [
        (r.get("usage_evidence") or {}).get("total_cost_usd") for r in eligible
    ]
    complete_cost = bool(eligible_costs) and all(
        _number(cost) for cost in eligible_costs
    )
    return {
        "outcome_scope": OUTCOME_SCOPE,
        "phase_acceptance_count": phase_count,
        "phase_acceptance_denominator": phase_denominator,
        "phase_acceptance_rate": round(phase_count / phase_denominator, 4)
        if phase_denominator
        else None,
        "verified_outcome_count": len(verified),
        "verified_outcome_denominator": len(eligible),
        "verified_outcome_rate": round(len(verified) / len(eligible), 4)
        if eligible
        else None,
        "outcome_unmeasured_count": len(records) - len(eligible),
        "duration_sample_count": len(durations),
        "duration_p50_seconds": statistics.median(durations) if durations else None,
        "duration_p95_seconds": durations[math.ceil(0.95 * len(durations)) - 1]
        if len(durations) >= 20
        else None,
        "token_coverage_count": len(tokens),
        "total_tokens": sum(tokens)
        if records and len(tokens) == len(records)
        else None,
        "cost_coverage_count": len(costs),
        "total_cost_usd": round(sum(costs), 8)
        if records and len(costs) == len(records)
        else None,
        "estimated_usage_count": sum(u.get("estimated") is True for u in usage),
        "cost_per_verified_outcome_usd": round(sum(eligible_costs) / len(verified), 8)
        if complete_cost and verified
        else None,
    }


def validate_product_evidence(record: dict[str, Any], expected: dict[str, Any]) -> None:
    """Strict optional v2 extension; legacy reports keep their original shape."""
    if "outcome_evidence" in record:
        value = record["outcome_evidence"]
        template = empty_outcome_evidence(expected)
        if not isinstance(value, dict) or set(value) != set(template):
            raise ValueError("invalid outcome evidence fields")
        for key in ("scope", "eligible", "checks_expected"):
            if (
                type(value[key]) is not type(template[key])
                or value[key] != template[key]
            ):
                raise ValueError("outcome evidence does not match corpus")
        if (
            type(value["checks_passed"]) is not int
            or not 0 <= value["checks_passed"] <= value["checks_expected"]
        ):
            raise ValueError("invalid postcondition check count")
        if value["verification_error"] not in {"", "postcondition_verification_failed"}:
            raise ValueError("invalid postcondition error")
        if not value["eligible"]:
            if value != template:
                raise ValueError("unmeasured outcome must not claim verification")
        elif type(value["verified"]) is not bool:
            raise ValueError("eligible outcome evidence must record its result")
        elif value["verified"] and (
            record["phase"] != "completed"
            or value["verification_error"]
            or value["checks_passed"] != value["checks_expected"]
        ):
            raise ValueError("verified outcome contradicts recorded evidence")
    if "usage_evidence" in record:
        value = record["usage_evidence"]
        if not isinstance(value, dict) or set(value) != set(empty_usage_evidence()):
            raise ValueError("invalid usage evidence fields")
        if type(value["recorded_events"]) is not int or value["recorded_events"] < 0:
            raise ValueError("invalid usage event count")
        if value["total_tokens"] is not None and (
            type(value["total_tokens"]) is not int or value["total_tokens"] < 0
        ):
            raise ValueError("invalid measured tokens")
        if value["total_cost_usd"] is not None and not _number(value["total_cost_usd"]):
            raise ValueError("invalid measured cost")
        if value["estimated"] is not None and type(value["estimated"]) is not bool:
            raise ValueError("invalid usage estimation flag")
        if value["verification_error"] not in {"", "usage_collection_failed"}:
            raise ValueError("invalid usage error")
        if value["recorded_events"] == 0 or value["verification_error"]:
            if any(
                value[k] is not None
                for k in ("total_tokens", "total_cost_usd", "estimated")
            ):
                raise ValueError("missing usage cannot claim measured totals")
        elif type(value["estimated"]) is not bool:
            raise ValueError("recorded usage must disclose whether it is estimated")
