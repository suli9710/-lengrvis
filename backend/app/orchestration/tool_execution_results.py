from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.db_tool_calls import (
    list_agent_message_rows_for_task,
    list_tool_calls_for_task,
    list_tool_result_rows_for_calls,
)
from app.core.schemas import ToolCall, ToolResult
from app.orchestration.result_budget import reviewed_large_result_artifact_valid

_RESULT_QUERY_CHUNK_SIZE = 400
_RESTORABLE_CALL_STATUSES = frozenset({"committed", "created"})
_TRUSTED_AGENT_RUNTIME_PUBLISHERS = frozenset({"OrchestratorAgent"})


def is_durable_post_tool_denial(result: ToolResult) -> bool:
    output = result.output if isinstance(result.output, dict) else {}
    verdict = str(output.get("post_tool_review_verdict") or "").strip().casefold()
    return bool(
        result.runtime_review_completed is True
        and result.runtime_review_id
        and result.runtime_review_verdict == "deny"
        and output.get("withheld") is True
        and verdict == "deny"
    )


def runtime_review_allows_result_reuse(result: ToolResult) -> bool:
    """Require the root runtime ALLOW binding before a result can be resumed."""

    return bool(
        result.runtime_review_completed is True
        and str(result.runtime_review_id or "").strip()
        and str(result.runtime_review_verdict or "").strip().casefold() == "allow"
    )


def durable_post_tool_denial_reason(result: ToolResult) -> str:
    output = result.output if isinstance(result.output, dict) else {}
    return str(output.get("reason") or result.error or "").strip() or ("Tool result was withheld by SafetyReviewAgent.")


def select_tool_result_rows(rows: list[dict[str, Any]]) -> ToolResult | None:
    latest_result: ToolResult | None = None
    pending_result: ToolResult | None = None
    unknown_result: ToolResult | None = None
    denied_result: ToolResult | None = None
    for row in sorted(
        rows,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")),
        reverse=True,
    ):
        try:
            result = ToolResult.model_validate(row)
        except ValidationError:
            return None
        latest_result = latest_result or result
        output = result.output if isinstance(result.output, dict) else {}
        if output.get("review_pending") or output.get("artifact_cleanup_pending"):
            pending_result = pending_result or result
        elif output.get("outcome_unknown") or output.get("artifact_cleanup_required"):
            unknown_result = unknown_result or result
        if is_durable_post_tool_denial(result):
            denied_result = denied_result or result
    return pending_result or unknown_result or denied_result or latest_result


def load_persisted_observations(
    task_id: str,
    *,
    step_ids: Collection[str] | None = None,
) -> dict[str, ToolResult]:
    """Rebuild reviewed observations after approval, pause, or restart."""

    normalized_task_id = str(task_id or "").strip()
    requested_steps = (
        {str(step_id).strip() for step_id in step_ids if str(step_id).strip()} if step_ids is not None else None
    )
    if not normalized_task_id or requested_steps == set():
        return {}

    calls = list_tool_calls_for_task(normalized_task_id, limit=None)
    messages = list_agent_message_rows_for_task(normalized_task_id)
    agent_call_ids = _agent_runtime_call_ids(calls, messages)
    call_ids = [str(call.get("id") or "").strip() for call in calls if str(call.get("id") or "").strip()]
    results_by_call = _tool_results_by_call_id(call_ids)
    observations: dict[str, ToolResult] = {}
    journaled_steps: set[str] = set()
    for call in calls:
        step_id = str(call.get("step_id") or "").strip()
        call_id = str(call.get("id") or "").strip()
        status = str(call.get("status") or "").strip()
        if not step_id or not call_id or step_id in journaled_steps:
            continue
        journaled_steps.add(step_id)
        if status not in _RESTORABLE_CALL_STATUSES:
            continue
        result = results_by_call.get(call_id)
        if result is not None and result.ok and _result_reuse_valid(call, result):
            if call_id not in agent_call_ids:
                continue
            observations[step_id] = result

    recovery_aliases: list[tuple[str, str]] = []
    messaged_steps: set[str] = set()
    for row in messages:
        payload = _structured_message_payload(row)
        failed_step_id = str(payload.get("failed_step_id") or "").strip()
        recovery_step = payload.get("recovery_step")
        recovery_step_id = str(recovery_step.get("id") or "").strip() if isinstance(recovery_step, dict) else ""
        if failed_step_id and recovery_step_id:
            recovery_aliases.append((failed_step_id, recovery_step_id))

        step_id = str(row.get("step_id") or "").strip()
        tool_call_id = str(row.get("tool_call_id") or "").strip()
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        message_type = str(row.get("message_type") or metadata.get("message_type") or "")
        if (
            not step_id
            or step_id in journaled_steps
            or step_id in messaged_steps
            or not tool_call_id
            or message_type != "observation"
            or not payload
        ):
            continue
        try:
            result = ToolResult.model_validate(payload)
        except ValidationError:
            continue
        messaged_steps.add(step_id)
        if result.ok and result.tool_call_id == tool_call_id and _message_result_reuse_valid(result):
            observations[step_id] = result

    _apply_recovery_observation_aliases(observations, recovery_aliases)
    if requested_steps is None:
        return observations
    return {step_id: result for step_id, result in observations.items() if step_id in requested_steps}


