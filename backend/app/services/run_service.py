from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sqlite3
from typing import Any

from pydantic import ValidationError

from app.agents.delegation_metadata import merge_run_task_metadata
from app.core import db
from app.core.audit import record
from app.core.local_data_lifecycle import tracks_local_data
from app.core.schemas import (  # noqa: F401 - Plan is re-exported for tests.
    Approval,
    Plan,
    Run,
    RunEngine,
    RunEvent,
    RunPhase,
    StepStatus,
    now_iso,
)
from app.llm.registry import get_effective_settings
from app.observability.best_effort import log_best_effort_failure
from app.observability.tracing import span
from app.orchestration.execution_models import (
    CURRENT_RUN_STATE_SCHEMA_VERSION as CURRENT_RUN_STATE_SCHEMA_VERSION,  # noqa: F401 - public re-export.
)
from app.orchestration.execution_models import (
    RunState,  # noqa: F401 - public re-export.
)
from app.orchestration.run_event_bus import run_event_bus
from app.orchestration.task_phase import TERMINAL_TASK_PHASES, TaskPhase
from app.policy.redaction import redact_run_payload
from app.services.run_service_background import (
    active_run_ids,
    leftover_active_tasks,
)
from app.services.run_service_background import (
    bind_active_run as _bg_bind_active_run,
)
from app.services.run_service_background import (
    cancel_active_run_task as _bg_cancel_active_run_task,
)
from app.services.run_service_background import (
    new_active_run_handle as _bg_new_active_run_handle,
)
from app.services.run_service_background import (
    run_active as _bg_run_active,
)
from app.services.run_service_background import (
    schedule_background as _bg_schedule_background,
)
from app.services.run_service_background import (
    track_active_run as _bg_track_active_run,
)
from app.services.run_service_background import (
    track_active_run_if_idle as _bg_track_active_run_if_idle,
)
from app.services.run_service_background import (
    untrack_active_run as _bg_untrack_active_run,
)
from app.services.run_service_bridge import (
    _agent_message,
    _publish_task_phase_event_once,
    _publish_translated_message,
    _seen_task_message_ids,
)
from app.services.run_service_capabilities import (
    engine_capabilities_for_run,  # noqa: F401 - re-exported for route callers and tests.
    engine_route_rule_for_run,  # noqa: F401 - re-exported for route callers and tests.
)
from app.services.run_service_engine import (
    _engine_router,
    _engine_selection,
    _release_terminal_orchestrator,  # noqa: F401 - public re-export.
    _resume_engine_loop,
    _router_for_run,
    _run_engine_loop_body,  # noqa: F401 - public re-export.
    _start_engine_loop,
    _track_run_router,
)
from app.services.run_service_events import (
    publish_plan_events as _publish_plan_events,
)
from app.services.run_service_state import (
    _PERSISTED_RUN_ROW_ERRORS,
    _PERSISTED_RUN_STATE_ERRORS,
    _PERSISTED_STORE_READ_ERRORS,
    TERMINAL_PHASES,
    _cancel_persisted_state,
    _ensure_run_trace_context,
    _is_approval_continuation,
    _latest_plan_for_task,
    _parse_persisted_run_state,  # noqa: F401 - public re-export.
    _phase_for_task,
    _phase_for_task_plan,
    _redacted_error,
    _run_data_dir,
    _run_from_state,
    _state_from_run,
    _state_payload_for_run,  # noqa: F401 - public re-export.
    _sync_persisted_state_phase,
    _update_run,
    _update_run_from_state,  # noqa: F401 - public re-export.
)
from app.services.task_service import get_task, set_task_status

_ACCEPTING_NEW_RUNS = True
logger = logging.getLogger(__name__)

_schedule_background = _bg_schedule_background
_new_active_run_handle = _bg_new_active_run_handle
_track_active_run = _bg_track_active_run
_track_active_run_if_idle = _bg_track_active_run_if_idle
_bind_active_run = _bg_bind_active_run
_untrack_active_run = _bg_untrack_active_run
_run_active = _bg_run_active
_cancel_active_run_task = _bg_cancel_active_run_task


