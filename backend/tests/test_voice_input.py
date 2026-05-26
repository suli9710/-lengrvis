from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.orchestration.events import event_to_dict
from app.perception.schemas import PerceptionEvent
from app.perception.voice_input import (
    AudioChunk,
    DeterministicFallbackTranscriber,
    TranscriptionResult,
    VoiceInputEvent,
    VoiceInputProcessor,
    WakeWordGate,
    build_default_transcriber,
    pcm16_to_wav,
)


@dataclass(slots=True)
class FakeTranscriber:
    text: str
    confidence: float = 0.8

    def transcribe(self, audio: bytes, *, sample_rate: int = 16_000, language: str | None = None) -> TranscriptionResult:
        return TranscriptionResult(
            text=self.text,
            confidence=self.confidence,
            language=language or "en",
            metadata={"provider": "fake", "bytes": len(audio), "sample_rate": sample_rate},
        )


def test_voice_input_event_is_event_compatible():
    event = VoiceInputEvent(task_id="task_1", transcript="open the budget")

    assert isinstance(event, PerceptionEvent)
    assert event.event_type == "perception.voice_input"
    assert event.summary() == "Voice input: open the budget"

    serialized = event_to_dict(event)
    assert serialized["task_id"] == "task_1"
    assert serialized["structured_payload"]["transcript"] == "open the budget"


def test_deterministic_fallback_extracts_text_like_audio():
    transcriber = DeterministicFallbackTranscriber()

    result = transcriber.transcribe(b"  hello   mavris  ")

    assert result.text == "hello mavris"
    assert result.confidence == 1.0
    assert result.metadata["provider"] == "deterministic_fallback"
    assert result.metadata["sha256"]


def test_deterministic_fallback_does_not_hallucinate_binary_audio():
    result = DeterministicFallbackTranscriber().transcribe(bytes([0, 255, 1, 2, 3]))

    assert result.text == ""
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_processor_buffers_realtime_chunks_until_final():
    processor = VoiceInputProcessor(transcriber=FakeTranscriber("start task"), auto_submit=False)

    first = await processor.process_chunk(AudioChunk(b"one", sample_rate=8_000))
    event = await processor.process_chunk(AudioChunk(b"two", sample_rate=8_000, is_final=True))

    assert first is None
    assert event is not None
    assert event.transcript == "start task"
    assert event.audio_metadata["chunk_count"] == 2
    assert event.audio_metadata["bytes"] == 6
    assert event.audio_metadata["sample_rate"] == 8_000


@pytest.mark.asyncio
async def test_processor_applies_wake_word_gate_and_strips_phrase():
    processor = VoiceInputProcessor(
        transcriber=FakeTranscriber("hey mavris summarize this page"),
        wake_words=["hey mavris"],
    )

    event = await processor.process_utterance(b"text-like audio")

    assert event is not None
    assert event.wake_word_detected is True
    assert event.wake_word == "hey mavris"
    assert event.transcript == "summarize this page"
    assert event.raw_transcript == "hey mavris summarize this page"


@pytest.mark.asyncio
async def test_processor_suppresses_transcript_without_wake_word():
    processor = VoiceInputProcessor(
        transcriber=FakeTranscriber("summarize this page"),
        wake_words=["hey mavris"],
    )

    assert await processor.process_utterance(b"text-like audio") is None


@pytest.mark.asyncio
async def test_processor_auto_submits_through_injected_async_callback():
    calls: list[tuple[str, str]] = []

    async def submit(text: str, event: VoiceInputEvent):
        calls.append((text, event.id))
        return {"message": "accepted", "delegated": False}

    processor = VoiceInputProcessor(
        transcriber=FakeTranscriber("create a note"),
        auto_submit=True,
        submit_callback=submit,
        task_id="voice_task",
    )

    event = await processor.process_utterance(b"text-like audio")

    assert event is not None
    assert event.task_id == "voice_task"
    assert event.auto_submitted is True
    assert event.submit_result == {"message": "accepted", "delegated": False}
    assert calls == [("create a note", event.id)]


@pytest.mark.asyncio
async def test_processor_auto_submits_through_injected_client():
    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def submit(self, text: str, event: VoiceInputEvent):  # noqa: ARG002
            self.calls.append(text)
            return {"task_id": "task_1"}

    client = Client()
    processor = VoiceInputProcessor(
        transcriber=FakeTranscriber("run backup"),
        auto_submit=True,
        chat_client=client,
    )

    event = await processor.process_utterance(b"text-like audio")

    assert event is not None
    assert event.submit_result == {"task_id": "task_1"}
    assert client.calls == ["run backup"]


def test_wake_word_gate_can_be_disabled():
    allowed, wake_word, transcript = WakeWordGate().apply(" plain command ")

    assert allowed is True
    assert wake_word == ""
    assert transcript == "plain command"


def test_default_transcriber_falls_back_when_whisper_unavailable(monkeypatch):
    import app.perception.voice_input as voice_input

    class BrokenWhisper:
        def __init__(self, model_path: str = "base.en") -> None:  # noqa: ARG002
            raise ModuleNotFoundError("pywhispercpp")

    monkeypatch.setattr(voice_input, "WhisperCppTranscriber", BrokenWhisper)

    assert isinstance(build_default_transcriber(), DeterministicFallbackTranscriber)


def test_pcm16_to_wav_wraps_audio_bytes():
    wav = pcm16_to_wav(b"\x00\x00\x01\x00", sample_rate=16_000)

    assert wav.startswith(b"RIFF")
    assert b"WAVE" in wav[:16]
