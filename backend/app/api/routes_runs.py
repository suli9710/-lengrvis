from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.core.schemas import Run, RunCreateRequest, RunCreateResponse, RunStateResponse
from app.orchestration.run_event_bus import run_event_bus, run_event_to_wire
from app.security.lan import allow_lan_desktop_api, is_loopback_host
from app.services import run_service


router = APIRouter()
ws_router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse)
async def create_run(request: RunCreateRequest) -> RunCreateResponse:
    run = await run_service.create_run(request.message, request.mode, request.engine)
    return RunCreateResponse(run_id=run.id, engine=run.engine, phase=run.phase)


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
    client_host = websocket.client.host if websocket.client else ""
    if not is_loopback_host(client_host) and not allow_lan_desktop_api():
        await websocket.close(code=1008)
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
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "run_id": run_id})
                continue
            if event.sequence <= last_sequence:
                continue
            last_sequence = event.sequence
            await websocket.send_json(run_event_to_wire(event))
    except WebSocketDisconnect:
        pass
    finally:
        run_event_bus.unsubscribe(run_id, queue)


def _load_run(run_id: str) -> Run:
    try:
        return run_service.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found") from None


def _state_response(run: Run) -> RunStateResponse:
    return RunStateResponse(
        run_id=run.id,
        engine=run.engine,
        phase=run.phase,
        task_id=run.task_id,
        message=run.message,
        mode=run.mode,
        requested_engine=run.requested_engine,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )
