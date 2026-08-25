from __future__ import annotations

import base64
import binascii
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.schemas import RunCreateResponse, RunEngine
from app.observability.best_effort import log_best_effort_failure
from app.perception.intent_predictor import IntentSuggestion
from app.perception.voice_input import (
    DEFAULT_MAX_AUDIO_BYTES,
    AudioBufferLimitError,
    DeterministicFallbackTranscriber,
    VoiceInputProcessor,
    WhisperCppTranscriber,
)
from app.services import perception_suggestion_service

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_VOICE_AUDIO_BYTES = DEFAULT_MAX_AUDIO_BYTES  # ~5 minutes of 16 kHz mono PCM16.
MAX_VOICE_AUDIO_BASE64_CHARS = ((MAX_VOICE_AUDIO_BYTES + 2) // 3) * 4
_voice_processor: VoiceInputProcessor | None = None


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
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        log_best_effort_failure(logger, "perception.capture", exc)
        raise HTTPException(status_code=503, detail="Perception capture is temporarily unavailable.") from None
    return PerceptionCaptureResponse(screen_state=state)


class VoiceHealthResponse(BaseModel):
    available: bool
    provider: str
    detail: str = ""


class VoiceTranscribeRequest(BaseModel):
    # Base64-encoded PCM16 little-endian mono audio.
    audio_base64: str = Field(min_length=1)
    sample_rate: int = 16_000
    language: str | None = None


class VoiceTranscribeResponse(BaseModel):
    transcript: str
    confidence: float | None = None
    language: str = ""
    provider: str = ""


def _get_voice_processor() -> VoiceInputProcessor:
    global _voice_processor
    if _voice_processor is None:
        _voice_processor = VoiceInputProcessor()
    return _voice_processor


def reset_voice_processor_for_tests() -> None:
    global _voice_processor
    _voice_processor = None


@router.get("/perception/voice/health", response_model=VoiceHealthResponse)
def voice_health() -> VoiceHealthResponse:
    if WhisperCppTranscriber.available():
        return VoiceHealthResponse(available=True, provider=WhisperCppTranscriber.provider_name)
    return VoiceHealthResponse(
        available=False,
        provider=DeterministicFallbackTranscriber.provider_name,
        detail="本地语音识别引擎(pywhispercpp)未安装；语音输入暂不可用。",
    )


@router.post("/perception/voice/transcribe", response_model=VoiceTranscribeResponse)
async def voice_transcribe(request: VoiceTranscribeRequest) -> VoiceTranscribeResponse:
    if len(request.audio_base64) > MAX_VOICE_AUDIO_BASE64_CHARS:
        raise HTTPException(status_code=413, detail="audio_base64 exceeds the 10 MB decoded payload limit")
    try:
        audio = base64.b64decode(request.audio_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="audio_base64 is not valid base64") from None
    if not audio:
        raise HTTPException(status_code=400, detail="audio payload is empty")
    if len(audio) > MAX_VOICE_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio payload exceeds the 10 MB limit")
    sample_rate = max(8_000, min(int(request.sample_rate or 16_000), 48_000))

    processor = _get_voice_processor()
    try:
        payload = audio if sample_rate == 16_000 else _resample_pcm16(audio, sample_rate)
        # Pass the language per call: assigning it on the shared module-level
        # processor would race with concurrent requests that use a different
        # language (the assignment happens before an await).
        event = await processor.process_utterance(payload, language=request.language or None)
    except AudioBufferLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    if event is None:
        return VoiceTranscribeResponse(transcript="", provider=type(processor.transcriber).__name__)
    return VoiceTranscribeResponse(
        transcript=event.transcript,
        confidence=event.confidence,
        language=event.language,
        provider=str(event.metadata.get("provider") or ""),
    )


def _resample_pcm16(audio: bytes, sample_rate: int, *, target_rate: int = 16_000) -> bytes:
    """Naive nearest-sample resampler good enough for speech transcription."""
    if sample_rate == target_rate or not audio:
        return audio
    import array

    samples = array.array("h")
    samples.frombytes(audio[: len(audio) - (len(audio) % 2)])
    if not samples:
        return audio
    ratio = sample_rate / target_rate
    resampled = array.array(
        "h",
        (samples[min(int(index * ratio), len(samples) - 1)] for index in range(int(len(samples) / ratio))),
    )
    return resampled.tobytes()


@router.post(
    "/perception/suggestions/{suggestion_id}/launch",
    response_model=SuggestionLaunchResponse,
)
async def launch_suggestion(
    suggestion_id: str,
    request: SuggestionLaunchRequest | None = None,
) -> SuggestionLaunchResponse:
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
