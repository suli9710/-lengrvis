from __future__ import annotations

import asyncio
import time

import pytest

from app.acceleration import onnx_sessions
from app.acceleration.onnx_sessions import OnnxAccelerationUnavailable
from app.agents import memory_agent
from app.agents.memory_agent import MemoryAgent
from app.config import AppSettings
from app.core import db
from app.indexer import embedding_service, local_embedding_provider
from app.indexer.embedding_storage import cosine_similarity as vector_cosine_similarity
from app.indexer.local_embedding_provider import LocalEmbeddingBackend, LocalEmbeddingProvider


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


def test_embed_texts_falls_back_when_local_provider_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    local_completed = False

    class SlowLocalProvider:
        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:  # noqa: ARG002
            nonlocal local_completed
            await asyncio.sleep(1.0)
            local_completed = True
            return [[99.0] for _ in texts]

    class RegistryProvider:
        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:  # noqa: ARG002
            return [[float(len(text)), 7.0] for text in texts]

    monkeypatch.setattr(embedding_service, "get_effective_settings", lambda: AppSettings())
    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: SlowLocalProvider())
    monkeypatch.setattr(embedding_service, "get_provider", lambda settings, task="embed": RegistryProvider())

    started = time.monotonic()
    vectors = asyncio.run(embedding_service.embed_texts(["alpha"], timeout_seconds=0.01))

    assert vectors == [[5.0, 7.0]]
    assert local_completed is False
    assert time.monotonic() - started < 0.5


def test_embed_texts_hashes_when_registry_provider_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowRegistryProvider:
        async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:  # noqa: ARG002
            await asyncio.sleep(1.0)
            return [[99.0] for _ in texts]

    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: None)
    monkeypatch.setattr(embedding_service, "get_provider", lambda settings, task="embed": SlowRegistryProvider())

    started = time.monotonic()
    vectors = asyncio.run(embedding_service.embed_texts(["alpha", "beta"], timeout_seconds=0.01))

    assert len(vectors) == 2
    assert all(len(vector) == 64 for vector in vectors)
    assert time.monotonic() - started < 0.5


def test_embed_texts_sync_timeout_raises_from_running_event_loop() -> None:
    async def slow_embedder(texts: list[str]) -> list[list[float]]:
        await asyncio.sleep(1.0)
        return [[99.0] for _ in texts]

    async def invoke() -> list[list[float]]:
        return embedding_service.embed_texts_sync(["alpha"], embedder=slow_embedder, timeout_seconds=0.01)

    started = time.monotonic()

    with pytest.raises(TimeoutError):
        asyncio.run(invoke())
    assert time.monotonic() - started < 0.5


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


def test_local_embedding_health_redacts_runtime_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    model = tmp_path / "Users" / "Suli" / "private-models" / "embed-secret.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"placeholder")
    backend = LocalEmbeddingBackend(
        kind="onnx-cpu",
        model_path=str(model),
        execution_provider="CPUExecutionProvider",
        available_providers=["CPUExecutionProvider"],
    )

    def fail_create_session(_backend):
        raise OnnxAccelerationUnavailable(f"failed to load {model} with token=embedding-secret-1234567890")

    monkeypatch.setattr(local_embedding_provider, "create_inference_session", fail_create_session)

    health = LocalEmbeddingProvider(backend).health()

    assert health["available"] is False
    assert "failed to load" in health["error"]
    assert "embedding-secret-1234567890" not in health["error"]
    assert str(model) not in health["error"]
    assert "embed-secret.onnx" not in health["error"]


def test_local_embedding_smoke_redacts_provider_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    private_file = tmp_path / "Users" / "Suli" / "private-models" / "tokenizer-secret.json"

    class FailingProvider:
        def embed_sync(self, texts: list[str]) -> list[list[float]]:  # noqa: ARG002
            raise RuntimeError(f"tokenizer failed at {private_file} api_key=sk-local-embedding-secret")

    monkeypatch.setattr(local_embedding_provider, "get_local_embedding_provider", lambda settings: FailingProvider())

    result = local_embedding_provider.test_embedding(texts=["hello"])

    assert result["ok"] is False
    assert "tokenizer failed" in result["error"]
    assert "sk-local-embedding-secret" not in result["error"]
    assert str(private_file) not in result["error"]
    assert "tokenizer-secret.json" not in result["error"]


def test_onnx_runtime_package_snapshot_redacts_import_failures(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    private_file = tmp_path / "Users" / "Suli" / "private-runtime" / "runtime-secret.dll"

    def fail_import(_name: str):
        raise ImportError(f"unable to load {private_file} token=runtime-secret-1234567890")

    monkeypatch.setattr(onnx_sessions.importlib, "import_module", fail_import)

    status = onnx_sessions._module_status("onnxruntime")

    assert status["available"] is False
    assert "unable to load" in status["error"]
    assert "runtime-secret-1234567890" not in status["error"]
    assert str(private_file) not in status["error"]
    assert "runtime-secret.dll" not in status["error"]


def test_onnx_module_status_only_suppresses_optional_runtime_import_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    private_file = tmp_path / "Users" / "Suli" / "private-runtime" / "runtime-secret.dll"

    def fail_import(_name: str):
        raise OSError(f"unable to load {private_file} token=runtime-secret-1234567890")

    monkeypatch.setattr(onnx_sessions.importlib, "import_module", fail_import)
    status = onnx_sessions._module_status("onnxruntime")

    assert status["available"] is False
    assert "runtime-secret-1234567890" not in status["error"]
    assert str(private_file) not in status["error"]

    def import_bug(_name: str):
        raise AssertionError("module status bug")

    monkeypatch.setattr(onnx_sessions.importlib, "import_module", import_bug)
    with pytest.raises(AssertionError, match="module status bug"):
        onnx_sessions._module_status("onnxruntime")


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
