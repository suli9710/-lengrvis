from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sqlite3
import threading
from typing import Any

from pydantic import ValidationError

from app.agents.delegation_metadata import merge_run_task_metadata
from app.config import AppSettings
from app.core import db
from app.core.schemas import Approval, Plan, Run, RunEngine, RunEvent, RunPhase, StepStatus, now_iso
from app.llm.registry import get_effective_settings
from app.observability.best_effort import log_best_effort_failure
from app.orchestration.agent_bus import AgentBus
from app.orchestration.engine_router import EngineRouter
from app.orchestration.execution_engine import default_run_store
from app.orchestration.execution_models import (
    APPROVAL_REMAINING_STEPS_SUMMARY,
    TERMINAL_RUN_PHASES,
    EngineTurnResult,
    RunState,
)
from app.orchestration.execution_models import (
    RunPhase as EngineRunPhase,
)
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.run_event_bus import run_event_bus, task_message_to_run_event
from app.orchestration.task_phase import TaskPhase
from app.policy.redaction import redact_run_payload, redact_value
from app.policy.risk import RiskLevel
from app.services.run_service_background import (
    active_run_ids,
    leftover_active_tasks,
)
from app.services.run_service_background import (
    cancel_active_run_task as _bg_cancel_active_run_task,
)
from app.services.run_service_background import (
    register_resident_task as _bg_register_resident_task,
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
    unregister_resident_task as _bg_unregister_resident_task,
)
from app.services.run_service_background import (
    untrack_active_run as _bg_untrack_active_run,
)
from app.services.run_service_capabilities import (
    engine_capabilities_for_run,  # noqa: F401 - re-exported for route callers and tests.
    engine_route_rule_for_run,  # noqa: F401 - re-exported for route callers and tests.
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
_RUN_ENGINE_ROUTERS: dict[str, EngineRouter] = {}
_RUN_ENGINE_ROUTERS_LOCK = threading.RLock()
_ACCEPTING_NEW_RUNS = True
_PERSISTED_RUN_ROW_ERRORS = (ValidationError, TypeError)
_PERSISTED_RUN_STATE_ERRORS = (ValidationError, AttributeError)
_PERSISTED_AGENT_MESSAGE_ERRORS = (ValidationError, TypeError)
_PERSISTED_PLAN_ROW_ERRORS = (ValidationError,)
_PERSISTED_STORE_READ_ERRORS = (
    sqlite3.Error,
    json.JSONDecodeError,
    db.SensitiveRecordIntegrityError,
    ValidationError,
)
logger = logging.getLogger(__name__)


def _schedule_background(coro, *, data_dir: str | None = None) -> concurrent.futures.Future:
    return _bg_schedule_background(coro, data_dir=data_dir)


def _track_active_run(run_id: str, task: asyncio.Future | concurrent.futures.Future) -> None:
    _bg_track_active_run(run_id, task)


def _track_active_run_if_idle(run_id: str, task: asyncio.Future | concurrent.futures.Future) -> bool:
    return _bg_track_active_run_if_idle(run_id, task)


def _untrack_active_run(run_id: str) -> None:
    _bg_untrack_active_run(run_id)


def _run_active(run_id: str) -> bool:
    return _bg_run_active(run_id)


def _cancel_active_run_task(run_id: str, *, grace_seconds: float = 0.0) -> None:
    _bg_cancel_active_run_task(run_id, grace_seconds=grace_seconds)


def _register_resident_task(run_id: str, task: asyncio.Task) -> None:
    _bg_register_resident_task(run_id, task)


def _unregister_resident_task(run_id: str) -> None:
    _bg_unregister_resident_task(run_id)


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
    db.upsert_model("runs", run)
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
        },
    )
    _publish_plan_events(run.id, state)

    _track_run_router(run.id, router)
    task = _schedule_background(
        _start_engine_loop(run.id, router, state, task_id=run.task_id),
        data_dir=settings.data_dir,
    )
    _track_active_run(run.id, task)
    return run


