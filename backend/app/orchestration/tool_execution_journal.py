from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.audit import record
from app.core.db_tool_calls import (
    TOOL_CALL_DATA_CORRUPT_FIELD,
    claim_pending_tool_result_cleanup,
    compare_and_swap_task_execution_recovery,
    complete_claimed_tool_result_cleanup,
    fetch_task_execution_recovery_snapshot,
    fetch_tool_result_cleanup_snapshot,
    list_committed_tool_call_ids_with_blocked_result,
    list_committed_tool_call_ids_with_result,
    list_corrupt_tool_call_bindings,
    list_tool_call_ids_with_cleanup_state_for_task,
    list_tool_call_ids_with_durable_denial,
    list_tool_call_ids_with_durable_denial_for_task,
    list_tool_calls_for_task,
    list_tool_result_rows_for_call,
    list_tool_result_rows_requiring_artifact_cleanup,
    resolve_denied_tool_call_cleanup,
)
from app.core.schemas import Plan, StepStatus, Task, TaskStatus, ToolCall, ToolResult, now_iso
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.result_budget import discard_large_result_artifact, reviewed_large_result_artifact_valid
from app.orchestration.step_phase import set_step_status
from app.orchestration.task_phase import TaskPhase
from app.orchestration.tool_execution_identity import (
    ToolExecutionJournalError,
    risk_provenance_matches,
    tool_call_risk_binding,
)
from app.orchestration.tool_execution_identity import (
    build_tool_execution_intent_key as build_tool_execution_intent_key,
)
from app.orchestration.tool_execution_identity import (
    build_tool_execution_key as build_tool_execution_key,
)
from app.orchestration.tool_execution_identity import (
    execution_key_for_intent as _execution_key_for_intent,
)
from app.orchestration.tool_execution_identity import (
    normalize_tool_execution_risk_binding as normalize_tool_execution_risk_binding,
)
from app.orchestration.tool_execution_results import (
    durable_post_tool_denial_reason,
    is_durable_post_tool_denial,
    select_tool_result_rows,
)
from app.orchestration.tool_execution_results import (
    load_persisted_observations as load_persisted_observations,
)
from app.orchestration.tool_execution_results import (
    runtime_review_allows_result_reuse as runtime_review_allows_result_reuse,
)

_MAX_TASK_EXECUTION_ROWS = 5000
_RECOVERY_METADATA_VERSION = 1
_QUARANTINE_ERROR = "Tool result lacks a valid runtime safety-review binding."
_QUARANTINE_OBSERVATION = "Unreviewed tool result was quarantined."
_TASK_RECOVERY_CAS_RETRIES = 8


def reserve_prepared_tool_call(call: ToolCall) -> tuple[ToolCall, bool]:
    requested_binding = tool_call_risk_binding(call)
    expected_key = _execution_key_for_intent(call.execution_intent_key, requested_binding)
    if call.execution_key != expected_key:
        raise ToolExecutionJournalError("Tool execution key does not match its intent and risk bindings.")
    data, created = db.reserve_tool_call(call.model_dump(mode="json"))
    stored = ToolCall.model_validate(data)
    if created:
        return stored, True
    if stored.execution_intent_key != call.execution_intent_key:
        raise ToolExecutionJournalError(
            "A legacy or invalid execution row lacks the current stable intent binding; automatic replay is blocked."
        )
    stored_binding = tool_call_risk_binding(stored)
    if stored.execution_key != _execution_key_for_intent(stored.execution_intent_key, stored_binding):
        raise ToolExecutionJournalError("Stored tool execution risk binding failed integrity validation.")
    if not risk_provenance_matches(requested_binding, stored_binding):
        raise ToolExecutionJournalError(
            "Effective risk changed for an existing execution intent; automatic execution is blocked."
        )
    return stored, False


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
    return select_tool_result_rows(list_tool_result_rows_for_call(call_id))


