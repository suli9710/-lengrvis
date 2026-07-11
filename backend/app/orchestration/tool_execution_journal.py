from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from typing import Any

from pydantic import ValidationError

from app.automation.intent_capsule import user_goal_digest
from app.core import db
from app.core.audit import record
from app.core.schemas import Task, TaskStatus, ToolCall, ToolResult, now_iso
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TERMINAL_TASK_PHASES
from app.policy.approval_binding import canonical_args, hmac_digest


class ToolExecutionJournalError(RuntimeError):
    pass


_MAX_TASK_EXECUTION_ROWS = 5000
_RESULT_QUERY_CHUNK_SIZE = 400
_RESTORABLE_CALL_STATUSES = frozenset({"committed", "created"})


def build_tool_execution_key(
    *,
    task: Task,
    step_id: str,
    tool_name: str,
    tool_version: str,
    args: dict[str, Any],
    plan_revision: int,
    approval_id: str | None,
) -> str:
    return hmac_digest(
        {
            "task_id": task.id,
            "user_goal_digest": user_goal_digest(task.user_goal),
            "step_id": step_id,
            "plan_revision": int(plan_revision),
            "tool_name": tool_name,
            "tool_version": tool_version,
            "args": canonical_args(args),
            "approval_id": approval_id or "",
        },
        prefix="execution",
    )


def reserve_prepared_tool_call(call: ToolCall) -> tuple[ToolCall, bool]:
    data, created = db.reserve_tool_call(call.model_dump(mode="json"))
    return ToolCall.model_validate(data), created


def mark_tool_call_executing(call: ToolCall) -> ToolCall | None:
    data = db.claim_tool_call_execution(call.id, now_iso())
    return ToolCall.model_validate(data) if data else None


def mark_tool_call_committed(call: ToolCall) -> ToolCall:
    data = db.commit_tool_call_execution(call.id, now_iso())
    if data:
        return ToolCall.model_validate(data)
    current = load_tool_call(call.id)
    if current and current.status == "committed":
        return current
    raise ToolExecutionJournalError(f"Tool call {call.id} could not transition to committed.")


def mark_tool_call_outcome_unknown(call: ToolCall, *, expected_status: str) -> ToolCall:
    data = db.mark_tool_call_outcome_unknown(
        call.id,
        now_iso(),
        expected_status=expected_status,
    )
    if data:
        return ToolCall.model_validate(data)
    current = load_tool_call(call.id)
    if current and current.status == "outcome_unknown":
        return current
    raise ToolExecutionJournalError(f"Tool call {call.id} could not transition to outcome_unknown.")


def load_tool_call(call_id: str) -> ToolCall | None:
    data = db.fetch_tool_call(call_id)
    if not data:
        return None
    try:
        return ToolCall.model_validate(data)
    except ValidationError:
        return None


def load_tool_result(call_id: str) -> ToolResult | None:
    rows = db.fetch_many("tool_results", "tool_call_id = ?", (call_id,), limit=1)
    if not rows:
        return None
    try:
        return ToolResult.model_validate(rows[0])
    except ValidationError:
        return None


def load_persisted_observations(
    task_id: str,
    *,
    step_ids: Collection[str] | None = None,
) -> dict[str, ToolResult]:
    """Rebuild successful step observations after approval, pause, or restart.

    The execution journal is authoritative for current runs. Durable tool
    observation messages provide a compatibility fallback for older rows, and
    recovery revision messages map a successful recovery result back to the
    failed parent step that dependents still reference.
    """

    normalized_task_id = str(task_id or "").strip()
    requested_steps = (
        {str(step_id).strip() for step_id in step_ids if str(step_id).strip()} if step_ids is not None else None
    )
    if not normalized_task_id or requested_steps == set():
        return {}

    calls = db.list_tool_calls_for_task(normalized_task_id, limit=_MAX_TASK_EXECUTION_ROWS)
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
        if result is not None and result.ok:
            observations[step_id] = result

    recovery_aliases: list[tuple[str, str]] = []
    messaged_steps: set[str] = set()
    for row in db.fetch_many("agent_messages", "task_id = ?", (normalized_task_id,), limit=_MAX_TASK_EXECUTION_ROWS):
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
        if result.ok and result.tool_call_id == tool_call_id:
            observations[step_id] = result

    _apply_recovery_observation_aliases(observations, recovery_aliases)
    if requested_steps is None:
        return observations
    return {step_id: result for step_id, result in observations.items() if step_id in requested_steps}


def _tool_results_by_call_id(call_ids: list[str]) -> dict[str, ToolResult]:
    results: dict[str, ToolResult] = {}
    unique_call_ids = list(dict.fromkeys(call_id for call_id in call_ids if call_id))
    for start in range(0, len(unique_call_ids), _RESULT_QUERY_CHUNK_SIZE):
        chunk = unique_call_ids[start : start + _RESULT_QUERY_CHUNK_SIZE]
        rows = db.fetch_many_in("tool_results", "tool_call_id", chunk, limit=max(1, len(chunk) * 4))
        for row in rows:
            call_id = str(row.get("tool_call_id") or "").strip()
            if not call_id or call_id in results:
                continue
            try:
                results[call_id] = ToolResult.model_validate(row)
            except ValidationError:
                continue
    return results


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


def recover_interrupted_tool_executions() -> list[str]:
    recovered: list[str] = []
    unknown_by_task: dict[str, list[str]] = defaultdict(list)
    for call_id in db.list_tool_call_ids_by_status("executing"):
        row = db.recover_tool_call_execution(call_id, now_iso())
        if row is None:
            continue
        try:
            call = ToolCall.model_validate(row)
        except ValidationError:
            continue
        if call.status == "committed":
            continue
        if call.status != "outcome_unknown":
            continue
        recovered.append(call.id)
        unknown_by_task[call.task_id].append(call.id)
        record(
            "tool.execution_outcome_unknown",
            "ToolExecutionJournal",
            {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
            task_id=call.task_id,
        )

    for task_id, call_ids in unknown_by_task.items():
        _mark_task_for_manual_review(task_id, call_ids)
    return recovered


def _mark_task_for_manual_review(task_id: str, call_ids: list[str]) -> None:
    row = db.fetch_one("tasks", task_id)
    if not row:
        return
    try:
        task = Task.model_validate(row)
    except ValidationError:
        return
    task.metadata = {
        **task.metadata,
        "execution_recovery": {
            "state": "outcome_unknown",
            "tool_call_ids": call_ids,
            "requires_user_review": True,
            "automatic_replay_blocked": True,
        },
    }
    task.final_summary = (
        "Tool execution outcome is unknown after an interrupted process. "
        "Automatic replay is blocked; inspect the target state before retrying."
    )
    db.upsert_model("tasks", task)
    if task.status not in TERMINAL_TASK_PHASES:
        safe_transition(task, TaskStatus.FAILED, actor="ToolExecutionJournal", strict=False)
