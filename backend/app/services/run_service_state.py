from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.schemas import Plan, Run, RunEngine, RunPhase, now_iso
from app.observability import context as observability_context
from app.observability.best_effort import log_best_effort_failure
from app.observability.tracing import Span
from app.orchestration.execution_engine import default_run_store
from app.orchestration.execution_models import (
    APPROVAL_REMAINING_STEPS_SUMMARY,
    TERMINAL_RUN_PHASES,
    RunState,
)
from app.orchestration.execution_models import (
    RunPhase as EngineRunPhase,
)
from app.orchestration.task_phase import TERMINAL_TASK_PHASES, TaskPhase
from app.services.run_state_checkpoints import (
    RunStateCheckpointError,
    parse_run_state_checkpoint,
    public_run_state_payload,
    state_payload_with_runtime,
)
from app.services.task_service import get_task, set_task_status

TERMINAL_PHASES = {RunPhase(phase.value) for phase in TERMINAL_RUN_PHASES}
TASK_SYNC_EVENT_PHASES = {
    RunPhase.AWAITING_APPROVAL,
    RunPhase.COMPLETED,
    RunPhase.FAILED,
    RunPhase.DENIED,
    RunPhase.CANCELLED,
}
ENGINE_TERMINAL_PHASES = {
    EngineRunPhase.AWAITING_APPROVAL,
    EngineRunPhase.COMPLETED,
    EngineRunPhase.FAILED,
    EngineRunPhase.DENIED,
    EngineRunPhase.CANCELLED,
}
_PERSISTED_RUN_ROW_ERRORS = (ValidationError, TypeError)
_PERSISTED_RUN_STATE_ERRORS = (ValidationError, AttributeError, RunStateCheckpointError)
_PERSISTED_AGENT_MESSAGE_ERRORS = (ValidationError, TypeError)
_PERSISTED_PLAN_ROW_ERRORS = (ValidationError,)
_PERSISTED_STORE_READ_ERRORS = (
    sqlite3.Error,
    json.JSONDecodeError,
    db.SensitiveRecordIntegrityError,
    ValidationError,
)
_RUN_OBSERVABILITY_RUNTIME_KEY = "observability"

logger = logging.getLogger(__name__)


def _get_run(run_id: str) -> Run:
    # Lazy import avoids a module-level cycle: run_service imports these helpers
    # and get_run itself reconciles state via _sync_run_phase_from_task.
    from app.services.run_service import get_run

    return get_run(run_id)


def _redacted_error(error: BaseException | str) -> str:
    from app.policy.redaction import redact_value

    return str(redact_value(str(error) or type(error).__name__))


def _run_from_state(state: RunState, *, requested_engine: RunEngine) -> Run:
    return Run(
        id=state.run_id,
        message=state.goal,
        mode=state.mode,
        requested_engine=requested_engine,
        engine=RunEngine(state.engine),
        phase=RunPhase(state.phase.value),
        task_id=state.task_id or None,
        state=state.model_dump(mode="json"),
        created_at=now_iso(),
        updated_at=now_iso(),
    )


def _update_run_from_state(run: Run, state: RunState) -> Run:
    run.state = _state_payload_for_run(run, state)
    return _update_run(
        run,
        phase=RunPhase(state.phase.value),
        task_id=state.task_id or None,
        error=state.transition_reason if state.phase == EngineRunPhase.FAILED else run.error,
    )


def _state_from_run(run: Run) -> RunState:
    if run.state:
        state = _parse_persisted_run_state(run)
        run.state = _state_payload_for_run(run, state)
        return default_run_store.put(state)
    try:
        return default_run_store.get(run.id)
    except KeyError:
        logger.debug("run state %s missing from store; rebuilding from run record", run.id)
    state = RunState(
        run_id=run.id,
        engine="developer" if run.engine == RunEngine.DEVELOPER else "os",
        phase=EngineRunPhase(run.phase.value),
        goal=run.message,
        mode=run.mode,
        task_id=run.task_id or "",
    )
    return default_run_store.put(state)


def _parse_persisted_run_state(run: Run) -> RunState:
    return parse_run_state_checkpoint(run.state)


def _run_data_dir(run: Run) -> str:
    runtime = run.state.get("_runtime") if isinstance(run.state, dict) else None
    if isinstance(runtime, dict) and runtime.get("data_dir"):
        return str(runtime["data_dir"])
    return ""


def _ensure_run_trace_context(run: Run) -> dict[str, str]:
    if not isinstance(run.state, dict):
        run.state = {}
    runtime = run.state.setdefault("_runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        run.state["_runtime"] = runtime
    existing = runtime.get(_RUN_OBSERVABILITY_RUNTIME_KEY)
    if isinstance(existing, dict):
        trace_id = str(existing.get("trace_id") or "")
        run_span_id = str(existing.get("run_span_id") or "")
        parent_span_id = str(existing.get("parent_span_id") or "")
        if trace_id and run_span_id:
            return {
                "trace_id": trace_id,
                "run_span_id": run_span_id,
                "parent_span_id": parent_span_id,
            }

    trace_id = observability_context.get_trace_id() or observability_context.new_trace_id()
    parent_span_id = observability_context.get_span_id() or ""
    context = {
        "trace_id": trace_id,
        "run_span_id": observability_context.new_span_id(),
        "parent_span_id": parent_span_id,
    }
    runtime[_RUN_OBSERVABILITY_RUNTIME_KEY] = dict(context)
    return context


def _run_has_unknown_tool_outcome(task_id: str) -> bool:
    if not task_id:
        return False
    try:
        return bool(
            db.fetch_many(
                "tool_calls",
                "task_id = ? AND status = ?",
                (task_id, "outcome_unknown"),
                limit=1,
            )
        )
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "run_trace.unknown_outcome_lookup", exc, task_id=task_id)
        return False