async def _start_engine_loop(run_id: str, router: EngineRouter, state: RunState, *, task_id: str) -> None:
    """Set up the message bridge and drive the engine loop on the resident loop.

    The bus subscription must happen on the loop that consumes the queue
    (AgentBus binds the queue to the subscribing loop), so it lives here
    rather than in the request handler.
    """
    stop_event: asyncio.Event | None = None
    bridge_task: asyncio.Future | None = None
    if task_id:
        stop_event = asyncio.Event()
        bus = orchestrator_registry.bus_for_task(task_id)
        queue = bus.subscribe(task_id)
        bridge_task = asyncio.get_running_loop().create_task(
            _bridge_task_messages(run_id, task_id, queue, stop_event, bus=bus)
        )
    await _run_engine_loop(run_id, router, state, stop_event=stop_event, bridge_task=bridge_task)


def get_run(run_id: str) -> Run:
    data = db.fetch_one("runs", run_id)
    if not data:
        raise KeyError(run_id)
    return _sync_run_phase_from_task(Run.model_validate(data))


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


def pause_run(run_id: str) -> Run:
    run = get_run(run_id)
    if run.phase in TERMINAL_PHASES:
        return run
    if run.task_id:
        expired = _expire_pending_approvals(run.task_id, "pause_requested")
        _deny_waiting_steps_for_expired_approvals(run.task_id, expired)
        try:
            set_task_status(run.task_id, "paused")
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: pausing the run should still proceed.
            log_best_effort_failure(logger, "pause_run.set_task_status", exc, run_id=run.id, task_id=run.task_id)
    _sync_persisted_state_phase(run, RunPhase.PAUSED, "pause_requested")
    _update_run(run, phase=RunPhase.PAUSED)
    run_event_bus.publish(run.id, "turn.completed", {"reason": "pause_requested", "phase": run.phase.value})
    return run


def resume_run(run_id: str) -> Run:
    run = get_run(run_id)
    if run.phase in TERMINAL_PHASES or run.phase == RunPhase.AWAITING_APPROVAL:
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
            resumed.append(run)
            continue
        if run.phase in {RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED} or (
            include_approval_continuations and _is_approval_continuation(run)
        ):
            resumed.append(_schedule_resume(run))
    return resumed


def _schedule_resume(run: Run) -> Run:
    settings = get_effective_settings()
    router = _engine_router(settings)
    try:
        state = _state_from_run(run)
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        error = _redacted_error(exc)
        if not isinstance(run.state, dict):
            run.state = {}
        _update_run(run, phase=RunPhase.FAILED, error=error)
        run_event_bus.publish(run.id, "run.failed", {"error": error, "task_id": run.task_id})
        return run
    if run.phase == RunPhase.RUNNING and _run_active(run.id):
        return run
    _update_run(run, phase=RunPhase.RUNNING)
    run_event_bus.publish(run.id, "turn.started", {"reason": "resume_requested", "task_id": run.task_id})
    task = _schedule_background(_resume_engine_loop(run.id, router, state), data_dir=_run_data_dir(run))
    # Atomically claim the slot; if a concurrent resume already owns an active
    # loop for this run, cancel this duplicate before it can run side effects.
    if not _track_active_run_if_idle(run.id, task):
        task.cancel()
    return run


def cancel_run(run_id: str) -> Run:
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
    if run.task_id:
        expired = _expire_pending_approvals(run.task_id, "cancel_requested")
        _deny_waiting_steps_for_expired_approvals(run.task_id, expired)
        try:
            set_task_status(run.task_id, TaskPhase.CANCELLED)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: cancellation still records run state below.
            log_best_effort_failure(logger, "cancel_run.set_task_cancelled", exc, run_id=run.id, task_id=run.task_id)
    _update_run(run, phase=RunPhase.CANCELLED)
    _cancel_active_run_task(run.id, grace_seconds=2.0)
    run_event_bus.publish(run.id, "run.cancelled", {"task_id": run.task_id, "reason": "cancel_requested"})
    return run


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