@tracks_local_data
async def create_run(
    message: str,
    mode: str,
    requested_engine: RunEngine,
    *,
    agent_hint: str | None = None,
    task_metadata: dict[str, Any] | None = None,
) -> Run:
    db.init_db()
    if not _ACCEPTING_NEW_RUNS:
        run = Run(
            message=message,
            mode=mode,
            requested_engine=requested_engine,
            engine=RunEngine.DEVELOPER if requested_engine == RunEngine.DEVELOPER else RunEngine.OS,
            phase=RunPhase.PAUSED,
            error="full_backend_backgrounding",
        )
        db.upsert_model("runs", run)
        run_event_bus.publish(
            run.id,
            "run.paused",
            {"reason": "full_backend_backgrounding", "message": message, "mode": mode},
        )
        return run
    settings = get_effective_settings()
    run_event_bus.prune_old_events(settings)
    router = _engine_router(settings)
    delegation_metadata = merge_run_task_metadata(
        agent_hint=agent_hint,
        task_metadata=task_metadata,
        goal=message,
    )
    try:
        state = await router.start_run(
            message,
            mode,
            _engine_selection(requested_engine),
            task_metadata=delegation_metadata or None,
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: engine startup crosses provider, DB, policy, and tool setup.
        error = _redacted_error(exc)
        run = Run(
            message=message,
            mode=mode,
            requested_engine=requested_engine,
            engine=RunEngine.DEVELOPER if requested_engine == RunEngine.DEVELOPER else RunEngine.OS,
            phase=RunPhase.FAILED,
            error=error,
        )
        db.upsert_model("runs", run)
        run_event_bus.publish(run.id, "run.failed", {"error": error, "message": message, "mode": mode})
        return run

    run = _run_from_state(state, requested_engine=requested_engine)
    run.state.setdefault("_runtime", {})["data_dir"] = settings.data_dir
    trace_context = _ensure_run_trace_context(run)
    with span(
        "run.create",
        {
            "run.id": run.id,
            "run.engine": run.engine.value,
            "run.requested_engine": run.requested_engine.value,
            "run.phase": run.phase.value,
            "task.id": run.task_id or "",
        },
        trace_id=trace_context["trace_id"],
        span_id=trace_context["run_span_id"],
        parent_span_id=trace_context["parent_span_id"],
    ):
        pass
    db.upsert_model("runs", run)
    if delegation_metadata.get("butler_matter_id"):
        from app.services.butler_service import attach_run

        attach_run(
            str(delegation_metadata["butler_matter_id"]),
            str(delegation_metadata.get("butler_execution_ticket") or ""),
            run.id,
            run.task_id or "",
        )
    run_event_bus.publish(
        run.id,
        "run.started",
        {
            "message": message,
            "mode": mode,
            "engine": run.engine.value,
            "requested_engine": requested_engine.value,
            "transition_reason": state.transition_reason,
            "engine_route_rule": state.route_rule,
            **(
                {"matter_id": delegation_metadata["butler_matter_id"]}
                if delegation_metadata.get("butler_matter_id")
                else {}
            ),
        },
    )
    _publish_plan_events(run.id, state)

    _track_run_router(run.id, router)
    active_owner = _new_active_run_handle()
    _track_active_run(run.id, active_owner)
    coro = _start_engine_loop(run.id, router, state, task_id=run.task_id, active_owner=active_owner)
    try:
        task = _schedule_background(coro, data_dir=settings.data_dir)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: scheduling failures must release the active-run claim.
        coro.close()
        _untrack_active_run(run.id, active_owner)
        raise
    if task is not None and not _bind_active_run(run.id, active_owner, task):
        task.cancel()
    return run


@tracks_local_data
def get_run(run_id: str) -> Run:
    data = db.fetch_one("runs", run_id)
    if not data:
        raise KeyError(run_id)
    return _sync_run_phase_from_task(Run.model_validate(data))


@tracks_local_data
def list_runs(limit: int = 100) -> list[Run]:
    return [_sync_run_phase_from_task(Run.model_validate(item)) for item in db.fetch_many("runs", limit=limit)]


def get_timeline(run_id: str) -> dict[str, Any]:
    # Reconcile belongs on write paths (approval resume, task mutation), not on
    # every timeline read — it replays agent_messages for every run on the task.
    run = get_run(run_id)
    events = [redact_run_payload(event.model_dump(mode="json")) for event in list_run_events(run_id)]
    return {"run": redact_run_payload(run.model_dump(mode="json")), "events": events, "count": len(events)}


def get_progress(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    events = list_run_events(run_id)
    progress_events = [event for event in events if event.name == "tool.progress"]
    latest = events[-1] if events else None
    return {
        "run_id": run.id,
        "task_id": run.task_id,
        "engine": run.engine.value,
        "phase": run.phase.value,
        "latest_event": redact_run_payload(latest.model_dump(mode="json")) if latest else None,
        "progress": [redact_run_payload(event.model_dump(mode="json")) for event in progress_events],
        "count": len(progress_events),
    }


def list_run_events(run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[RunEvent]:
    get_run(run_id)
    return run_event_bus.replay(run_id, after_sequence=after_sequence, limit=limit)


async def prepare_for_background(*, timeout_seconds: float = 8.0) -> dict[str, Any]:
    global _ACCEPTING_NEW_RUNS
    _ACCEPTING_NEW_RUNS = False
    active_ids = active_run_ids()
    for run_id in active_ids:
        try:
            pause_run(run_id)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: backgrounding should keep draining other runs.
            log_best_effort_failure(logger, "prepare_for_background.pause_run", exc, run_id=run_id)
            continue
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
    while active_run_ids() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
    return {
        "ok": True,
        "acceptingNewTasks": _ACCEPTING_NEW_RUNS,
        "pausedRunIds": active_ids,
        "remainingActiveRunIds": active_run_ids(),
    }


def recover_interrupted_runs() -> list[str]:
    """Reconcile crash-orphaned RUNNING runs at startup.

    A run can only legitimately be RUNNING while this process holds its engine
    loop task; after a crash/hard kill nothing is driving it, so a DB row stuck
    in RUNNING is dead weight (perpetual spinner in the UI, cannot complete).
    Flip such rows to PAUSED with an explicit reason so the existing resume
    path can pick them up on demand.
    """
    db.init_db()
    recovered: list[str] = []
    try:
        rows = db.fetch_many("runs", "phase = ?", (RunPhase.RUNNING.value,), limit=1000)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: startup recovery should not block backend startup.
        log_best_effort_failure(logger, "recover_interrupted_runs.scan", exc)
        return recovered
    for row in rows:
        try:
            run = Run.model_validate(row)
        except _PERSISTED_RUN_ROW_ERRORS as exc:
            row_id = row.get("id") if isinstance(row, dict) else ""
            log_best_effort_failure(logger, "recover_interrupted_runs.validate_row", exc, run_id=row_id)
            continue
        if _run_active(run.id):
            continue
        _sync_persisted_state_phase(run, RunPhase.PAUSED, "interrupted_by_restart")
        _update_run(run, phase=RunPhase.PAUSED, error=run.error or "interrupted_by_restart")
        run_event_bus.publish(
            run.id,
            "run.paused",
            {"reason": "interrupted_by_restart", "task_id": run.task_id},
        )
        recovered.append(run.id)
    return recovered


async def shutdown_runs(*, timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Drain in-flight engine loops at backend shutdown (R4-M3).

    The engine loops run on the process-resident background loop outside the
    TaskPool, so lifespan must drain them explicitly or in-flight runs get
    killed mid-write and stay RUNNING in the DB forever.
    """
    global _ACCEPTING_NEW_RUNS
    _ACCEPTING_NEW_RUNS = False
    paused_ids = active_run_ids()
    for run_id in paused_ids:
        try:
            pause_run(run_id)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: shutdown should continue draining other runs.
            log_best_effort_failure(logger, "shutdown_runs.pause_run", exc, run_id=run_id)
            continue
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
    while active_run_ids() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)
    leftovers = leftover_active_tasks()
    for task in leftovers.values():
        task.cancel()
    awaitable = [
        asyncio.wrap_future(task) if isinstance(task, concurrent.futures.Future) else task
        for task in leftovers.values()
        if asyncio.isfuture(task) or isinstance(task, concurrent.futures.Future)
    ]
    if awaitable:
        await asyncio.gather(*awaitable, return_exceptions=True)
    return {"ok": True, "pausedRunIds": paused_ids, "cancelledRunIds": sorted(leftovers)}


def enter_foreground_runtime() -> dict[str, Any]:
    global _ACCEPTING_NEW_RUNS
    _ACCEPTING_NEW_RUNS = True
    return {"ok": True, "acceptingNewTasks": _ACCEPTING_NEW_RUNS}


def runtime_status() -> dict[str, Any]:
    return {
        "acceptingNewTasks": _ACCEPTING_NEW_RUNS,
        "activeRunIds": active_run_ids(),
    }


def pause_run(run_id: str, *, update_task_status: bool = True) -> Run:
    run = get_run(run_id)
    if run.phase in TERMINAL_PHASES:
        return run
    if run.task_id:
        expired = _expire_pending_approvals(run.task_id, "pause_requested")
        _deny_waiting_steps_for_expired_approvals(run.task_id, expired)
        if update_task_status:
            try:
                set_task_status(run.task_id, "paused")
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: pausing the run should still proceed.
                log_best_effort_failure(logger, "pause_run.set_task_status", exc, run_id=run.id, task_id=run.task_id)
    _sync_persisted_state_phase(run, RunPhase.PAUSED, "pause_requested")
    _update_run(run, phase=RunPhase.PAUSED)
    _cancel_active_run_task(run.id)
    run_event_bus.publish(run.id, "turn.completed", {"reason": "pause_requested", "phase": run.phase.value})
    return run


def pause_runs_for_task(task_id: str) -> list[Run]:
    runs: list[Run] = []
    try:
        rows = db.fetch_many("runs", "task_id = ?", (task_id,), limit=100)
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "pause_runs_for_task.fetch", exc, task_id=task_id)
        return runs
    for row in rows:
        try:
            run = Run.model_validate(row)
        except _PERSISTED_RUN_ROW_ERRORS as exc:
            row_id = row.get("id") if isinstance(row, dict) else ""
            log_best_effort_failure(logger, "pause_runs_for_task.validate_row", exc, run_id=row_id, task_id=task_id)
            continue
        if run.phase in TERMINAL_PHASES:
            runs.append(run)
            continue
        try:
            runs.append(pause_run(run.id, update_task_status=False))
        except KeyError:
            continue
    return runs


@tracks_local_data
def resume_run(run_id: str) -> Run:
    run = get_run(run_id)
    if run.phase in TERMINAL_PHASES or run.phase == RunPhase.AWAITING_APPROVAL:
        return run
    if run.task_id:
        task = get_task(run.task_id)
        if task.status in TERMINAL_TASK_PHASES:
            # A stale Run must never revive work belonging to a terminal task.
            record(
                "run.resume_terminal_task_ignored",
                "RunService",
                {"task_status": task.status.value},
                task_id=task.id,
                run_id=run.id,
            )
            return run
    if _run_active(run.id):
        return run
    return _schedule_resume(run)


def resume_runs_for_task(task_id: str, *, include_approval_continuations: bool = False) -> list[Run]:
    runs = [Run.model_validate(item) for item in db.fetch_many("runs", "task_id = ?", (task_id,), limit=100)]
    resumed: list[Run] = []
    for run in runs:
        if run.phase in TERMINAL_PHASES:
            resumed.append(run)
            continue
        if _run_active(run.id):
            if include_approval_continuations and _is_deferable_approval_resume(run):
                resumed.append(_defer_resume_until_active_idle(run))
                continue
            resumed.append(run)
            continue
        if run.phase in {RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED} or (
            include_approval_continuations and _is_approval_continuation(run)
        ):
            resumed.append(_schedule_resume(run))
    return resumed


def _is_deferable_approval_resume(run: Run) -> bool:
    if run.phase == RunPhase.AWAITING_APPROVAL or _is_approval_continuation(run):
        return True
    if run.phase == RunPhase.RUNNING and run.task_id:
        return _task_has_waiting_approval_continuation(run.task_id)
    return False


def _task_has_waiting_approval_continuation(task_id: str) -> bool:
    try:
        task = get_task(task_id)
    except KeyError:
        return False
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "task_waiting_approval_continuation.task_lookup", exc, task_id=task_id)
        return False
    if getattr(task.execution_stage, "value", "") == "awaiting_approval":
        return True
    plan = _latest_plan_for_task(task_id)
    if plan is None:
        return False
    return any(step.status in {StepStatus.PENDING, StepStatus.WAITING_USER_APPROVAL} for step in plan.steps)


def _defer_resume_until_active_idle(run: Run) -> Run:
    active = leftover_active_tasks().get(run.id)
    if active is None or active.done():
        return _schedule_resume(run)
    data_dir = _run_data_dir(run)

    def _resume_after_active_done(_future: asyncio.Future | concurrent.futures.Future) -> None:
        try:
            with db.using_data_dir(data_dir):
                latest = get_run(run.id)
                if latest.phase in TERMINAL_PHASES or _run_active(latest.id):
                    return
                if latest.phase == RunPhase.AWAITING_APPROVAL or _is_approval_continuation(latest):
                    run_event_bus.publish(
                        latest.id,
                        "turn.resume_deferred_ready",
                        {"reason": "approval_resume_after_active_turn", "task_id": latest.task_id},
                    )
                    _schedule_resume(latest)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: callback boundary must not leak out of the active future.
            log_best_effort_failure(
                logger,
                "resume_after_active_done",
                exc,
                run_id=run.id,
                task_id=run.task_id,
            )

    active.add_done_callback(_resume_after_active_done)
    run_event_bus.publish(
        run.id,
        "turn.resume_deferred",
        {"reason": "approval_resume_waiting_for_active_turn", "task_id": run.task_id},
    )
    return run


def _schedule_resume(run: Run) -> Run:
    if run.phase == RunPhase.RUNNING and _run_active(run.id):
        return run
    active_owner = _new_active_run_handle()
    if not _track_active_run_if_idle(run.id, active_owner):
        return run
    try:
        settings = get_effective_settings()
        router = _engine_router(settings)
        state = _state_from_run(run)
        trace_context = _ensure_run_trace_context(run)
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        error = _redacted_error(exc)
        if not isinstance(run.state, dict):
            run.state = {}
        _update_run(run, phase=RunPhase.FAILED, error=error)
        run_event_bus.publish(run.id, "run.failed", {"error": error, "task_id": run.task_id})
        _untrack_active_run(run.id, active_owner)
        return run
    except Exception:  # noqa: BLE001 - broad-exception-boundary: resume setup failures must release the active-run claim.
        _untrack_active_run(run.id, active_owner)
        raise
    with span(
        "run.resume",
        {"run.id": run.id, "run.engine": run.engine.value, "run.phase": run.phase.value, "task.id": run.task_id or ""},
        trace_id=trace_context["trace_id"],
        parent_span_id=trace_context["run_span_id"],
    ):
        pass
    _update_run(run, phase=RunPhase.RUNNING)
    run_event_bus.publish(run.id, "turn.started", {"reason": "resume_requested", "task_id": run.task_id})
    coro = _resume_engine_loop(run.id, router, state, active_owner=active_owner)
    try:
        task = _schedule_background(coro, data_dir=_run_data_dir(run))
    except Exception:  # noqa: BLE001 - broad-exception-boundary: scheduling failures must close the coroutine and release the active-run claim.
        coro.close()
        _untrack_active_run(run.id, active_owner)
        raise
    if task is not None and not _bind_active_run(run.id, active_owner, task):
        task.cancel()
    return run


def cancel_run(run_id: str, *, update_task_status: bool = True, active_grace_seconds: float = 2.0) -> Run:
    run = get_run(run_id)
    if run.phase in TERMINAL_PHASES:
        return run
    _cancel_persisted_state(run)
    if run.engine == RunEngine.DEVELOPER:
        try:
            from app.orchestration.lengrvis_code_runner import cancel_lengrvis_code_run

            _schedule_background(cancel_lengrvis_code_run(run.id), data_dir=_run_data_dir(run))
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: cancellation still records run state below.
            log_best_effort_failure(logger, "cancel_run.schedule_developer_cancellation", exc, run_id=run.id)
    elif run.engine in {RunEngine.OS, RunEngine.AUTO}:
        try:
            settings = get_effective_settings()
            router = _router_for_run(run.id, settings)
            _schedule_background(router.cancel_run(run.id), data_dir=_run_data_dir(run))
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: cancellation still records run state below.
            log_best_effort_failure(logger, "cancel_run.schedule_engine_cancellation", exc, run_id=run.id)
    if run.task_id and update_task_status:
        expired = _expire_pending_approvals(run.task_id, "cancel_requested")
        _deny_waiting_steps_for_expired_approvals(run.task_id, expired)
    _update_run(run, phase=RunPhase.CANCELLED)
    _cancel_active_run_task(run.id, grace_seconds=active_grace_seconds)
    if run.task_id and update_task_status:
        try:
            set_task_status(run.task_id, TaskPhase.CANCELLED)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: cancellation still records run state below.
            log_best_effort_failure(logger, "cancel_run.set_task_cancelled", exc, run_id=run.id, task_id=run.task_id)
    run_event_bus.publish(run.id, "run.cancelled", {"task_id": run.task_id, "reason": "cancel_requested"})
    return run


def cancel_runs_for_task(task_id: str, *, active_grace_seconds: float = 0.0) -> list[Run]:
    runs: list[Run] = []
    try:
        rows = db.fetch_many("runs", "task_id = ?", (task_id,), limit=100)
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "cancel_runs_for_task.fetch", exc, task_id=task_id)
        return runs
    for row in rows:
        try:
            run = Run.model_validate(row)
        except _PERSISTED_RUN_ROW_ERRORS as exc:
            row_id = row.get("id") if isinstance(row, dict) else ""
            log_best_effort_failure(logger, "cancel_runs_for_task.validate_row", exc, run_id=row_id, task_id=task_id)
            continue
        if run.phase in TERMINAL_PHASES:
            runs.append(run)
            continue
        try:
            runs.append(cancel_run(run.id, update_task_status=False, active_grace_seconds=active_grace_seconds))
        except KeyError:
            continue
    return runs


def _expire_pending_approvals(task_id: str, reason: str) -> list[Approval]:
    try:
        expired = db.expire_pending_approvals_for_task(task_id, now_iso(), reason)
    except (sqlite3.Error, json.JSONDecodeError, db.SensitiveRecordIntegrityError) as exc:
        log_best_effort_failure(logger, "expire_pending_approvals.persist", exc, task_id=task_id)
        return []
    if not expired:
        return []
    try:
        approvals = [Approval.model_validate(item) for item in expired]
    except ValidationError as exc:
        log_best_effort_failure(logger, "expire_pending_approvals.validate_expired", exc, task_id=task_id)
        return []
    try:
        from app.services.approval_event_service import publish_approval_decided

        for approval in approvals:
            publish_approval_decided(approval)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: expiry already persisted; event fanout is best effort.
        log_best_effort_failure(logger, "expire_pending_approvals.publish_events", exc, task_id=task_id)
    return approvals


def _deny_waiting_steps_for_expired_approvals(task_id: str, approvals: list[Approval]) -> None:
    step_ids = {approval.step_id for approval in approvals if approval.step_id}
    if not step_ids:
        return
    plan = _latest_plan_for_task(task_id)
    if plan is None:
        return
    changed = False
    for step in plan.steps:
        if step.id in step_ids and step.status == StepStatus.WAITING_USER_APPROVAL:
            step.status = StepStatus.DENIED
            changed = True
    if changed:
        db.upsert_model("plans", plan)


def reconcile_expired_approvals(approvals: list[Approval]) -> None:
    """Finish expired authorization waits without replaying their actions."""
    for task_id in {approval.task_id for approval in approvals}:
        _deny_waiting_steps_for_expired_approvals(
            task_id, [approval for approval in approvals if approval.task_id == task_id]
        )
        for row in db.fetch_many("runs", "task_id = ?", (task_id,), limit=100):
            run = Run.model_validate(row)
            if run.phase == RunPhase.AWAITING_APPROVAL:
                reason = "Approval authorization expired. Start a new task to request fresh approval."
                _sync_persisted_state_phase(run, RunPhase.DENIED, reason)
                _update_run(run, phase=RunPhase.DENIED, error=reason)
                run_event_bus.publish(run.id, "run.denied", {"reason": reason})
        try:
            task = get_task(task_id)
        except KeyError:
            continue
        if task.status not in TERMINAL_TASK_PHASES and str(task.execution_stage) == "awaiting_approval":
            set_task_status(task_id, "denied", strict=True)


def reconcile_task_runs(task_id: str) -> list[Run]:
    runs = [Run.model_validate(item) for item in db.fetch_many("runs", "task_id = ?", (task_id,), limit=100)]
    if not runs:
        return []
    task = get_task(task_id)
    updated_runs: list[Run] = []
    for run in runs:
        seen = _seen_task_message_ids(run.id)
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run.id, message, seen)
        phase = _phase_for_task(task)
        previous_phase = run.phase
        _sync_persisted_state_phase(run, phase, task.final_summary)
        _update_run(run, phase=phase)
        if phase != previous_phase:
            run_event_bus.publish(run.id, "turn.completed", {"task_id": task.id, "task_status": task.status.value})
            _publish_task_phase_event_once(run, phase, task, reason="task_reconciled")
        updated_runs.append(run)
    return updated_runs


def _sync_run_phase_from_task(run: Run) -> Run:
    if not run.task_id or run.phase in TERMINAL_PHASES:
        return run
    try:
        task = get_task(run.task_id)
    except KeyError:
        return run
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "sync_run_phase_from_task.task_lookup", exc, run_id=run.id, task_id=run.task_id)
        return run
    phase = _phase_for_task(task)
    if phase == RunPhase.CANCELLED:
        phase = _phase_for_task_plan(task, _latest_plan_for_task(task.id))
    if run.phase == RunPhase.PAUSED:
        # A paused run is not auto-resumed for display, but it must still reflect
        # a TERMINAL task outcome (completed/failed/cancelled/denied) reached via
        # a divergent path (e.g. task cancel/resume while the run row is paused).
        # Otherwise get_run/list_runs report a perpetual "paused" spinner.
        if phase in TERMINAL_PHASES and phase != run.phase:
            _sync_persisted_state_phase(run, phase, task.final_summary)
            _update_run(run, phase=phase)
            _publish_task_phase_event_once(run, phase, task, reason="task_status_sync")
        return run
    if _run_active(run.id) and run.phase == RunPhase.RUNNING and phase in {RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED}:
        return run
    if phase == RunPhase.RUNNING or phase == run.phase:
        return run
    _sync_persisted_state_phase(run, phase, task.final_summary)
    _update_run(run, phase=phase)
    _publish_task_phase_event_once(run, phase, task, reason="task_status_sync")
    return run


# Public re-exports for callers/tests (patchable via _schedule_background in tests).
track_active_run = _track_active_run
untrack_active_run = _untrack_active_run
