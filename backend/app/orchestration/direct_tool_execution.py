from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.safety_review_agent import SafetyReviewAgent
from app.config import AppSettings
from app.core import db
from app.core.audit import record
from app.core.schemas import Approval, ApprovalStatus, Plan, Task, ToolCall, ToolResult, new_id
from app.orchestration.agent_bus import AgentBus
from app.orchestration.resource_state import ResourceStateError
from app.orchestration.result_budget import apply_result_budget, reviewed_large_result_artifact_valid
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_execution_journal import (
    build_tool_execution_intent_key,
    build_tool_execution_key,
    is_durable_post_tool_denial,
    load_tool_result,
    mark_tool_call_committed,
    mark_tool_call_executing,
    mark_tool_call_outcome_unknown,
    normalize_tool_execution_risk_binding,
    quarantine_result_for_reuse,
    reserve_prepared_tool_call,
    runtime_review_allows_result_reuse,
)
from app.orchestration.tool_runtime_support import (
    _discard_persisted_result,
    _pending_review_result_stub,
    _persistable_tool_result,
    _sanitize_tool_rollback_evidence,
    _withheld_tool_result,
)
from app.policy.effective_risk_binding import (
    approval_risk_binding,
    effective_risk_binding_error,
    refreshed_effective_risk_error,
    risk_revalidation_context,
)
from app.policy.redaction import redact_public_text, redact_value
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools.schemas import ToolDefinition

DirectExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
AsyncDirectExecutor = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]
_RESERVED_DIRECT_OUTPUT_CONTROLS = frozenset(
    {
        "_resource_state_after",
        "_resource_state_before",
        "artifact_cleanup_pending",
        "artifact_cleanup_required",
        "artifact_sha256",
        "artifact_size_bytes",
        "automatic_replay_blocked",
        "automatic_replay_available",
        "direct_result_journaled",
        "full_result_review_completed",
        "outcome_unknown",
        "persisted_result",
        "post_tool_review_id",
        "post_tool_review_verdict",
        "review_pending",
        "withheld",
    }
)
_REDACTED_SECRET_LABEL = re.compile(
    r"(?i)\b(?:api[_-]?key|token|password|secret|authorization|cookie|bearer)(?:\s*=|\s+)"
    r"\[REDACTED(?:_[A-Z_]+)?\]"
)


class _DirectRuntimeOutput(dict[str, Any]):
    """Output controls produced by this runtime rather than by a tool adapter."""


_UNJOURNALED_DIRECT_TASK_ID = "direct_api_unjournaled"
_UNJOURNALED_RESULT_METADATA = {
    "automatic_replay_available": False,
    "direct_result_journaled": False,
}


def execute_direct_tool_journaled(
    tool: ToolDefinition,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    approval_id: str,
    executor: DirectExecutor | None = None,
) -> dict[str, Any]:
    call, existing = _prepare_direct_tool_call(tool, args, approval_id, context)
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
        output = _exception_failure(exc, outcome_unknown=_is_modifying_risk(claimed.risk_level))
    return _commit_direct_result(tool, claimed, output, context)


async def execute_direct_tool_journaled_async(
    tool: ToolDefinition,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    approval_id: str,
    executor: AsyncDirectExecutor,
) -> dict[str, Any]:
    call, existing = _prepare_direct_tool_call(tool, args, approval_id, context)
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
        output = _exception_failure(exc, outcome_unknown=_is_modifying_risk(claimed.risk_level))
    return _commit_direct_result(tool, claimed, output, context)