async def _run_engine_loop(
    run_id: str,
    router: EngineRouter,
    state: RunState,
    *,
    stop_event: asyncio.Event | None,
    bridge_task: asyncio.Future | None,
) -> None:
    current_task = asyncio.current_task()
    if current_task is not None:
        _register_resident_task(run_id, current_task)
    try:
        current = state
        max_turns = max(1, int(router.max_turns))
        while current.turn_count < max_turns:
            if _run_cancelled(run_id):
                return
            run_event_bus.publish(
                run_id,
                "turn.started",
                {"turn": current.turn_count + 1, "engine": current.engine, "phase": current.phase.value},
            )
            result = await router.run_turn(current)
            if _run_cancelled(run_id) or _run_paused(run_id):
                if result.outputs:
                    _publish_turn_result(run_id, result)
                return
            _publish_turn_result(run_id, result)
            current = result.state
            run = get_run(run_id)
            if run.phase in {RunPhase.CANCELLED, RunPhase.PAUSED}:
                return
            _update_run_from_state(run, current)
            if result.finished or current.phase in ENGINE_TERMINAL_PHASES:
                if _run_cancelled(run_id):
                    return
                _publish_terminal_event(run_id, current, result)
                return
        run = get_run(run_id)
        if run.phase in {RunPhase.CANCELLED, RunPhase.PAUSED}:
            return
        _update_run(run, phase=RunPhase.FAILED, error=f"max turns reached ({max_turns})")
        run_event_bus.publish(run_id, "run.failed", {"reason": run.error, "max_turns": max_turns})
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: resident engine loop boundary: persist a failed run instead of leaking RUNNING.
        run = get_run(run_id)
        if run.phase == RunPhase.CANCELLED:
            return
        error = _redacted_error(exc)
        _update_run(run, phase=RunPhase.FAILED, error=error)
        run_event_bus.publish(run_id, "run.failed", {"error": error})
    finally:
        _unregister_resident_task(run_id)
        if stop_event is not None:
            stop_event.set()
        if bridge_task is not None:
            try:
                await asyncio.wait_for(bridge_task, timeout=0.5)
            except (TimeoutError, asyncio.CancelledError):
                bridge_task.cancel()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: run cleanup must release active-run state below.
                log_best_effort_failure(logger, "run_engine_loop.stop_bridge", exc, run_id=run_id)
        _untrack_active_run(run_id)
        _release_run_router(run_id)
        _release_terminal_orchestrator(run_id)


def _release_terminal_orchestrator(run_id: str) -> None:
    """Free the per-task orchestrator/bus cache once a run is terminal (R4-M5).

    Without this, orchestrator_registry._by_task keeps every finished task's
    orchestrator (+ bus + engine graph) alive for the process lifetime. Paused
    and awaiting-approval runs keep their binding: resume and approval
    execution must reuse the same bus.
    """
    try:
        run = get_run(run_id)
    except KeyError:
        return
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: cleanup should not affect terminal run completion.
        log_best_effort_failure(logger, "release_terminal_orchestrator.get_run", exc, run_id=run_id)
        return
    if run.phase not in TERMINAL_PHASES:
        return
    orchestrator_registry.release_run(run_id)
    if not run.task_id:
        return
    try:
        sibling_runs = db.fetch_many("runs", "task_id = ?", (run.task_id,), limit=50)
    except _PERSISTED_STORE_READ_ERRORS as exc:
        log_best_effort_failure(logger, "release_terminal_orchestrator.list_task_runs", exc, run_id=run_id)
        return
    for other in sibling_runs:
        if str(other.get("id")) == run_id:
            continue
        try:
            if RunPhase(str(other.get("phase"))) not in TERMINAL_PHASES:
                return
        except ValueError:
            return
    orchestrator_registry.release_task(run.task_id)


def _track_run_router(run_id: str, router: EngineRouter) -> None:
    with _RUN_ENGINE_ROUTERS_LOCK:
        _RUN_ENGINE_ROUTERS[run_id] = router


def _router_for_run(run_id: str, settings: AppSettings) -> EngineRouter:
    with _RUN_ENGINE_ROUTERS_LOCK:
        router = _RUN_ENGINE_ROUTERS.get(run_id)
    if router is not None:
        return router
    return _engine_router(settings)


def _release_run_router(run_id: str) -> None:
    with _RUN_ENGINE_ROUTERS_LOCK:
        _RUN_ENGINE_ROUTERS.pop(run_id, None)