def _tool_results_by_call_id(call_ids: list[str]) -> dict[str, ToolResult]:
    rows_by_call: dict[str, list[dict[str, Any]]] = {}
    unique_call_ids = list(dict.fromkeys(call_id for call_id in call_ids if call_id))
    for start in range(0, len(unique_call_ids), _RESULT_QUERY_CHUNK_SIZE):
        chunk = unique_call_ids[start : start + _RESULT_QUERY_CHUNK_SIZE]
        rows = list_tool_result_rows_for_calls(chunk)
        for row in rows:
            call_id = str(row.get("tool_call_id") or "").strip()
            if call_id:
                rows_by_call.setdefault(call_id, []).append(row)
    results: dict[str, ToolResult] = {}
    for call_id, rows in rows_by_call.items():
        result = select_tool_result_rows(rows)
        if result is not None:
            results[call_id] = result
    return results


def _result_reuse_valid(call: dict[str, Any], result: ToolResult) -> bool:
    try:
        journal_call = ToolCall.model_validate(call)
    except ValidationError:
        return False
    valid = runtime_review_allows_result_reuse(result)
    if valid and bool((result.output or {}).get("persisted_result")):
        valid = reviewed_large_result_artifact_valid(
            result,
            data_dir=db.db_path().parent,
            task_id=journal_call.task_id,
            tool_name=journal_call.tool_name,
        )
    if valid:
        return True
    from app.orchestration.tool_execution_journal import quarantine_result_for_reuse

    quarantine_result_for_reuse(journal_call, result)
    return False


def _message_result_reuse_valid(result: ToolResult) -> bool:
    call_data = db.fetch_tool_call(result.tool_call_id)
    if call_data is not None:
        return _result_reuse_valid(call_data, result)
    return runtime_review_allows_result_reuse(result) and not bool((result.output or {}).get("persisted_result"))


def _agent_runtime_call_ids(calls: list[dict[str, Any]], messages: list[dict[str, Any]]) -> set[str]:
    calls_by_id = {str(call.get("id") or "").strip(): call for call in calls if str(call.get("id") or "").strip()}
    call_ids: set[str] = set()
    for row in messages:
        payload_id = str(_structured_message_payload(row).get("id") or "").strip()
        call = calls_by_id.get(payload_id)
        if call is not None and _trusted_agent_runtime_proposal(row, call):
            call_ids.add(payload_id)
    return call_ids


def _trusted_agent_runtime_proposal(row: dict[str, Any], call: dict[str, Any]) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    publisher = str(row.get("from_agent") or "").strip()
    name = str(row.get("name") or "").strip()
    if publisher not in _TRUSTED_AGENT_RUNTIME_PUBLISHERS or (name and name != publisher):
        return False
    if str(metadata.get("from_agent") or publisher).strip() != publisher:
        return False
    message_type = str(row.get("message_type") or metadata.get("message_type") or "").strip()
    if message_type != "proposal" or str(row.get("role") or "assistant") != "assistant":
        return False

    payload = _structured_message_payload(row)
    exact_fields = (
        "id",
        "task_id",
        "step_id",
        "tool_name",
        "execution_intent_key",
        "execution_key",
        "plan_revision",
        "approval_id",
        "dry_run",
        "risk_level",
        "declared_risk_level",
        "risk_review_id",
        "risk_binding_version",
    )
    if any(_binding_value(payload.get(field)) != _binding_value(call.get(field)) for field in exact_fields):
        return False
    if str(payload.get("status") or "") != "prepared":
        return False
    payload_args = payload.get("args")
    call_args = call.get("args")
    if not isinstance(payload_args, dict) or not isinstance(call_args, dict) or payload_args != call_args:
        return False
    if str(row.get("task_id") or "").strip() != str(call.get("task_id") or "").strip():
        return False
    if str(row.get("step_id") or "").strip() != str(call.get("step_id") or "").strip():
        return False

    tool_calls = row.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1 or not isinstance(tool_calls[0], dict):
        return False
    item = tool_calls[0]
    function = item.get("function") if isinstance(item.get("function"), dict) else {}
    if str(item.get("id") or "").strip() != str(call.get("id") or "").strip():
        return False
    if str(function.get("name") or "").strip() != str(call.get("tool_name") or "").strip():
        return False
    return _tool_call_arguments(function.get("arguments")) == call_args


def _binding_value(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value or "")


def _tool_call_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _structured_message_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("structured_payload")
    if isinstance(payload, dict):
        return payload
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("structured_payload"), dict):
        return metadata["structured_payload"]
    return {}


def _apply_recovery_observation_aliases(
    observations: dict[str, ToolResult],
    aliases: list[tuple[str, str]],
) -> None:
    for _ in range(len(aliases)):
        changed = False
        for failed_step_id, recovery_step_id in aliases:
            if failed_step_id in observations or recovery_step_id not in observations:
                continue
            observations[failed_step_id] = observations[recovery_step_id]
            changed = True
        if not changed:
            break
