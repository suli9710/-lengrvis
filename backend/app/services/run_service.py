from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any

from app.config import AppSettings
from app.core import db
from app.core.schemas import Approval, Run, RunEngine, RunEvent, RunPhase, now_iso
from app.llm.registry import get_effective_settings
from app.orchestration.agent_bus import AgentBus
from app.orchestration.engine_router import EngineRouter
from app.orchestration.execution_engine import default_run_store
from app.orchestration.execution_models import EngineTurnResult, RunPhase as EngineRunPhase, RunState
from app.orchestration.run_event_bus import run_event_bus, task_message_to_run_event
from app.orchestration.task_phase import TaskPhase
from app.services.task_service import get_task, set_task_status


TERMINAL_PHASES = {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.DENIED, RunPhase.CANCELLED}
ENGINE_TERMINAL_PHASES = {
    EngineRunPhase.AWAITING_APPROVAL,
    EngineRunPhase.COMPLETED,
    EngineRunPhase.FAILED,
    EngineRunPhase.DENIED,
    EngineRunPhase.CANCELLED,
}
_ACTIVE_RUN_TASKS: dict[str, asyncio.Future | concurrent.futures.Future] = {}
_ACTIVE_RUN_TASKS_LOCK = threading.RLock()


async def create_run(message: str, mode: str, requested_engine: RunEngine) -> Run:
    db.init_db()
    settings = get_effective_settings()
    run_event_bus.prune_old_events(settings)
    router = _engine_router(settings)

    try:
        state = await router.start_run(message, mode, _engine_selection(requested_engine))
    except Exception as exc:  # noqa: BLE001
        run = Run(
            message=message,
            mode=mode,
            requested_engine=requested_engine,
            engine=RunEngine.DEVELOPER if requested_engine == RunEngine.DEVELOPER else RunEngine.OS,
            phase=RunPhase.FAILED,
            error=str(exc),
        )
        db.upsert_model("runs", run)
        run_event_bus.publish(run.id, "run.failed", {"error": str(exc), "message": message, "mode": mode})
        return run

    run = _run_from_state(state, requested_engine=requested_engine)
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
        },
    )
    _publish_plan_events(run.id, state)

    stop_event: asyncio.Event | None = None
    bridge_task: asyncio.Future | None = None
    if run.task_id:
        stop_event = asyncio.Event()
        queue = AgentBus().subscribe(run.task_id)
        bridge_task = _schedule_background(_bridge_task_messages(run.id, run.task_id, queue, stop_event))
    task = _schedule_background(_run_engine_loop(run.id, router, state, stop_event=stop_event, bridge_task=bridge_task))
    _track_active_run(run.id, task)
    return run


def get_run(run_id: str) -> Run:
    data = db.fetch_one("runs", run_id)
    if not data:
        raise KeyError(run_id)
    return Run.model_validate(data)


def list_runs(limit: int = 100) -> list[Run]:
    return [Run.model_validate(item) for item in db.fetch_many("runs", limit=limit)]


def get_timeline(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    events = [event.model_dump(mode="json") for event in list_run_events(run_id)]
    return {"run": run.model_dump(mode="json"), "events": events, "count": len(events)}


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
        "latest_event": latest.model_dump(mode="json") if latest else None,
        "progress": [event.model_dump(mode="json") for event in progress_events],
        "count": len(progress_events),
    }


