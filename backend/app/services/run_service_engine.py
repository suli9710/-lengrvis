from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading

from app.config import AppSettings
from app.core import db
from app.core.schemas import RunEngine, RunPhase
from app.observability import context as observability_context
from app.observability.best_effort import log_best_effort_failure
from app.observability.tracing import span
from app.orchestration.engine_router import EngineRouter
from app.orchestration.execution_models import RunState
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.run_event_bus import run_event_bus
from app.services.run_service_background import (
    active_run_owned_by as _active_run_owned_by,
)
from app.services.run_service_background import (
    register_resident_task as _register_resident_task,
)
from app.services.run_service_background import (
    unregister_resident_task as _unregister_resident_task,
)
from app.services.run_service_background import (
    untrack_active_run as _untrack_active_run,
)
from app.services.run_service_bridge import _bridge_task_messages
from app.services.run_service_events import (
    publish_terminal_event as _publish_terminal_event,
)
from app.services.run_service_events import (
    publish_turn_result as _publish_turn_result,
)
from app.services.run_service_state import (
    _PERSISTED_STORE_READ_ERRORS,
    ENGINE_TERMINAL_PHASES,
    TERMINAL_PHASES,
    _ensure_run_trace_context,
    _fail_run_task,
    _finish_lifecycle_span,
    _get_run,
    _redacted_error,
    _run_cancelled,
    _run_paused,
    _update_run,
    _update_run_from_state,
)

logger = logging.getLogger(__name__)

_RUN_ENGINE_ROUTERS: dict[str, EngineRouter] = {}
_RUN_ENGINE_ROUTERS_LOCK = threading.RLock()


async def _start_engine_loop(
    run_id: str,
    router: EngineRouter,
    state: RunState,
    *,
    task_id: str,
    active_owner: asyncio.Future | concurrent.futures.Future | None = None,
) -> None:
    stop_event: asyncio.Event | None = None
    bridge_task: asyncio.Future | None = None
    if task_id:
        stop_event = asyncio.Event()
        bus = orchestrator_registry.bus_for_task(task_id)
        queue = bus.subscribe(task_id)
        bridge_task = asyncio.get_running_loop().create_task(
            _bridge_task_messages(run_id, task_id, queue, stop_event, bus=bus)
        )
    await _run_engine_loop(
        run_id,
        router,
        state,
        stop_event=stop_event,
        bridge_task=bridge_task,
        active_owner=active_owner,
    )


async def _run_engine_loop(
    run_id: str,
    router: EngineRouter,
    state: RunState,
    *,
    stop_event: asyncio.Event | None,
    bridge_task: asyncio.Future | None,
    active_owner: asyncio.Future | concurrent.futures.Future | None = None,
) -> None:
    try:
        run = _get_run(run_id)
        trace_context = _ensure_run_trace_context(run)
    except Exception as exc:  # noqa: BLE001
        log_best_effort_failure(logger, "run_trace.context_lookup", exc, run_id=run_id)
        trace_context = {
            "trace_id": observability_context.get_trace_id() or observability_context.new_trace_id(),
            "run_span_id": observability_context.get_span_id() or observability_context.new_span_id(),
            "parent_span_id": "",
        }
    with span(
        "run.execute",
        {"run.id": run_id, "run.engine": state.engine, "task.id": state.task_id or ""},
        trace_id=trace_context["trace_id"],
        parent_span_id=trace_context["run_span_id"],
    ) as run_span:
        try:
            if state.task_id:
                with span(
                    "task.execute",
                    {"task.id": state.task_id, "run.id": run_id, "task.phase": state.phase.value},
                ) as task_span:
                    try:
                        await _run_engine_loop_body(
                            run_id,
                            router,
                            state,
                            stop_event=stop_event,
                            bridge_task=bridge_task,
                            active_owner=active_owner,
                        )
                    finally:
                        _finish_lifecycle_span(task_span, run_id=run_id, task_id=state.task_id)
            else:
                await _run_engine_loop_body(
                    run_id,
                    router,
                    state,
                    stop_event=stop_event,
                    bridge_task=bridge_task,
                    active_owner=active_owner,
                )
        finally:
            _finish_lifecycle_span(run_span, run_id=run_id, task_id=state.task_id)


async def _run_engine_loop_body(
    run_id: str,
    router: EngineRouter,
    state: RunState,
    *,
    stop_event: asyncio.Event | None,
    bridge_task: asyncio.Future | None,
    active_owner: asyncio.Future | concurrent.futures.Future | None = None,
) -> None:
    current_task = asyncio.current_task()
    resident_registered = False
    if current_task is not None:
        resident_registered = _register_resident_task(run_id, current_task, active_owner)
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
            run = _get_run(run_id)
            if run.phase in {RunPhase.CANCELLED, RunPhase.PAUSED}:
                return
            _update_run_from_state(run, current)
            if result.finished or current.phase in ENGINE_TERMINAL_PHASES:
                if _run_cancelled(run_id):
                    return
                _publish_terminal_event(run_id, current, result)
                return
        run = _get_run(run_id)
        if run.phase in {RunPhase.CANCELLED, RunPhase.PAUSED}:
            return
        _update_run(run, phase=RunPhase.FAILED, error=f"max turns reached ({max_turns})")
        _fail_run_task(run)
        run_event_bus.publish(run_id, "run.failed", {"reason": run.error, "max_turns": max_turns})
    except Exception as exc:  # noqa: BLE001
        run = _get_run(run_id)
        if run.phase in {RunPhase.CANCELLED, RunPhase.PAUSED}:
            return
        error = _redacted_error(exc)
        import httpx

        retryable = isinstance(exc, httpx.TransportError | TimeoutError | ConnectionError) or (
            isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {408, 429, 500, 502, 503, 504}
        )
        run.state["failure"] = {"error_type": type(exc).__name__, "retryable_dependency": retryable}
        _update_run(run, phase=RunPhase.FAILED, error=error)
        _fail_run_task(run)
        run_event_bus.publish(run_id, "run.failed", {"error": error, **run.state["failure"]})
    finally:
        if resident_registered:
            _unregister_resident_task(run_id, current_task)
        if stop_event is not None:
            stop_event.set()
        if bridge_task is not None:
            try:
                await asyncio.wait_for(bridge_task, timeout=0.5)
            except (TimeoutError, asyncio.CancelledError):
                bridge_task.cancel()
            except Exception as exc:  # noqa: BLE001
                log_best_effort_failure(logger, "run_engine_loop.stop_bridge", exc, run_id=run_id)
        if active_owner is None or _active_run_owned_by(run_id, active_owner):
            _release_run_router(run_id)
            _release_terminal_orchestrator(run_id)
            _untrack_active_run(run_id, active_owner)


def _release_terminal_orchestrator(run_id: str) -> None:
    try:
        run = _get_run(run_id)
    except KeyError:
        return
    except Exception as exc:  # noqa: BLE001
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
        log_best_effort_failure(
            logger,
            "release_terminal_orchestrator.list_task_runs",
            exc,
            run_id=run_id,
        )
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


async def _resume_engine_loop(
    run_id: str,
    router: EngineRouter,
    state: RunState,
    *,
    active_owner: asyncio.Future | concurrent.futures.Future | None = None,
) -> None:
    try:
        resumed = await router.engines[state.engine].resume_run(state.run_id)
    except Exception as exc:  # noqa: BLE001
        log_best_effort_failure(
            logger,
            "resume_engine_loop.resume_run",
            exc,
            run_id=run_id,
            engine=state.engine,
        )
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
    await _run_engine_loop(
        run_id,
        router,
        resumed,
        stop_event=stop_event,
        bridge_task=bridge_task,
        active_owner=active_owner,
    )


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