async def _monitor_task_to_terminal(
    run_id: str,
    task_id: str,
    stop_event: asyncio.Event,
    bridge_task: asyncio.Future,
) -> None:
    try:
        for _ in range(600):
            await asyncio.sleep(0.1)
            task = get_task(task_id)
            phase = _phase_for_task(task)
            if phase not in TERMINAL_PHASES and phase != RunPhase.AWAITING_APPROVAL:
                continue
            run = get_run(run_id)
            _update_run(run, phase=phase)
            run_event_bus.publish(run_id, "turn.completed", {"task_id": task.id, "task_status": task.status.value})
            run_event_bus.publish(
                run_id,
                phase.event_name,
                {"task_id": task.id, "final_summary": task.final_summary, "phase": phase.value},
            )
            return
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(bridge_task, timeout=0.5)
        except (TimeoutError, asyncio.CancelledError):
            bridge_task.cancel()
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: terminal monitor already persisted the task outcome.
            log_best_effort_failure(logger, "monitor_task_terminal_phase.stop_bridge", exc, run_id=run_id)


async def _resume_engine_loop(run_id: str, router: EngineRouter, state: RunState) -> None:
    try:
        resumed = await router.engines[state.engine].resume_run(state.run_id)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: engine-specific resume may fail while persisted RunState is still usable.
        log_best_effort_failure(logger, "resume_engine_loop.resume_run", exc, run_id=run_id, engine=state.engine)
        resumed = state
    stop_event: asyncio.Event | None = None
    bridge_task: asyncio.Future | None = None
    _track_run_router(run_id, router)
    if resumed.task_id:
        stop_event = asyncio.Event()
        bus = orchestrator_registry.bus_for_task(resumed.task_id)
        queue = bus.subscribe(resumed.task_id)
        bridge_task = asyncio.get_running_loop().create_task(
            _bridge_task_messages(run_id, resumed.task_id, queue, stop_event, bus=bus)
        )
    await _run_engine_loop(run_id, router, resumed, stop_event=stop_event, bridge_task=bridge_task)


async def _bridge_task_messages(
    run_id: str,
    task_id: str,
    queue: asyncio.Queue,
    stop_event: asyncio.Event,
    *,
    bus: AgentBus,
) -> None:
    # Seed the dedupe set from events already persisted for this run so a
    # resume (or bus rebind) does not re-publish the full agent_message history
    # into the timeline. reconcile_task_runs already uses the same helper.
    seen_message_ids: set[str] = _seen_task_message_ids(run_id)
    try:
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run_id, message, seen_message_ids)
        while not stop_event.is_set():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            _publish_translated_message(run_id, message, seen_message_ids)
        # stop_event fires as soon as the run reaches a terminal phase; tail
        # messages (final tool.progress/tool.result) may still be queued or
        # only persisted. Drain both before unsubscribing or the run timeline
        # silently loses them.
        while True:
            try:
                message = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            _publish_translated_message(run_id, message, seen_message_ids)
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run_id, message, seen_message_ids)
    finally:
        bus.unsubscribe(task_id, queue)


def _publish_translated_message(run_id: str, message: Any, seen_message_ids: set[str]) -> None:
    if message.id in seen_message_ids:
        return
    seen_message_ids.add(message.id)
    translated = task_message_to_run_event(message, run_id=run_id)
    if translated is None:
        return
    name, payload = translated
    run_event_bus.publish(run_id, name, payload)
    if name == "plan.generated":
        for step in payload.get("structured_payload", {}).get("steps") or []:
            if isinstance(step, dict):
                run_event_bus.publish(run_id, "step.selected", {"task_id": message.task_id, "step": step})


def _seen_task_message_ids(run_id: str) -> set[str]:
    seen: set[str] = set()
    for event in db.fetch_run_events(run_id, limit=5000):
        payload = event.get("payload") or {}
        message_id = payload.get("message_id")
        if message_id:
            seen.add(str(message_id))
    return seen


def _has_run_event(run_id: str, name: str) -> bool:
    return any(str(event.get("name")) == name for event in db.fetch_run_events(run_id, limit=5000))


def _publish_task_phase_event_once(run: Run, phase: RunPhase, task: Any, *, reason: str) -> None:
    if phase not in TASK_SYNC_EVENT_PHASES:
        return
    event_name = phase.event_name
    if _has_run_event(run.id, event_name):
        return
    run_event_bus.publish(
        run.id,
        event_name,
        {
            "task_id": task.id,
            "task_status": task.status.value,
            "execution_stage": task.execution_stage.value,
            "final_summary": task.final_summary,
            "phase": phase.value,
            "reason": reason,
        },
    )


def _agent_message(raw: dict[str, Any]) -> Any | None:
    try:
        from app.core.schemas import AgentMessage

        return AgentMessage.model_validate(raw)
    except _PERSISTED_AGENT_MESSAGE_ERRORS as exc:
        message_id = raw.get("id") if isinstance(raw, dict) else ""
        logger.debug(
            "invalid agent message skipped while bridging run messages (message_id=%s): %s",
            message_id,
            _redacted_error(exc),
            exc_info=True,
        )
        return None


def _engine_router(settings: AppSettings) -> EngineRouter:
    from app.orchestration.developer_engine import DeveloperExecutionEngine
    from app.orchestration.os_execution_engine import OSExecutionEngine

    default_engine = settings.default_engine if settings.default_engine in {"auto", "os", "developer"} else "auto"
    return EngineRouter(
        {
            "os": OSExecutionEngine(),
            "developer": DeveloperExecutionEngine(settings=settings),
        },
        default_engine=default_engine,
        max_turns=settings.agent_loop_max_turns,
        developer_writes_enabled=bool(getattr(settings, "developer_writes_enabled", False)),
    )


def _engine_selection(engine: RunEngine) -> str:
    return engine.value if engine.value in {"auto", "os", "developer"} else "auto"


def _redacted_error(error: BaseException | str) -> str:
    return str(redact_value(str(error)))


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
    return RunState.model_validate(_run_state_payload(run.state))


def _run_data_dir(run: Run) -> str:
    runtime = run.state.get("_runtime") if isinstance(run.state, dict) else None
    if isinstance(runtime, dict) and runtime.get("data_dir"):
        return str(runtime["data_dir"])
    return ""


def _run_cancelled(run_id: str) -> bool:
    try:
        return get_run(run_id).phase == RunPhase.CANCELLED
    except KeyError:
        return False


def _run_paused(run_id: str) -> bool:
    try:
        return get_run(run_id).phase == RunPhase.PAUSED
    except KeyError:
        return False


def _is_approval_continuation(run: Run) -> bool:
    if run.phase != RunPhase.RUNNING:
        return False
    try:
        state = RunState.model_validate(_run_state_payload(run.state or {}))
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        log_best_effort_failure(logger, "is_approval_continuation.parse_state", exc, run_id=run.id)
        return False
    return state.continuation_kind == "approval_remaining_steps"


def _sync_persisted_state_phase(run: Run, phase: RunPhase, reason: str = "") -> None:
    if not run.state:
        return
    try:
        state = RunState.model_validate(_run_state_payload(run.state))
    except _PERSISTED_RUN_STATE_ERRORS as exc:
        log_best_effort_failure(logger, "sync_persisted_state_phase.parse_state", exc, run_id=run.id, phase=phase.value)
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
    return {key: value for key, value in (raw or {}).items() if not str(key).startswith("_")}


def _state_payload_for_run(run: Run, state: RunState) -> dict[str, Any]:
    payload = state.model_dump(mode="json")
    runtime = (run.state or {}).get("_runtime") if isinstance(run.state, dict) else None
    if isinstance(runtime, dict) and runtime:
        payload["_runtime"] = dict(runtime)
    return payload


def _update_run(run: Run, *, phase: RunPhase, task_id: str | None = None, error: str | None = None) -> Run:
    run.phase = phase
    if task_id is not None:
        run.task_id = task_id
    if error is not None:
        run.error = error
    run.updated_at = now_iso()
    db.upsert_model("runs", run)
    return run


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
    if _run_active(run.id) and run.phase == RunPhase.RUNNING and phase == RunPhase.PAUSED:
        return run
    if phase == RunPhase.RUNNING or phase == run.phase:
        return run
    _sync_persisted_state_phase(run, phase, task.final_summary)
    _update_run(run, phase=phase)
    _publish_task_phase_event_once(run, phase, task, reason="task_status_sync")
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


