"""Voice input productization (plan: phase2-onboarding).

/api/perception/voice/* must report transcriber availability honestly and
transcribe base64 PCM16 payloads through VoiceInputProcessor without ever
requiring pywhispercpp at import time.
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.api import routes_perception
from app.core import db
from app.main import create_app


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    routes_perception.reset_voice_processor_for_tests()
    return TestClient(create_app())


def test_voice_health_reports_fallback_when_whisper_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(routes_perception.WhisperCppTranscriber, "available", classmethod(lambda cls: False))

    response = client.get("/api/perception/voice/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["provider"] == "deterministic_fallback"
    assert body["detail"]


def test_voice_health_reports_whisper_when_available(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(routes_perception.WhisperCppTranscriber, "available", classmethod(lambda cls: True))

    response = client.get("/api/perception/voice/health")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["provider"] == "pywhispercpp"


def test_voice_transcribe_round_trip_with_fallback_transcriber(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    # The deterministic fallback decodes text-like buffers; binary audio
    # would require pywhispercpp which is not present in CI.
    audio = "打开记事本".encode()
    payload = {"audio_base64": base64.b64encode(audio).decode("ascii"), "sample_rate": 16000}

    response = client.post("/api/perception/voice/transcribe", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "打开记事本"


def test_voice_transcribe_rejects_bad_payloads(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    not_base64 = client.post("/api/perception/voice/transcribe", json={"audio_base64": "@@not-base64@@"})
    assert not_base64.status_code == 400

    empty = client.post(
        "/api/perception/voice/transcribe", json={"audio_base64": base64.b64encode(b"").decode("ascii")}
    )
    assert empty.status_code in {400, 422}

    too_large = client.post(
        "/api/perception/voice/transcribe",
        json={
            "audio_base64": base64.b64encode(b"\x00" * (routes_perception.MAX_VOICE_AUDIO_BYTES + 2)).decode("ascii")
        },
    )
    assert too_large.status_code == 413


def test_resample_pcm16_shrinks_higher_sample_rates():
    audio = b"".join(int(value).to_bytes(2, "little", signed=True) for value in range(0, 480))
    resampled = routes_perception._resample_pcm16(audio, 48_000)
    # 48 kHz -> 16 kHz should keep roughly one third of the samples.
    assert abs(len(resampled) - len(audio) // 3) <= 2
    assert routes_perception._resample_pcm16(audio, 16_000) == audio
