from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import logging
import os
import tempfile
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import Field

from app.core.schemas import new_id, now_iso
from app.orchestration.agent_bus import GLOBAL_TASK_ID
from app.perception.schemas import PerceptionEvent, ScreenState

logger = logging.getLogger(__name__)


class VoiceTranscriber(Protocol):
    def transcribe(self, audio: bytes, *, sample_rate: int = 16_000, language: str | None = None) -> str | "TranscriptionResult":
        """Return text for a single audio buffer."""


@dataclass(slots=True)
class AudioChunk:
    data: bytes
    sample_rate: int = 16_000
    channels: int = 1
    is_final: bool = False
    timestamp: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = now_iso()
        self.sample_rate = int(self.sample_rate or 16_000)
        self.channels = max(1, int(self.channels or 1))
        self.metadata = dict(self.metadata or {})


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    confidence: float | None = None
    language: str = ""
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.text = normalize_transcript(self.text)
        self.metadata = dict(self.metadata or {})


class VoiceInputEvent(PerceptionEvent):
    event_type: str = "perception.voice_input"
    task_id: str = GLOBAL_TASK_ID
    source_agent: str = "VoiceInput"
    screen_state: ScreenState | None = None
    transcript: str = ""
    raw_transcript: str = ""
    confidence: float | None = None
    language: str = ""
    wake_word_detected: bool = False
    wake_word: str = ""
    auto_submitted: bool = False
    submit_result: Any | None = None
    audio_metadata: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        body = self.transcript or self.raw_transcript
        if body:
            return f"Voice input: {body[:80]}"
        return "Voice input observed"


class DeterministicFallbackTranscriber:
    """Dependency-free fallback for tests and offline operation.

    It extracts text only when the buffer is already text-like. Binary audio is
    intentionally represented as no transcript instead of hallucinated speech.
    """

    provider_name = "deterministic_fallback"

    def transcribe(self, audio: bytes, *, sample_rate: int = 16_000, language: str | None = None) -> TranscriptionResult:
        text = _decode_text_like_audio(audio)
        digest = hashlib.sha256(audio).hexdigest()
        return TranscriptionResult(
            text=text,
            confidence=1.0 if text else 0.0,
            language=language or "",
            metadata={
                "provider": self.provider_name,
                "sample_rate": sample_rate,
                "sha256": digest,
                "bytes": len(audio),
            },
        )


class WhisperCppTranscriber:
    """Thin optional wrapper around pywhispercpp.

    The binding is imported lazily so importing this module never requires
    pywhispercpp. Tests can also inject a model instance or model class.
    """

    provider_name = "pywhispercpp"

    def __init__(
        self,
        model_path: str = "base.en",
        *,
        model: Any | None = None,
        model_cls: Any | None = None,
        model_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.model_path = model_path
        self.model_kwargs = dict(model_kwargs or {})
        self._model = model
        self._model_cls = model_cls

    @classmethod
    def available(cls) -> bool:
        try:
            cls._import_model_cls()
        except Exception:
            return False
        return True

    @classmethod
    def _import_model_cls(cls) -> Any:
        from pywhispercpp.model import Model  # type: ignore[import-not-found]

        return Model

    @property
    def model(self) -> Any:
        if self._model is None:
            model_cls = self._model_cls or self._import_model_cls()
            self._model = model_cls(self.model_path, **self.model_kwargs)
        return self._model

    def transcribe(self, audio: bytes, *, sample_rate: int = 16_000, language: str | None = None) -> TranscriptionResult:
        wav_audio = pcm16_to_wav(audio, sample_rate=sample_rate)
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                handle.write(wav_audio)
                temp_path = handle.name
            segments = self.model.transcribe(temp_path, language=language) if language else self.model.transcribe(temp_path)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    logger.debug("Failed to remove temporary voice input file %s", temp_path, exc_info=True)
        text, confidence, metadata = _coerce_whisper_segments(segments)
        metadata.update({"provider": self.provider_name, "sample_rate": sample_rate})
        return TranscriptionResult(text=text, confidence=confidence, language=language or "", metadata=metadata)


class WakeWordGate:
    def __init__(self, wake_words: Iterable[str] | None = None, *, strip_wake_word: bool = True) -> None:
        self.wake_words = [normalize_transcript(item).lower() for item in (wake_words or []) if normalize_transcript(item)]
        self.strip_wake_word = strip_wake_word

    @property
    def enabled(self) -> bool:
        return bool(self.wake_words)

    def apply(self, transcript: str) -> tuple[bool, str, str]:
        normalized = normalize_transcript(transcript)
        if not self.enabled:
            return True, "", normalized

        haystack = normalized.lower()
        for wake_word in self.wake_words:
            index = haystack.find(wake_word)
            if index < 0:
                continue
            gated = normalized
            if self.strip_wake_word:
                gated = f"{normalized[:index]} {normalized[index + len(wake_word):]}".strip(" ,.:;")
                gated = normalize_transcript(gated)
            return True, wake_word, gated
        return False, "", normalized


SubmitCallback = Callable[[str, VoiceInputEvent], Any]


class ChatEndpointClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        mode: str = "efficiency",
        timeout: float = 30.0,
        chat_path: str = "/api/chat",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.timeout = timeout
        self.chat_path = "/" + chat_path.strip("/")

    async def submit(self, text: str, event: VoiceInputEvent) -> Any:  # noqa: ARG002
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}{self.chat_path}", json={"message": text, "mode": self.mode})
            response.raise_for_status()
            return response.json()