def _publish_plan_events(run_id: str, state: RunState) -> None:
    if not state.current_plan:
        return
    run_event_bus.publish(
        run_id,
        "plan.generated",
        {"plan": state.current_plan, "engine": state.engine, "turn": state.turn_count},
    )
    for step in state.current_plan.get("steps") or []:
        if isinstance(step, dict):
            run_event_bus.publish(run_id, "step.selected", {"step": step, "engine": state.engine})
            if step.get("tool"):
                run_event_bus.publish(
                    run_id,
                    "tool.proposed",
                    {"tool_name": step.get("tool"), "step_id": step.get("id"), "engine": state.engine},
                )


def _publish_turn_result(run_id: str, result: EngineTurnResult) -> None:
    state = result.state
    for source, payload in result.outputs.items():
        run_event_bus.publish(
            run_id,
            "tool.progress",
            {"tool_name": source, "status": "completed", "engine": state.engine, "turn": state.turn_count},
        )
        run_event_bus.publish(
            run_id,
            "tool.result",
            {"tool_name": source, "output": payload, "engine": state.engine, "turn": state.turn_count},
        )
        if isinstance(payload, dict):
            for event in [*(payload.get("lengrvis_events") or []), *(payload.get("events") or [])]:
                if not isinstance(event, dict):
                    continue
                name = event.get("name") or event.get("event")
                event_payload = event.get("payload") or {
                    key: value for key, value in event.items() if key not in {"name", "event"}
                }
                if isinstance(name, str) and isinstance(event_payload, dict):
                    run_event_bus.publish(
                        run_id,
                        name,
                        {**event_payload, "engine": state.engine, "turn": state.turn_count, "source": source},
                    )
    run_event_bus.publish(
        run_id,
        "turn.completed",
        {
            "turn": state.turn_count,
            "engine": state.engine,
            "phase": state.phase.value,
            "message": result.message,
            "transition_reason": state.transition_reason,
        },
    )


def _publish_terminal_event(run_id: str, state: RunState, result: EngineTurnResult) -> None:
    phase = RunPhase(state.phase.value)
    if phase == RunPhase.AWAITING_APPROVAL:
        event_name = "run.waiting_approval"
    elif phase == RunPhase.CANCELLED:
        event_name = "run.cancelled"
    elif phase in {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.DENIED}:
        event_name = phase.event_name
    else:
        return
    run_event_bus.publish(
        run_id,
        event_name,
        {
            "engine": state.engine,
            "phase": phase.value,
            "message": result.message,
            "transition_reason": state.transition_reason,
            "task_id": state.task_id,
        },
    )


def _phase_for_task(task: Any) -> RunPhase:
    if task.execution_stage.value == "awaiting_approval":
        return RunPhase.AWAITING_APPROVAL
    if task.status == TaskPhase.COMPLETED:
        return RunPhase.COMPLETED
    if task.status == TaskPhase.FAILED:
        return RunPhase.FAILED
    if task.status == TaskPhase.CANCELLED:
        return RunPhase.CANCELLED
    if task.execution_stage.value == "paused":
        return RunPhase.PAUSED
    return RunPhase.RUNNING


def _phase_for_task_plan(task: Any, plan: Plan | None) -> RunPhase:
    phase = _phase_for_task(task)
    if phase != RunPhase.CANCELLED:
        return phase
    summary = (getattr(task, "final_summary", "") or "").casefold()
    if "cancel" in summary or "rejected" in summary:
        return RunPhase.CANCELLED
    if "deny" in summary or "denied" in summary or "forbidden" in summary or "safety" in summary:
        return RunPhase.DENIED
    if plan is None:
        return RunPhase.CANCELLED
    if plan.global_risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
        return RunPhase.DENIED
    if any(step.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF for step in plan.steps):
        return RunPhase.DENIED
    if any(str(step.status) == "denied" for step in plan.steps):
        return RunPhase.DENIED
    return RunPhase.CANCELLED


# Public re-exports for callers/tests (patchable via _schedule_background in tests).
track_active_run = _track_active_run
untrack_active_run = _untrack_active_run
