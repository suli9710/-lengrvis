from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.schemas import RunCreateResponse, RunEngine
from app.perception.intent_predictor import IntentSuggestion
from app.services import perception_suggestion_service


router = APIRouter()


class PerceptionStateResponse(BaseModel):
    available: bool
    sensitive_context_suppressed: bool = False
    screen_state: dict[str, Any] | None = None
    app_context: dict[str, Any] | None = None


class PerceptionCaptureResponse(BaseModel):
    screen_state: dict[str, Any]


class SuggestionLaunchRequest(BaseModel):
    mode: str = "efficiency"
    engine: RunEngine = RunEngine.AUTO


class SuggestionLaunchResponse(RunCreateResponse):
    suggestion_id: str


@router.get("/perception/state", response_model=PerceptionStateResponse)
def perception_state() -> dict[str, Any]:
    return perception_suggestion_service.current_state()


@router.get("/perception/suggestions", response_model=list[IntentSuggestion])
def perception_suggestions() -> list[IntentSuggestion]:
    return perception_suggestion_service.current_suggestions()


@router.post("/perception/capture", response_model=PerceptionCaptureResponse)
def capture_perception() -> PerceptionCaptureResponse:
    try:
        state = perception_suggestion_service.capture_once_summary()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Perception capture failed: {exc}") from exc
    return PerceptionCaptureResponse(screen_state=state)


@router.post("/perception/suggestions/{suggestion_id}/launch", response_model=SuggestionLaunchResponse)
async def launch_suggestion(suggestion_id: str, request: SuggestionLaunchRequest | None = None) -> SuggestionLaunchResponse:
    payload = request or SuggestionLaunchRequest()
    try:
        run = await perception_suggestion_service.launch_suggestion(
            suggestion_id,
            mode=payload.mode,
            engine=payload.engine,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Suggestion not found") from None
    return SuggestionLaunchResponse(
        suggestion_id=suggestion_id,
        run_id=run.id,
        engine=run.engine,
        phase=run.phase,
    )