def list_run_events(run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[RunEvent]:
    get_run(run_id)
    return run_event_bus.replay(run_id, after_sequence=after_sequence, limit=limit)


def pause_run(run_id: str) -> Run:
    run = get_run(run_id)
    if run.task_id:
        _expire_pending_approvals(run.task_id, "pause_requested")
        try:
            set_task_status(run.task_id, "paused")
        except Exception:
            pass
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
    except Exception as exc:  # noqa: BLE001
        _update_run(run, phase=RunPhase.FAILED, error=str(exc))
        run_event_bus.publish(run.id, "run.failed", {"error": str(exc), "task_id": run.task_id})
        return run
    if run.phase == RunPhase.RUNNING and _run_active(run.id):
        return run
    _update_run(run, phase=RunPhase.RUNNING)
    run_event_bus.publish(run.id, "turn.started", {"reason": "resume_requested", "task_id": run.task_id})
    task = _schedule_background(_resume_engine_loop(run.id, router, state))
    _track_active_run(run.id, task)
    return run


def cancel_run(run_id: str) -> Run:
    run = get_run(run_id)
    _cancel_persisted_state(run)
    if run.task_id:
        _expire_pending_approvals(run.task_id, "cancel_requested")
        try:
            set_task_status(run.task_id, TaskPhase.CANCELLED)
        except Exception:
            pass
    _update_run(run, phase=RunPhase.CANCELLED)
    run_event_bus.publish(run.id, "run.cancelled", {"task_id": run.task_id, "reason": "cancel_requested"})
    return run


def _expire_pending_approvals(task_id: str, reason: str) -> None:
    try:
        expired = db.expire_pending_approvals_for_task(task_id, now_iso(), reason)
    except Exception:
        return
    if not expired:
        return
    try:
        from app.services.approval_event_service import publish_approval_decided

        for item in expired:
            publish_approval_decided(Approval.model_validate(item))
    except Exception:
        return


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
            event_name = phase.event_name
            if event_name in {"run.completed", "run.failed", "run.denied", "run.cancelled", "run.waiting_approval"}:
                run_event_bus.publish(
                    run.id,
                    event_name,
                    {
                        "task_id": task.id,
                        "task_status": task.status.value,
                        "execution_stage": task.execution_stage.value,
                        "final_summary": task.final_summary,
                        "phase": phase.value,
                        "reason": "task_reconciled",
                    },
                )
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
    except Exception as exc:  # noqa: BLE001
        run = get_run(run_id)
        if run.phase == RunPhase.CANCELLED:
            return
        _update_run(run, phase=RunPhase.FAILED, error=str(exc))
        run_event_bus.publish(run_id, "run.failed", {"error": str(exc)})
    finally:
        if stop_event is not None:
            stop_event.set()
        if bridge_task is not None:
            try:
                await asyncio.wait_for(bridge_task, timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                bridge_task.cancel()
            except Exception:
                pass
        _untrack_active_run(run_id)


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
        except (asyncio.TimeoutError, asyncio.CancelledError):
            bridge_task.cancel()
        except Exception:
            pass


async def _resume_engine_loop(run_id: str, router: EngineRouter, state: RunState) -> None:
    try:
        resumed = await router.engines[state.engine].resume_run(state.run_id)
    except Exception:
        resumed = state
    stop_event: asyncio.Event | None = None
    bridge_task: asyncio.Future | None = None
    if resumed.task_id:
        stop_event = asyncio.Event()
        queue = AgentBus().subscribe(resumed.task_id)
        bridge_task = _schedule_background(_bridge_task_messages(run_id, resumed.task_id, queue, stop_event))
    await _run_engine_loop(run_id, router, resumed, stop_event=stop_event, bridge_task=bridge_task)


async def _bridge_task_messages(
    run_id: str,
    task_id: str,
    queue: asyncio.Queue,
    stop_event: asyncio.Event,
) -> None:
    seen_message_ids: set[str] = set()
    try:
        for raw in reversed(db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=1000)):
            message = _agent_message(raw)
            if message is not None:
                _publish_translated_message(run_id, message, seen_message_ids)
        while not stop_event.is_set():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            _publish_translated_message(run_id, message, seen_message_ids)
    finally:
        AgentBus().unsubscribe(task_id, queue)


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


def _agent_message(raw: dict[str, Any]) -> Any | None:
    try:
        from app.core.schemas import AgentMessage

        return AgentMessage.model_validate(raw)
    except Exception:
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
    )


def _engine_selection(engine: RunEngine) -> str:
    return engine.value if engine.value in {"auto", "os", "developer"} else "auto"


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
    run.state = state.model_dump(mode="json")
    return _update_run(
        run,
        phase=RunPhase(state.phase.value),
        task_id=state.task_id or None,
        error=state.transition_reason if state.phase == EngineRunPhase.FAILED else run.error,
    )


def _state_from_run(run: Run) -> RunState:
    try:
        return default_run_store.get(run.id)
    except KeyError:
        pass
    if run.state:
        state = RunState.model_validate(run.state)
        return default_run_store.put(state)
    state = RunState(
        run_id=run.id,
        engine="developer" if run.engine == RunEngine.DEVELOPER else "os",
        phase=EngineRunPhase(run.phase.value),
        goal=run.message,
        mode=run.mode,
        task_id=run.task_id or "",
    )
    return default_run_store.put(state)


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
        state = RunState.model_validate(run.state or {})
    except Exception:
        return False
    return "continuing remaining plan steps" in state.transition_reason.casefold()


def _sync_persisted_state_phase(run: Run, phase: RunPhase, reason: str = "") -> None:
    if not run.state:
        return
    try:
        state = RunState.model_validate(run.state)
    except Exception:
        return
    state = state.model_copy(
        update={
            "phase": EngineRunPhase(phase.value),
            "transition_reason": reason or state.transition_reason,
        },
        deep=True,
    )
    run.state = state.model_dump(mode="json")
    default_run_store.put(state)


def _cancel_persisted_state(run: Run) -> None:
    try:
        state = _state_from_run(run)
    except Exception:
        return
    state = state.model_copy(
        update={"phase": EngineRunPhase.CANCELLED, "transition_reason": "cancel_requested"},
        deep=True,
    )
    run.state = state.model_dump(mode="json")
    default_run_store.put(state)


def _update_run(run: Run, *, phase: RunPhase, task_id: str | None = None, error: str | None = None) -> Run:
    run.phase = phase
    if task_id is not None:
        run.task_id = task_id
    if error is not None:
        run.error = error
    run.updated_at = now_iso()
    db.upsert_model("runs", run)
    return run


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


def _schedule_background(coro) -> asyncio.Future:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        future: concurrent.futures.Future = concurrent.futures.Future()

        def runner() -> None:
            try:
                result = asyncio.run(coro)
            except Exception as exc:  # noqa: BLE001
                future.set_exception(exc)
            else:
                future.set_result(result)

        threading.Thread(target=runner, name="run-service-background", daemon=True).start()
        return future
    return loop.create_task(coro)


def _track_active_run(run_id: str, task: asyncio.Future | concurrent.futures.Future) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _ACTIVE_RUN_TASKS[run_id] = task


def _untrack_active_run(run_id: str) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _ACTIVE_RUN_TASKS.pop(run_id, None)


def _run_active(run_id: str) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        task = _ACTIVE_RUN_TASKS.get(run_id)
    if task is None:
        return False
    return not task.done()