def _prepare_direct_tool_call(
    tool: ToolDefinition,
    args: dict[str, Any],
    approval_id: str,
    context: dict[str, Any],
) -> tuple[ToolCall, dict[str, Any] | None]:
    approval = _load_approval(approval_id)
    _require_claimed_approval(approval, tool)
    task = _load_task(approval)
    step_id = str(approval.step_id or f"direct-{approval.id}")
    review = SafetyReviewAgent(settings=context.get("settings")).review_tool_call(
        task.id,
        step_id,
        tool.name,
        args,
        tool.risk_level,
        context={
            **risk_revalidation_context(context, task_id=task.id),
            "task_id": task.id,
            "step_id": step_id,
        },
        tool_definition=tool,
    )
    risk_binding = approval_risk_binding(approval)
    risk_error = effective_risk_binding_error(
        risk_binding,
        current_declared_risk=review.declared_risk_level or tool.risk_level,
        approval_risk_level=approval.risk_level,
    )
    if risk_error:
        raise ValueError(risk_error)
    refreshed_error = refreshed_effective_risk_error(risk_binding, review)
    if refreshed_error:
        raise ValueError(refreshed_error)
    normalized_risk_binding = normalize_tool_execution_risk_binding(risk_binding)
    plan_revision = _plan_revision(task.id)
    execution_intent_key = build_tool_execution_intent_key(
        task=task,
        step_id=step_id,
        tool_name=tool.name,
        tool_version=str(tool.tool_version or "1"),
        args=args,
        plan_revision=plan_revision,
        approval_id=approval.id,
    )
    call = ToolCall(
        task_id=task.id,
        step_id=step_id,
        tool_name=tool.name,
        args=_safe_args(tool, args),
        risk_level=RiskLevel(normalized_risk_binding["effective_risk_level"]),
        declared_risk_level=RiskLevel(normalized_risk_binding["declared_risk_level"]),
        risk_review_id=normalized_risk_binding["review_id"],
        risk_binding_version=normalized_risk_binding["version"],
        execution_intent_key=execution_intent_key,
        execution_key=build_tool_execution_key(
            task=task,
            step_id=step_id,
            tool_name=tool.name,
            tool_version=str(tool.tool_version or "1"),
            args=args,
            plan_revision=plan_revision,
            approval_id=approval.id,
            risk_binding=normalized_risk_binding,
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
            if is_durable_post_tool_denial(result):
                return call, _result_response(result)
            reusable = runtime_review_allows_result_reuse(result)
            if reusable and bool((result.output or {}).get("persisted_result")):
                reusable = reviewed_large_result_artifact_valid(
                    result,
                    data_dir=_direct_settings(context).data_dir,
                    task_id=call.task_id,
                    tool_name=call.tool_name,
                )
            if not reusable:
                call, _result = quarantine_result_for_reuse(call, result)
                return call, _blocked_execution(call)
            record(
                "tool.execution_result_reused",
                "DirectToolExecution",
                {"tool_call_id": call.id, "execution_key": call.execution_key, "tool_name": call.tool_name},
                task_id=call.task_id,
            )
            return call, _result_response(result)
        call = mark_tool_call_outcome_unknown(call, expected_status="committed")
    return call, _blocked_execution(call)


def _commit_direct_result(
    tool: ToolDefinition,
    call: ToolCall,
    output: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    result = _direct_result_from_output(
        tool,
        output,
        context,
        tool_call_id=call.id,
        task_id=call.task_id,
    )
    runtime = _direct_runtime_context(call, context)
    pending_result = _pending_review_result_stub(result)
    db.upsert_model("tool_results", pending_result)
    try:
        review = SafetyReviewAgent(settings=runtime.settings).review_tool_result(
            call.task_id,
            call.step_id,
            tool.name,
            result,
            call.risk_level,
            tool_definition=tool,
        )
    except Exception:  # noqa: BLE001 - broad-exception-boundary: journal uncertainty is outcome-unknown
        mark_tool_call_outcome_unknown(call, expected_status="executing")
        raise
    if not str(review.id or "").strip():
        mark_tool_call_outcome_unknown(call, expected_status="executing")
        raise RuntimeError("Post-tool safety review did not produce a durable review id.")
    result = result.model_copy(
        update={
            "runtime_review_id": review.id,
            "runtime_review_verdict": review.verdict.value,
            "runtime_review_completed": True,
        },
        deep=True,
    )
    if review.verdict != SafetyVerdict.ALLOW:
        result = _withheld_tool_result(result, review, runtime, tool_name=tool.name)
        db.upsert_model("tool_results", result)
    else:
        result = _persistable_tool_result(result)
        try:
            result = apply_result_budget(
                result,
                tool_name=tool.name,
                max_result_size=tool.max_result_size,
                runtime=runtime,
                review_completed=True,
            )
            db.upsert_model("tool_results", result)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: result persistence fails closed
            _discard_persisted_result(result, runtime, tool_name=tool.name)
            mark_tool_call_outcome_unknown(call, expected_status="executing")
            raise
    outcome_unknown = bool((result.output or {}).get("outcome_unknown"))
    if bool((result.output or {}).get("artifact_cleanup_required")):
        outcome_unknown = True
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


def finalize_unjournaled_direct_result(
    tool: ToolDefinition,
    output: dict[str, Any],
    context: dict[str, Any],
    *,
    risk_level: RiskLevel | None = None,
    task_id: str = _UNJOURNALED_DIRECT_TASK_ID,
    step_id: str | None = None,
) -> dict[str, Any]:
    """Review and bound a direct adapter result that has no replay journal."""

    result = _direct_result_from_output(
        tool,
        output,
        context,
        tool_call_id=new_id("direct_unjournaled"),
        task_id=task_id,
    )
    review = SafetyReviewAgent(settings=_direct_settings(context)).review_tool_result(
        task_id,
        step_id,
        tool.name,
        result,
        risk_level or tool.risk_level,
        tool_definition=tool,
    )
    if not str(review.id or "").strip():
        raise RuntimeError("Post-tool safety review did not produce a durable review id.")
    result = result.model_copy(
        update={
            "runtime_review_id": review.id,
            "runtime_review_verdict": review.verdict.value,
            "runtime_review_completed": True,
        },
        deep=True,
    )
    if review.verdict != SafetyVerdict.ALLOW:
        reason = review.safe_alternative or "Tool result was withheld by runtime safety review."
        result = ToolResult(
            id=result.id,
            tool_call_id=result.tool_call_id,
            ok=False,
            output={
                "ok": False,
                "withheld": True,
                "post_tool_review_id": review.id,
                "post_tool_review_verdict": review.verdict.value,
                "error": reason,
            },
            error=reason,
            changed_paths=list(result.changed_paths),
            rollback_info=dict(result.rollback_info),
            observation="Tool result was withheld by SafetyReviewAgent.",
            runtime_review_id=review.id,
            runtime_review_verdict=review.verdict.value,
            runtime_review_completed=True,
        )
    else:
        result = _persistable_tool_result(result)
        result = _apply_unjournaled_result_budget(result, max_result_size=tool.max_result_size)
        result.output.update(
            {
                "post_tool_review_id": review.id,
                "post_tool_review_verdict": review.verdict.value,
                "full_result_review_completed": True,
            }
        )
    result.output.update(_UNJOURNALED_RESULT_METADATA)
    return _result_response(result, include_tool_call_id=False)


def _direct_result_from_output(
    tool: ToolDefinition,
    output: dict[str, Any],
    context: dict[str, Any],
    *,
    tool_call_id: str,
    task_id: str,
) -> ToolResult:
    runtime_owned = type(output) is _DirectRuntimeOutput
    if not isinstance(output, dict):
        normalized: dict[str, Any] = {"ok": False, "error": "Tool returned a non-object result."}
    elif runtime_owned:
        normalized = dict(output)
    else:
        reserved = sorted(_RESERVED_DIRECT_OUTPUT_CONTROLS.intersection(output))
        normalized = {key: value for key, value in output.items() if key not in reserved}
        if reserved:
            record(
                "tool.reserved_result_control_ignored",
                "DirectToolExecution",
                {"tool": tool.name, "keys": reserved},
                task_id=task_id,
            )
    changed_paths, rollback_info = _sanitize_direct_rollback_evidence(normalized, tool, context)
    error = str(normalized.get("error") or "")
    return ToolResult(
        tool_call_id=tool_call_id,
        ok=bool(normalized.get("ok", not error)) and not error,
        output=normalized,
        error=error,
        changed_paths=changed_paths,
        rollback_info=rollback_info,
        observation=f"Direct {tool.name} execution {'completed' if not error else 'failed'}.",
    )


def _apply_unjournaled_result_budget(result: ToolResult, *, max_result_size: int) -> ToolResult:
    """Bound non-replayable API output without creating an orphan artifact."""

    if max_result_size <= 0:
        return result
    content = json.dumps(result.output, ensure_ascii=False, default=str)
    if len(content) <= max_result_size:
        return result
    preview_size = max(0, min(2000, max_result_size))
    budgeted: dict[str, Any] = {
        "ok": result.ok,
        "result_truncated": True,
        "original_size": len(content),
        "preview": content[:preview_size],
        "has_more": len(content) > preview_size,
        "full_result_available": False,
    }
    for key in ("browser_content_warnings", "content_trust"):
        if key in result.output:
            budgeted[key] = result.output[key]
    return result.model_copy(update={"output": budgeted}, deep=True)


def _direct_settings(context: dict[str, Any]) -> AppSettings:
    settings = context.get("settings")
    if isinstance(settings, AppSettings):
        return settings
    return AppSettings(
        data_dir=str(db.db_path().parent),
        allowed_directories=list(context.get("allowed_directories") or []),
    )


def _direct_runtime_context(call: ToolCall, context: dict[str, Any]) -> TaskRuntimeContext:
    task_data = db.fetch_one("tasks", call.task_id)
    if not task_data:
        raise ValueError("Direct tool execution requires its original task record.")
    task = Task.model_validate(task_data)
    settings = _direct_settings(context)
    runtime = TaskRuntimeContext.from_task(task, settings, AgentBus())
    runtime.allowed_directories = list(context.get("allowed_directories") or settings.allowed_directories or [])
    return runtime


def _sanitize_direct_rollback_evidence(
    output: dict[str, Any],
    tool: ToolDefinition,
    context: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    # Direct adapters do not yet capture trusted before/after state. Calling
    # sanitizer v2 with empty runtime-owned state yields its standard blocker
    # for any claimed rollback evidence, so inventory cannot auto-rollback it.
    settings = context.get("settings")
    data_dir = Path(getattr(settings, "data_dir", db.db_path().parent))
    return _sanitize_tool_rollback_evidence(
        output,
        pre_resource_state=[],
        post_resource_state=[],
        tool_origin=str(getattr(tool, "origin", "") or "unknown"),
        tool_trust_tier=str(getattr(tool, "trust_tier", "") or "unknown"),
        data_dir=data_dir,
    )


def _exception_failure(exc: Exception, *, outcome_unknown: bool) -> dict[str, Any]:
    safe_error = redact_public_text(str(redact_value(str(exc)) or "")).strip()
    safe_error = _REDACTED_SECRET_LABEL.sub("[REDACTED]", safe_error)
    output: dict[str, Any] = _DirectRuntimeOutput(
        {
            "ok": False,
            "error": safe_error or type(exc).__name__,
            "error_type": type(exc).__name__,
        }
    )
    if outcome_unknown:
        output.update(
            {
                "status": "outcome_unknown",
                "outcome_unknown": True,
                "automatic_replay_blocked": True,
            }
        )
    return output


def _is_modifying_risk(risk_level: RiskLevel) -> bool:
    return risk_level in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}


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


def _result_response(result: ToolResult, *, include_tool_call_id: bool = True) -> dict[str, Any]:
    response = dict(result.output or {})
    response.setdefault("ok", result.ok)
    if result.error:
        response.setdefault("error", result.error)
    if include_tool_call_id:
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
    if approval_risk_binding(approval) is None:
        raise ValueError("Direct tool execution approval lacks effective risk binding metadata.")


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
