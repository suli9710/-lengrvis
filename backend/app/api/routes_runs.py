from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.api.task_public_views import empty_completion_evidence
from app.core import db
from app.core.schemas import Run, RunCreateRequest, RunCreateResponse, RunStateResponse
from app.orchestration.run_event_bus import run_event_bus, run_event_to_wire
from app.policy.redaction import redact_run_payload
from app.security.desktop_api import close_unauthorized_desktop_websocket
from app.services import run_service
from app.services.task_explain_service import build_task_completion_evidence, build_task_result_quality
from app.services.task_service import get_task

router = APIRouter()
ws_router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse)
async def create_run(request: RunCreateRequest) -> RunCreateResponse:
    hint = request.agent_hint.strip() or None
    run = await run_service.create_run(request.message, request.mode, request.engine, agent_hint=hint)
    return RunCreateResponse(
        run_id=run.id,
        engine=run.engine,
        phase=run.phase,
        engine_route_rule=run_service.engine_route_rule_for_run(run),
        engine_capabilities=run_service.engine_capabilities_for_run(run),
    )


@router.get("/runs", response_model=list[RunStateResponse])
def list_runs() -> list[RunStateResponse]:
    return [_state_response(run) for run in run_service.list_runs()]


@router.get("/runs/{run_id}", response_model=RunStateResponse)
def get_run(run_id: str) -> RunStateResponse:
    return _state_response(_load_run(run_id))


@router.get("/runs/{run_id}/timeline")
def timeline(run_id: str):
    try:
        return run_service.get_timeline(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


@router.get("/runs/{run_id}/progress")
def progress(run_id: str):
    try:
        return run_service.get_progress(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


@router.post("/runs/{run_id}/pause", response_model=RunStateResponse)
def pause(run_id: str) -> RunStateResponse:
    try:
        return _state_response(run_service.pause_run(run_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


@router.post("/runs/{run_id}/resume", response_model=RunStateResponse)
def resume(run_id: str) -> RunStateResponse:
    try:
        return _state_response(run_service.resume_run(run_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


@router.post("/runs/{run_id}/cancel", response_model=RunStateResponse)
def cancel(run_id: str) -> RunStateResponse:
    try:
        return _state_response(run_service.cancel_run(run_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


@ws_router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: str):
    if await close_unauthorized_desktop_websocket(websocket):
        return
    try:
        run = run_service.get_run(run_id)
    except KeyError:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = run_event_bus.subscribe(run_id)
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "run_id": run.id,
                "engine": run.engine.value,
                "phase": run.phase.value,
            }
        )
        last_sequence = 0
        for event in run_event_bus.replay(run_id):
            last_sequence = max(last_sequence, event.sequence)
            await websocket.send_json(run_event_to_wire(event, replay=True))
        await websocket.send_json({"type": "replay.completed", "run_id": run_id, "last_sequence": last_sequence})

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat", "run_id": run_id})
                continue
            if event.sequence <= last_sequence:
                continue
            if event.sequence > last_sequence + 1:
                last_sequence = await _replay_missing_events(
                    websocket,
                    run_id,
                    last_sequence=last_sequence,
                    target_sequence=event.sequence,
                )
                if event.sequence <= last_sequence:
                    continue
            last_sequence = event.sequence
            await websocket.send_json(run_event_to_wire(event))
    except WebSocketDisconnect:
        return
    finally:
        run_event_bus.unsubscribe(run_id, queue)


def _load_run(run_id: str) -> Run:
    try:
        return run_service.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


async def _replay_missing_events(
    websocket: WebSocket,
    run_id: str,
    *,
    last_sequence: int,
    target_sequence: int,
) -> int:
    while last_sequence + 1 < target_sequence:
        replayed_events = run_event_bus.replay(run_id, after_sequence=last_sequence)
        if not replayed_events:
            break
        advanced = False
        for replayed_event in replayed_events:
            if replayed_event.sequence <= last_sequence:
                continue
            if replayed_event.sequence >= target_sequence:
                return last_sequence
            last_sequence = replayed_event.sequence
            advanced = True
            await websocket.send_json(run_event_to_wire(replayed_event, replay=True))
        if not advanced:
            break
    return last_sequence


def _state_response(run: Run) -> RunStateResponse:
    completion_evidence, result_quality = _run_result_contract(run)
    return RunStateResponse(
        run_id=run.id,
        engine=run.engine,
        phase=run.phase,
        task_id=run.task_id,
        message=redact_run_payload(run.message),
        mode=run.mode,
        requested_engine=run.requested_engine,
        engine_route_rule=run_service.engine_route_rule_for_run(run),
        error=redact_run_payload(run.error),
        created_at=run.created_at,
        updated_at=run.updated_at,
        engine_capabilities=run_service.engine_capabilities_for_run(run),
        completion_evidence=completion_evidence,
        result_quality=result_quality,
    )


def _run_result_contract(run: Run) -> tuple[dict, dict]:
    completion_evidence = empty_completion_evidence()
    if not run.task_id:
        return completion_evidence, build_task_result_quality(None, completion_evidence)

    try:
        task = get_task(run.task_id)
    except KeyError:
        return completion_evidence, build_task_result_quality(None, completion_evidence)

    messages = db.fetch_many_by_fields("agent_messages", {"task_id": task.id}, limit=5000)
    reviews = db.fetch_many_by_fields("safety_reviews", {"task_id": task.id}, limit=5000)
    audits = db.fetch_many_by_fields("audit_events", {"task_id": task.id}, limit=5000)
    completion_evidence = build_task_completion_evidence(
        task,
        messages=messages,
        reviews=reviews,
        audits=audits,
    )
    result_quality = build_task_result_quality(task, completion_evidence)
    return completion_evidence, result_quality