class VoiceInputProcessor:
    def __init__(
        self,
        *,
        transcriber: VoiceTranscriber | None = None,
        wake_words: Iterable[str] | None = None,
        auto_submit: bool = False,
        submit_callback: SubmitCallback | None = None,
        chat_client: Any | None = None,
        language: str | None = None,
        task_id: str = GLOBAL_TASK_ID,
        min_transcript_chars: int = 1,
    ) -> None:
        self.transcriber = transcriber or build_default_transcriber()
        self.wake_gate = WakeWordGate(wake_words)
        self.auto_submit = auto_submit
        self.submit_callback = submit_callback
        self.chat_client = chat_client
        self.language = language
        self.task_id = task_id
        self.min_transcript_chars = max(0, int(min_transcript_chars or 0))
        self._buffer: list[AudioChunk] = []
        self._lock = asyncio.Lock()

    async def process_chunk(self, chunk: AudioChunk | bytes, *, final: bool | None = None) -> VoiceInputEvent | None:
        audio_chunk = chunk if isinstance(chunk, AudioChunk) else AudioChunk(data=bytes(chunk))
        if final is not None:
            audio_chunk.is_final = bool(final)
        async with self._lock:
            self._buffer.append(audio_chunk)
            if not audio_chunk.is_final:
                return None
            chunks = self._buffer
            self._buffer = []

        return await self.process_utterance(chunks)

    async def process_utterance(self, chunks: Iterable[AudioChunk] | bytes) -> VoiceInputEvent | None:
        if isinstance(chunks, bytes):
            audio = chunks
            sample_rate = 16_000
            audio_metadata: dict[str, Any] = {"chunk_count": 1, "bytes": len(audio)}
        else:
            chunk_list = list(chunks)
            if not chunk_list:
                return None
            sample_rate = chunk_list[-1].sample_rate
            audio = b"".join(chunk.data for chunk in chunk_list)
            audio_metadata = {
                "chunk_count": len(chunk_list),
                "bytes": len(audio),
                "sample_rate": sample_rate,
                "channels": chunk_list[-1].channels,
            }
            for chunk in chunk_list:
                audio_metadata.update(dict(chunk.metadata or {}))

        result = await self._transcribe(audio, sample_rate=sample_rate)
        raw_text = normalize_transcript(result.text)
        allowed, wake_word, gated_text = self.wake_gate.apply(raw_text)
        if not allowed or len(gated_text) < self.min_transcript_chars:
            return None

        metadata = dict(result.metadata or {})
        event = VoiceInputEvent(
            task_id=self.task_id,
            transcript=gated_text,
            raw_transcript=raw_text,
            confidence=result.confidence,
            language=result.language,
            wake_word_detected=bool(wake_word),
            wake_word=wake_word,
            audio_metadata={**audio_metadata, **metadata},
            metadata={"provider": metadata.get("provider", type(self.transcriber).__name__)},
        )
        if self.auto_submit:
            event.submit_result = await self._submit(gated_text, event)
            event.auto_submitted = True
        return event

    async def _transcribe(self, audio: bytes, *, sample_rate: int) -> TranscriptionResult:
        result = self.transcriber.transcribe(audio, sample_rate=sample_rate, language=self.language)
        if inspect.isawaitable(result):
            result = await result
        return coerce_transcription_result(result)

    async def _submit(self, text: str, event: VoiceInputEvent) -> Any:
        if self.submit_callback is not None:
            result = self.submit_callback(text, event)
        elif self.chat_client is not None:
            submit = getattr(self.chat_client, "submit", None)
            if submit is None:
                raise TypeError("chat_client must expose submit(text, event)")
            result = submit(text, event)
        else:
            raise ValueError("auto_submit requires submit_callback or chat_client")
        if inspect.isawaitable(result):
            return await result
        return result


def build_default_transcriber(*, model_path: str = "base.en") -> VoiceTranscriber:
    try:
        transcriber = WhisperCppTranscriber(model_path)
        transcriber.model
        return transcriber
    except Exception as exc:
        logger.info("pywhispercpp unavailable; using deterministic voice fallback: %s", exc)
        return DeterministicFallbackTranscriber()


def coerce_transcription_result(result: str | TranscriptionResult | Any) -> TranscriptionResult:
    if isinstance(result, TranscriptionResult):
        return result
    if isinstance(result, str):
        return TranscriptionResult(text=result)
    if isinstance(result, dict):
        return TranscriptionResult(
            text=str(result.get("text") or ""),
            confidence=result.get("confidence"),
            language=str(result.get("language") or ""),
            metadata=dict(result.get("metadata") or {}),
        )
    return TranscriptionResult(text=str(getattr(result, "text", "") or result))


def normalize_transcript(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def pcm16_to_wav(audio: bytes, *, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)
    return buffer.getvalue()


def _decode_text_like_audio(audio: bytes) -> str:
    if not audio:
        return ""
    try:
        text = audio.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    printable = [ch for ch in text if ch.isprintable() or ch.isspace()]
    if len(printable) / max(1, len(text)) < 0.9:
        return ""
    return normalize_transcript("".join(printable))


def _coerce_whisper_segments(segments: Any) -> tuple[str, float | None, dict[str, Any]]:
    if isinstance(segments, str):
        return normalize_transcript(segments), None, {"segment_count": 1}
    if isinstance(segments, dict):
        text = str(segments.get("text") or "")
        confidence = segments.get("confidence")
        return normalize_transcript(text), confidence, {"segment_count": 1}

    items = list(segments or [])
    texts: list[str] = []
    confidences: list[float] = []
    for item in items:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        if text:
            texts.append(str(text))

        confidence = getattr(item, "confidence", None)
        if confidence is None and isinstance(item, dict):
            confidence = item.get("confidence")
        if confidence is not None:
            try:
                confidences.append(float(confidence))
            except (TypeError, ValueError):
                pass

    average_confidence = sum(confidences) / len(confidences) if confidences else None
    return normalize_transcript(" ".join(texts)), average_confidence, {"segment_count": len(items)}
