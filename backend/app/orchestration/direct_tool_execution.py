from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Plan, Task, ToolCall, ToolResult
from app.orchestration.resource_state import ResourceStateError
from app.orchestration.tool_execution_journal import (
    build_tool_execution_key,
    load_tool_result,
    mark_tool_call_committed,
    mark_tool_call_executing,
    mark_tool_call_outcome_unknown,
    reserve_prepared_tool_call,
)
from app.policy.redaction import redact_public_text, redact_value
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

DirectExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
AsyncDirectExecutor = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


def execute_direct_tool_journaled(
    tool: ToolDefinition,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    approval_id: str,
    executor: DirectExecutor | None = None,
) -> dict[str, Any]:
    call, existing = _prepare_direct_tool_call(tool, args, approval_id)
    if existing is not None:
        return existing
    claimed = mark_tool_call_executing(call)
    if claimed is None:
        return _blocked_execution(call)
    try:
        output = (executor or tool.execute)(args, context)
    except ResourceStateError as exc:
        output = _exception_failure(exc, outcome_unknown=False)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: a write may already have side effects.
        output = _exception_failure(exc, outcome_unknown=_is_modifying_tool(tool))
    return _commit_direct_result(tool, claimed, output)


async def execute_direct_tool_journaled_async(
    tool: ToolDefinition,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    approval_id: str,
    executor: AsyncDirectExecutor,
) -> dict[str, Any]:
    call, existing = _prepare_direct_tool_call(tool, args, approval_id)
    if existing is not None:
        return existing
    claimed = mark_tool_call_executing(call)
    if claimed is None:
        return _blocked_execution(call)
    try:
        output = await executor(args, context)
    except ResourceStateError as exc:
        output = _exception_failure(exc, outcome_unknown=False)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: a write may already have side effects.
        output = _exception_failure(exc, outcome_unknown=_is_modifying_tool(tool))
    return _commit_direct_result(tool, claimed, output)


def _prepare_direct_tool_call(
    tool: ToolDefinition,
    args: dict[str, Any],
    approval_id: str,
) -> tuple[ToolCall, dict[str, Any] | None]:
    approval = _load_approval(approval_id)
    _require_claimed_approval(approval, tool)
    task = _load_task(approval)
    step_id = str(approval.step_id or f"direct-{approval.id}")
    plan_revision = _plan_revision(task.id)
    call = ToolCall(
        task_id=task.id,
        step_id=step_id,
        tool_name=tool.name,
        args=_safe_args(tool, args),
        risk_level=tool.risk_level,
        execution_key=build_tool_execution_key(
            task=task,
            step_id=step_id,
            tool_name=tool.name,
            tool_version=str(tool.tool_version or "1"),
            args=args,
            plan_revision=plan_revision,
            approval_id=approval.id,
        ),
        plan_revision=plan_revision,
        approval_id=approval.id,
        status="prepared",
        dry_run=False,
    )
    call, created = reserve_prepared_tool_call(call)
    if created or call.status == "prepared":
        return call, None
    if call.status == "committed":
        result = load_tool_result(call.id)
        if result is not None:
            record(
                "tool.execution_result_reused",
                "DirectToolExecution",
                {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
                task_id=call.task_id,
            )
            return call, _result_response(result)
        call = mark_tool_call_outcome_unknown(call, expected_status="committed")
    return call, _blocked_execution(call)


def _commit_direct_result(tool: ToolDefinition, call: ToolCall, output: dict[str, Any]) -> dict[str, Any]:
    normalized = (
        dict(output) if isinstance(output, dict) else {"ok": False, "error": "Tool returned a non-object result."}
    )
    error = str(normalized.get("error") or "")
    result = ToolResult(
        tool_call_id=call.id,
        ok=bool(normalized.get("ok", not error)) and not error,
        output=normalized,
        error=error,
        changed_paths=list(normalized.get("changed_paths") or []),
        rollback_info=dict(normalized.get("rollback_info") or {}),
        observation=f"Direct {tool.name} execution {'completed' if not error else 'failed'}.",
    )
    db.upsert_model("tool_results", result)
    outcome_unknown = bool(normalized.get("outcome_unknown"))
    if outcome_unknown:
        mark_tool_call_outcome_unknown(call, expected_status="executing")
    else:
        mark_tool_call_committed(call)
    record(
        "tool.execution_outcome_unknown" if outcome_unknown else "tool.execution_committed",
        "DirectToolExecution",
        {
            "tool_call_id": call.id,
            "execution_key": call.execution_key,
            "tool_name": call.tool_name,
            "ok": result.ok,
            "outcome_unknown": outcome_unknown,
        },
        task_id=call.task_id,
    )
    return _result_response(result)


def _exception_failure(exc: Exception, *, outcome_unknown: bool) -> dict[str, Any]:
    safe_error = redact_public_text(str(redact_value(str(exc)) or "")).strip()
    output: dict[str, Any] = {
        "ok": False,
        "error": safe_error or type(exc).__name__,
        "error_type": type(exc).__name__,
    }
    if outcome_unknown:
        output.update(
            {
                "status": "outcome_unknown",
                "outcome_unknown": True,
                "automatic_replay_blocked": True,
            }
        )
    return output


def _is_modifying_tool(tool: ToolDefinition) -> bool:
    return tool.risk_level in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}


def _blocked_execution(call: ToolCall) -> dict[str, Any]:
    outcome_unknown = call.status == "outcome_unknown" or (
        call.status == "committed" and load_tool_result(call.id) is None
    )
    message = (
        "A prior execution may already have applied its side effect; automatic replay is blocked."
        if outcome_unknown
        else "The same tool execution is already in progress; a duplicate side effect was blocked."
    )
    record(
        "tool.execution_replay_blocked",
        "DirectToolExecution",
        {
            "tool_call_id": call.id,
            "execution_key": call.execution_key,
            "tool_name": call.tool_name,
            "status": call.status,
        },
        task_id=call.task_id,
    )
    return {
        "ok": False,
        "status": "outcome_unknown" if outcome_unknown else "duplicate_execution_blocked",
        "error": message,
        "outcome_unknown": outcome_unknown,
        "automatic_replay_blocked": True,
        "tool_call_id": call.id,
    }


def _result_response(result: ToolResult) -> dict[str, Any]:
    response = dict(result.output or {})
    response.setdefault("ok", result.ok)
    if result.error:
        response.setdefault("error", result.error)
    response.setdefault("tool_call_id", result.tool_call_id)
    return response


def _load_approval(approval_id: str) -> Approval:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise ValueError("Direct tool execution requires a stored approval.")
    return Approval.model_validate(data)


def _require_claimed_approval(approval: Approval, tool: ToolDefinition) -> None:
    if approval.status != ApprovalStatus.APPROVED or not approval.consumed_at:
        raise ValueError("Direct tool execution requires an approved, atomically consumed approval.")
    if str(approval.tool_name or "") != tool.name:
        raise ValueError("Direct tool execution approval is bound to a different tool.")


def _load_task(approval: Approval) -> Task:
    data = db.fetch_one("tasks", approval.task_id)
    if not data:
        raise ValueError("Direct tool execution requires its original task record.")
    try:
        return Task.model_validate(data)
    except ValidationError as exc:
        raise ValueError("Direct tool execution task record is invalid.") from exc


def _plan_revision(task_id: str) -> int:
    rows = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    if not rows:
        return 0
    try:
        return int(Plan.model_validate(rows[0]).version)
    except (TypeError, ValueError, ValidationError):
        return 0


def _safe_args(tool: ToolDefinition, args: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_value(args)
    safe = dict(redacted) if isinstance(redacted, dict) else {"args": redacted}
    for key in tool.sensitive_arg_keys:
        if key in safe:
            safe[key] = "***"
    return safe