def _finish_lifecycle_span(current: Span, *, run_id: str, task_id: str = "") -> None:
    try:
        run = _get_run(run_id)
    except Exception as exc:  # noqa: BLE001
        log_best_effort_failure(logger, "run_trace.finish_lookup", exc, run_id=run_id, task_id=task_id)
        return
    current.set_attribute("run.phase", run.phase.value)
    if run.phase == RunPhase.FAILED:
        current.set_status("error")
    if _run_has_unknown_tool_outcome(task_id or run.task_id or ""):
        current.mark_outcome_unknown()


def _run_cancelled(run_id: str) -> bool:
    try:
        return _get_run(run_id).phase == RunPhase.CANCELLED
    except KeyError:
        return False


def _run_paused(run_id: str) -> bool:
    try:
        return _get_run(run_id).phase == RunPhase.PAUSED
    except KeyError:
        return False


def _is_approval_continuation(run: Run) -> bool:
    if run.phase != RunPhase.RUNNING:
        return False
    try:
        state = _parse_persisted_run_state(run)
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        log_best_effort_failure(logger, "is_approval_continuation.parse_state", exc, run_id=run.id)
        return False
    return state.continuation_kind == "approval_remaining_steps"


def _sync_persisted_state_phase(run: Run, phase: RunPhase, reason: str = "") -> None:
    if not run.state:
        return
    try:
        state = _parse_persisted_run_state(run)
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        log_best_effort_failure(
            logger,
            "sync_persisted_state_phase.parse_state",
            exc,
            run_id=run.id,
            phase=phase.value,
        )
        return
    continuation_kind: str = ""
    if reason == APPROVAL_REMAINING_STEPS_SUMMARY:
        continuation_kind = "approval_remaining_steps"
    state = state.model_copy(
        update={
            "phase": EngineRunPhase(phase.value),
            "transition_reason": reason or state.transition_reason,
            "continuation_kind": continuation_kind,
        },
        deep=True,
    )
    run.state = _state_payload_for_run(run, state)
    default_run_store.put(state)


def _cancel_persisted_state(run: Run) -> None:
    runtime = (run.state or {}).get("_runtime") if isinstance(run.state, dict) else None
    try:
        state = _parse_persisted_run_state(run) if run.state else None
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        log_best_effort_failure(logger, "cancel_persisted_state.parse_state", exc, run_id=run.id)
        return
    if state is None:
        state = _state_from_run(run)
    else:
        state = default_run_store.put(state)
    if isinstance(runtime, dict) and runtime:
        run.state["_runtime"] = dict(runtime)
    state = state.model_copy(
        update={"phase": EngineRunPhase.CANCELLED, "transition_reason": "cancel_requested"},
        deep=True,
    )
    run.state = _state_payload_for_run(run, state)
    default_run_store.put(state)


def _run_state_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return public_run_state_payload(raw)


def _state_payload_for_run(run: Run, state: RunState) -> dict[str, Any]:
    return state_payload_with_runtime(run.state, state)


def _fail_run_task(run: Run) -> None:
    if not run.task_id:
        return
    task = get_task(run.task_id)
    if task.status not in TERMINAL_TASK_PHASES:
        set_task_status(task.id, "failed", strict=True)


def _update_run(run: Run, *, phase: RunPhase, task_id: str | None = None, error: str | None = None) -> Run:
    run.phase = phase
    if task_id is not None:
        run.task_id = task_id
    if error is not None:
        run.error = error
    run.updated_at = now_iso()
    db.upsert_model("runs", run)
    return run


def _latest_plan_for_task(task_id: str) -> Plan | None:
    try:
        rows = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "latest_plan_for_task.fetch", exc, task_id=task_id)
        return None
    if not rows:
        return None
    try:
        return Plan.model_validate(rows[0])
    except _PERSISTED_PLAN_ROW_ERRORS as exc:
        log_best_effort_failure(logger, "latest_plan_for_task.validate_row", exc, task_id=task_id)
        return None


def _phase_for_task(task: Any) -> RunPhase:
    if task.execution_stage.value == "awaiting_approval":
        return RunPhase.AWAITING_APPROVAL
    if task.status in {TaskPhase.COMPLETED, TaskPhase.ROLLED_BACK}:
        return RunPhase.COMPLETED
    if task.status in {TaskPhase.FAILED, TaskPhase.REPAIR_REQUIRED}:
        return RunPhase.FAILED
    if task.status == TaskPhase.DENIED:
        return RunPhase.DENIED
    if task.status == TaskPhase.CANCELLED:
        return RunPhase.CANCELLED
    if task.execution_stage.value == "paused":
        return RunPhase.PAUSED
    return RunPhase.RUNNING


def _phase_for_task_plan(task: Any, plan: Plan | None) -> RunPhase:
    del plan
    return _phase_for_task(task)
