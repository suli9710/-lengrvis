from __future__ import annotations

from app.perception.app_context import get_current_app_context
from app.perception.schemas import (
    AppContext,
    PerceptionEvent,
    PerceptionProvider,
    Rect,
    ScreenState,
    UIElement,
)
from app.perception.screen_monitor import ScreenMonitor, ScreenMonitorConfig
from app.perception.context_store import (
    clear,
    handle_perception_event,
    latest_app_context,
    latest_perception_context,
    latest_screen_state,
    update_app_context,
    update_screen_state,
)
from app.perception.intent_predictor import IntentPredictor, IntentSuggestion, predict_intents
from app.perception.voice_input import (
    AudioChunk,
    ChatEndpointClient,
    DeterministicFallbackTranscriber,
    TranscriptionResult,
    VoiceInputEvent,
    VoiceInputProcessor,
    WakeWordGate,
    WhisperCppTranscriber,
)

__all__ = [
    "AudioChunk",
    "AppContext",
    "ChatEndpointClient",
    "DeterministicFallbackTranscriber",
    "PerceptionEvent",
    "PerceptionProvider",
    "Rect",
    "ScreenMonitor",
    "ScreenMonitorConfig",
    "ScreenState",
    "TranscriptionResult",
    "UIElement",
    "VoiceInputEvent",
    "VoiceInputProcessor",
    "WakeWordGate",
    "WhisperCppTranscriber",
    "clear",
    "get_current_app_context",
    "handle_perception_event",
    "IntentPredictor",
    "IntentSuggestion",
    "latest_app_context",
    "latest_perception_context",
    "latest_screen_state",
    "predict_intents",
    "update_app_context",
    "update_screen_state",
]