def recover_interrupted_tool_executions() -> list[str]:
    affected_task_ids = _retry_pending_result_artifact_cleanup()
    affected_task_ids.update(
        str(row.get("task_id") or "") for row in list_corrupt_tool_call_bindings() if row.get("task_id")
    )
    recovered: set[str] = set()
    committed_call_ids = set(list_committed_tool_call_ids_with_result())
    committed_call_ids.update(list_committed_tool_call_ids_with_blocked_result())
    for call_id in sorted(committed_call_ids):
        call = load_tool_call(call_id)
        result = load_tool_result(call_id)
        if call is None or call.status != "committed":
            continue
        if result is not None and is_durable_post_tool_denial(result):
            affected_task_ids.add(call.task_id)
            continue
        if result is not None and _result_has_recovery_blocker(result):
            call = mark_tool_call_outcome_unknown(call, expected_status="committed")
            recovered.add(call.id)
            affected_task_ids.add(call.task_id)
            _record_outcome_unknown(call)
            continue
        if result is not None and _startup_result_reuse_valid(call, result):
            continue
        if result is not None:
            call, _result = quarantine_result_for_reuse(call, result)
        else:
            call = mark_tool_call_outcome_unknown(call, expected_status="committed")
            _record_outcome_unknown(call)
        recovered.add(call.id)
        affected_task_ids.add(call.task_id)
    for call_id in db.list_tool_call_ids_by_status("executing"):
        row = db.recover_tool_call_execution(call_id, now_iso())
        if row is None:
            continue
        try:
            call = ToolCall.model_validate(row)
        except ValidationError:
            continue
        result = load_tool_result(call.id)
        if result is not None and is_durable_post_tool_denial(result):
            affected_task_ids.add(call.task_id)
            continue
        if call.status == "committed":
            if result is not None and _startup_result_reuse_valid(call, result):
                continue
            if result is not None:
                call, _result = quarantine_result_for_reuse(call, result)
            else:
                call = mark_tool_call_outcome_unknown(call, expected_status="committed")
                _record_outcome_unknown(call)
            recovered.add(call.id)
            affected_task_ids.add(call.task_id)
            continue
        if call.status != "outcome_unknown":
            continue
        recovered.add(call.id)
        affected_task_ids.add(call.task_id)
        _record_outcome_unknown(call)

    affected_task_ids.update(_normalize_durable_post_tool_denials())
    for call_id in db.list_tool_call_ids_by_status("outcome_unknown"):
        call = load_tool_call(call_id)
        if call is not None:
            affected_task_ids.add(call.task_id)
    for task_id in sorted(affected_task_ids):
        _settle_task_execution_recovery(task_id)
    return sorted(recovered)


def _startup_result_reuse_valid(call: ToolCall, result: ToolResult) -> bool:
    if not runtime_review_allows_result_reuse(result):
        return False
    if not bool((result.output or {}).get("persisted_result")):
        return True
    return reviewed_large_result_artifact_valid(
        result,
        data_dir=db.db_path().parent,
        task_id=call.task_id,
        tool_name=call.tool_name,
    )


def _result_has_recovery_blocker(result: ToolResult) -> bool:
    output = result.output if isinstance(result.output, dict) else {}
    return any(
        bool(output.get(key))
        for key in (
            "review_pending",
            "outcome_unknown",
            "artifact_cleanup_pending",
            "artifact_cleanup_required",
        )
    )


