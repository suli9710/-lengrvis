from __future__ import annotations

import asyncio

import pytest

from app.agents import memory_agent
from app.agents.memory_agent import MemoryAgent
from app.core import db
from app.indexer import embedding_service
from app.indexer import local_embedding_provider
from app.indexer.vector_index import _cosine_similarity as vector_cosine_similarity
from app.config import AppSettings


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_EMBEDDING_ONNX_MODEL_PATH", raising=False)
    monkeypatch.delenv("LENGRVIS_EMBEDDING_ONNX_MODEL_PATH", raising=False)
    local_embedding_provider._CACHED_PROVIDER = None


def test_embed_texts_uses_local_onnx_provider_before_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    class LocalProvider:
        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
            return [[float(len(text)), 42.0] for text in texts]

    def fail_get_provider(*args, **kwargs):
        raise AssertionError("registry provider should not be used when local embedding is healthy")

    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: LocalProvider())
    monkeypatch.setattr(embedding_service, "get_provider", fail_get_provider)

    vectors = asyncio.run(embedding_service.embed_texts(["local"]))

    assert vectors == [[5.0, 42.0]]


def test_embed_texts_hashes_when_local_and_registry_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_get_provider(*args, **kwargs):
        raise RuntimeError("embedding provider unavailable")

    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: None)
    monkeypatch.setattr(embedding_service, "get_provider", fail_get_provider)

    vectors = asyncio.run(embedding_service.embed_texts(["alpha", "beta"]))

    assert len(vectors) == 2
    assert all(len(vector) == 64 for vector in vectors)
    assert vectors[0] == pytest.approx(asyncio.run(embedding_service.embed_texts(["alpha"]))[0])


def test_local_embedding_detection_gracefully_unavailable_without_model_path() -> None:
    assert local_embedding_provider.detect_local_embedding_backend() is None
    health = local_embedding_provider.health_snapshot()
    assert health["available"] is False
    assert "error" in health


def test_local_embedding_uses_dedicated_settings_fields(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    model = tmp_path / "embed" / "model.onnx"
    model.parent.mkdir()
    model.write_bytes(b"placeholder")
    settings = AppSettings(
        onnx_embedding_model_path=str(model.parent),
        onnx_embedding_execution_provider="cpu",
        onnx_embedding_model_id="test-embedder",
    )

    monkeypatch.setattr(local_embedding_provider, "_available_execution_providers", lambda: ["CPUExecutionProvider"])

    backend = local_embedding_provider.detect_local_embedding_backend(settings)

    assert backend is not None
    assert backend.model_path == str(model)
    assert backend.execution_provider == "CPUExecutionProvider"
    assert backend.model_id == "test-embedder"


def test_memory_agent_uses_embedding_service(monkeypatch: pytest.MonkeyPatch) -> None:
    db.init_db()
    calls: list[list[str]] = []

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[1.0, 2.0, 3.0] for _ in texts]

    monkeypatch.setattr(memory_agent, "embed_texts", fake_embed_texts)

    agent = MemoryAgent()
    memory = asyncio.run(agent.remember("remember through unified embedding service"))

    assert calls == [["remember through unified embedding service"]]
    assert memory.embedding_dim == 3
    stored = db.list_memories(limit=1)[0]
    assert stored["embedding"] == [1.0, 2.0, 3.0]


def test_cosine_similarity_returns_zero_for_dimension_mismatch() -> None:
    assert vector_cosine_similarity([1.0, 0.0], [1.0, 0.0, 99.0]) == 0.0
    assert memory_agent._cosine_similarity([1.0, 0.0], [1.0, 0.0, 99.0]) == 0.0