def _record_outcome_unknown(call: ToolCall) -> None:
    record(
        "tool.execution_outcome_unknown",
        "ToolExecutionJournal",
        {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
        task_id=call.task_id,
    )


def _retry_pending_result_artifact_cleanup() -> set[str]:
    affected_task_ids: set[str] = set()
    for call_id, result_id in list_tool_result_rows_requiring_artifact_cleanup():
        call = load_tool_call(call_id)
        snapshot = fetch_tool_result_cleanup_snapshot(result_id)
        try:
            result = ToolResult.model_validate(snapshot[0]) if snapshot else None
        except ValidationError:
            result = None
        output = result.output if result is not None and isinstance(result.output, dict) else {}
        if (
            call is None
            or result is None
            or snapshot is None
            or output.get("artifact_cleanup_pending") is not True
            or output.get("artifact_cleanup_required") is True
        ):
            continue
        required_result = result.model_copy(
            update={"output": _normalized_cleanup_output(result, cleanup_complete=False)},
            deep=True,
        )
        if not is_durable_post_tool_denial(result):
            required_result.changed_paths = []
            required_result.rollback_info = {}
        claimed_data = claim_pending_tool_result_cleanup(
            result.id,
            expected_data=snapshot[1],
            required_data=required_result.model_dump(mode="json"),
        )
        if claimed_data is None:
            continue
        affected_task_ids.add(call.task_id)
        cleanup_complete = discard_large_result_artifact(
            db.db_path().parent,
            call.task_id,
            result.id,
            call.tool_name,
        )
        if cleanup_complete:
            completed_result = required_result.model_copy(
                update={"output": _normalized_cleanup_output(required_result, cleanup_complete=True)},
                deep=True,
            )
            complete_claimed_tool_result_cleanup(
                result.id,
                expected_data=claimed_data,
                completed_data=completed_result.model_dump(mode="json"),
            )
        else:
            record(
                "tool.result_artifact_cleanup_failed",
                "ToolExecutionJournal",
                {"tool_call_id": call.id, "tool_name": call.tool_name},
                task_id=call.task_id,
            )
    return affected_task_ids


def _normalized_cleanup_output(result: ToolResult, *, cleanup_complete: bool) -> dict[str, Any]:
    output = result.output if isinstance(result.output, dict) else {}
    if is_durable_post_tool_denial(result):
        normalized = dict(output)
        normalized.pop("review_pending", None)
        normalized.pop("artifact_cleanup_pending", None)
        if cleanup_complete:
            normalized.pop("artifact_cleanup_required", None)
            normalized.pop("outcome_unknown", None)
            normalized.pop("automatic_replay_blocked", None)
        else:
            normalized["artifact_cleanup_required"] = True
            normalized["outcome_unknown"] = True
            normalized["automatic_replay_blocked"] = True
        return normalized
    normalized = _quarantine_output()
    if not cleanup_complete:
        normalized["artifact_cleanup_required"] = True
    return normalized


def _quarantine_output() -> dict[str, Any]:
    return {
        "outcome_unknown": True,
        "automatic_replay_blocked": True,
        "unreviewed_result_quarantined": True,
    }


def quarantine_result_for_reuse(call: ToolCall, result: ToolResult) -> tuple[ToolCall, ToolResult]:
    output = _quarantine_output()
    needs_cleanup = bool((result.output or {}).get("persisted_result"))
    if needs_cleanup:
        output["artifact_cleanup_pending"] = True
    quarantined = ToolResult(
        id=result.id,
        tool_call_id=result.tool_call_id,
        ok=False,
        output=output,
        error=_QUARANTINE_ERROR,
        changed_paths=[],
        rollback_info={},
        observation=_QUARANTINE_OBSERVATION,
    )
    db.upsert_model("tool_results", quarantined)
    if needs_cleanup:
        _retry_pending_result_artifact_cleanup()
    if call.status in {"created", "committed"}:
        call = mark_tool_call_outcome_unknown(call, expected_status=call.status)
        _record_outcome_unknown(call)
    _settle_task_execution_recovery(call.task_id)
    record(
        "tool.result_reuse_blocked",
        "ToolExecutionJournal",
        {"tool_call_id": call.id, "tool_name": call.tool_name},
        task_id=call.task_id,
    )
    return call, load_tool_result(call.id) or quarantined


def _normalize_durable_post_tool_denials() -> set[str]:
    affected_task_ids: set[str] = set()
    for call_id in list_tool_call_ids_with_durable_denial():
        call = load_tool_call(call_id)
        result = load_tool_result(call_id)
        if call is None or result is None or not is_durable_post_tool_denial(result):
            continue
        affected_task_ids.add(call.task_id)
        output = result.output if isinstance(result.output, dict) else {}
        if bool(output.get("artifact_cleanup_pending") or output.get("artifact_cleanup_required")):
            continue
        if call.status == "outcome_unknown":
            resolve_denied_tool_call_cleanup(call.id, now_iso())
    return affected_task_ids


def _settle_task_execution_recovery(task_id: str) -> None:
    for _attempt in range(_TASK_RECOVERY_CAS_RETRIES):
        snapshot = fetch_task_execution_recovery_snapshot(task_id)
        if snapshot is None:
            return
        task_data, expected_updated_at, expected_data = snapshot
        try:
            task = Task.model_validate(task_data)
        except ValidationError:
            return

        issues, denials = _task_execution_recovery_facts(task)
        previous_status = task.status
        previous_stage = task.execution_stage
        denial_settled = False
        if issues:
            changed = _apply_repair_required(task, issues)
        elif denials:
            changed, denial_settled = _apply_durable_denial(task, denials)
        else:
            return

        if not changed:
            _restore_denied_steps(denials)
            return
        task.updated_at = now_iso()
        if not compare_and_swap_task_execution_recovery(
            task.model_dump(mode="json"),
            expected_updated_at=expected_updated_at,
            expected_data=expected_data,
        ):
            continue
        if task.status != previous_status:
            _record_recovery_task_status_change(task, previous_status, previous_stage)
        _restore_denied_steps(denials)
        if denial_settled:
            _record_denial_recovered(sorted(denials, key=lambda item: item[0].id)[0][0])
        return
    raise ToolExecutionJournalError(f"Task {task_id} recovery could not acquire a current CAS snapshot.")


def _task_execution_recovery_facts(task: Task) -> tuple[list[dict[str, Any]], list[tuple[ToolCall, ToolResult]]]:
    issues = _rollback_recovery_issues(task)
    calls: dict[str, ToolCall] = {}
    for call_row in list_tool_calls_for_task(task.id, limit=None):
        if call_row.get(TOOL_CALL_DATA_CORRUPT_FIELD) is True:
            issues.append(
                {
                    "code": "tool_call_data_corrupt",
                    "tool_call_id": str(call_row.get("id") or ""),
                    "physical_status": str(call_row.get("status") or ""),
                }
            )
            continue
        try:
            call = ToolCall.model_validate(call_row)
        except ValidationError:
            continue
        calls[call.id] = call
        if call.status == "outcome_unknown":
            issues.append({"code": "outcome_unknown", "tool_call_id": call.id})

    for call_id in list_tool_call_ids_with_cleanup_state_for_task(task.id):
        if call_id in calls:
            issues.append({"code": "artifact_cleanup_required", "tool_call_id": call_id})

    denials: list[tuple[ToolCall, ToolResult]] = []
    for call_id in list_tool_call_ids_with_durable_denial_for_task(task.id):
        call = calls.get(call_id)
        result = load_tool_result(call_id)
        if call is not None and result is not None and is_durable_post_tool_denial(result):
            denials.append((call, result))

    return (
        sorted({_issue_sort_key(issue): issue for issue in issues}.values(), key=_issue_sort_key),
        sorted(denials, key=lambda item: item[0].id),
    )


def _issue_sort_key(issue: dict[str, Any]) -> tuple[str, str]:
    return (str(issue.get("tool_call_id") or ""), str(issue.get("code") or ""))


def _rollback_recovery_issues(task: Task) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    claim = task.metadata.get("rollback_claim")
    claim_state = str(claim.get("state") or "").strip().casefold() if isinstance(claim, dict) else ""
    if claim_state in {
        "claimed",
        "running",
        "interrupted",
        "failed",
    }:
        issues.append({"code": f"rollback_claim_{claim_state}"})
    rollback = task.metadata.get("rollback")
    if not isinstance(rollback, dict):
        return issues
    rollback_state = str(rollback.get("state") or "").strip().casefold()
    if rollback_state in {
        "interrupted",
        "manual_required",
        "failed",
        "partial",
        "unrecoverable",
    }:
        issues.append({"code": f"rollback_{rollback_state}"})
    try:
        for key in ("failed", "manual_required", "verification_failed"):
            if int(rollback.get(key) or 0) > 0:
                issues.append({"code": f"rollback_{key}"})
    except (TypeError, ValueError):
        issues.append({"code": "rollback_malformed"})
    return issues


def _apply_repair_required(task: Task, issues: list[dict[str, Any]]) -> bool:
    call_ids = sorted({str(issue.get("tool_call_id") or "") for issue in issues if issue.get("tool_call_id")})
    state = "outcome_unknown"
    summary = (
        "Tool execution outcome is unknown after an interrupted process. "
        "Automatic replay is blocked; inspect the target state before retrying."
    )
    if any(issue.get("code") == "tool_call_data_corrupt" for issue in issues):
        state = "journal_corruption"
        summary = "Tool execution journal data is corrupt. Automatic replay is blocked; manual repair is required."
    elif any(issue.get("code") == "artifact_cleanup_required" for issue in issues):
        state = "artifact_cleanup_required"
        summary = (
            "A withheld tool-result artifact could not be deleted. "
            "Automatic replay is blocked; manual repair is required."
        )
    elif any(str(issue.get("code") or "").startswith("rollback_") for issue in issues):
        state = "rollback_repair_required"
        summary = "Rollback recovery is incomplete. Manual repair and target-state verification are required."
    recovery = {
        "version": _RECOVERY_METADATA_VERSION,
        "state": state,
        "issues": issues,
        "tool_call_ids": call_ids,
        "requires_user_review": True,
        "automatic_replay_blocked": True,
    }
    metadata_changed = task.metadata.get("execution_recovery") != recovery
    summary_changed = task.final_summary != summary
    if not metadata_changed and not summary_changed and task.status == TaskStatus.REPAIR_REQUIRED:
        return False
    task.metadata = {**task.metadata, "execution_recovery": recovery}
    task.final_summary = summary
    _set_terminal_task_phase(task, TaskPhase.REPAIR_REQUIRED)
    return True


def _restore_denied_steps(denials: list[tuple[ToolCall, ToolResult]]) -> None:
    if not denials:
        return
    task_id = denials[0][0].task_id
    denied_by_step = {call.step_id: call for call, _result in denials}
    for row in db.fetch_many("plans", "task_id = ?", (task_id,), limit=_MAX_TASK_EXECUTION_ROWS):
        try:
            plan = Plan.model_validate(row)
        except ValidationError:
            continue
        changed_calls: list[ToolCall] = []
        for step in plan.steps:
            call = denied_by_step.get(step.id)
            if call is None or step.status == StepStatus.DENIED:
                continue
            set_step_status(step, StepStatus.DENIED, actor="ToolExecutionJournal")
            changed_calls.append(call)
        if changed_calls:
            db.upsert_model("plans", plan)
            for call in sorted(changed_calls, key=lambda item: item.id):
                _record_denial_recovered(call)
        break


def _apply_durable_denial(task: Task, denials: list[tuple[ToolCall, ToolResult]]) -> tuple[bool, bool]:
    if task.status == TaskStatus.REPAIR_REQUIRED and not _repair_was_execution_cleanup_only(task):
        recovery = task.metadata.get("execution_recovery")
        if isinstance(recovery, dict) and recovery.get("state") == "artifact_cleanup_required":
            task.metadata = {key: value for key, value in task.metadata.items() if key != "execution_recovery"}
            return True, False
        return False, False
    if task.status in {TaskStatus.CANCELLED, TaskStatus.ROLLED_BACK}:
        return False, False
    _call, result = denials[0]
    reason = durable_post_tool_denial_reason(result)
    metadata = {key: value for key, value in task.metadata.items() if key != "execution_recovery"}
    changed = task.metadata != metadata or task.final_summary != reason or task.status != TaskStatus.DENIED
    if not changed:
        return False, False
    task.metadata = metadata
    task.final_summary = reason
    _set_terminal_task_phase(task, TaskPhase.DENIED)
    return True, True


def _repair_was_execution_cleanup_only(task: Task) -> bool:
    if _rollback_recovery_issues(task) or _has_other_repair_marker(task):
        return False
    recovery = task.metadata.get("execution_recovery")
    if not isinstance(recovery, dict) or recovery.get("state") != "artifact_cleanup_required":
        return False
    issues = recovery.get("issues")
    if issues is None:
        return True
    if not isinstance(issues, list) or not issues:
        return False
    allowed_codes = {"artifact_cleanup_required", "outcome_unknown"}
    return all(isinstance(issue, dict) and str(issue.get("code") or "") in allowed_codes for issue in issues)


def _has_other_repair_marker(task: Task) -> bool:
    strong_states = {"failed", "interrupted", "manual_required", "repair_required", "unrecoverable", "partial"}
    for key, value in task.metadata.items():
        normalized_key = str(key or "").strip().casefold()
        if normalized_key in {"execution_recovery", "rollback", "rollback_claim"}:
            continue
        if "repair" in normalized_key and bool(value):
            return True
        if isinstance(value, dict) and str(value.get("state") or "").strip().casefold() in strong_states:
            return True
    return False


def _set_terminal_task_phase(task: Task, phase: TaskPhase) -> None:
    task.status = phase
    task.phase = phase
    task.execution_stage = ExecutionStage.IDLE


def _record_recovery_task_status_change(
    task: Task,
    previous_status: TaskPhase,
    previous_stage: ExecutionStage,
) -> None:
    record(
        "task.status_changed",
        "ToolExecutionJournal",
        {
            "from": previous_status,
            "to": task.status,
            "execution_stage_from": previous_stage,
            "execution_stage_to": task.execution_stage,
        },
        task_id=task.id,
    )


def _record_denial_recovered(call: ToolCall) -> None:
    record(
        "tool.execution_denial_recovered",
        "ToolExecutionJournal",
        {"tool_call_id": call.id, "tool_name": call.tool_name},
        task_id=call.task_id,
    )
