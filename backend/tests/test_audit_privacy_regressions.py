from __future__ import annotations

import asyncio
import os

import pytest

from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import Task
from app.indexer import embedding_service
from app.indexer.fts_index import FTSIndex
from app.llm import registry
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.policy.privacy import can_upload_file_content, can_use_browser_network
from app.tools import document_tools, file_tools


@pytest.fixture
def cloud_workspace(monkeypatch, tmp_path):
    for key in tuple(os.environ):
        if key.startswith("LENGRVIS_"):
            monkeypatch.delenv(key)
    for key, value in {
        "TEST": "1",
        "AUDIT_HMAC_SECRET": "privacy-regression-test-only",
        "ALLOW_INSECURE_LOCAL_SECRETS": "1",
        "DATA_DIR": str(tmp_path / "data"),
        "ENV_FILE": str(tmp_path / "absent.env"),
        "CONFIG_FILE": str(tmp_path / "absent.yaml"),
        "PLAN": "pro",
        "MODE": "efficiency",
        "BASE_URL": "https://8.8.8.8/v1",
        "API_KEY": "privacy-test-never-sent",
        "ALLOW_FILE_CONTENT_UPLOAD": "false",
        "ALLOW_CLOUD_CONTEXT": "false",
        "ALLOW_MOCK_FALLBACK": "false",
    }.items():
        monkeypatch.setenv("LENGRVIS_" + key, value)
    db.init_db()
    root = tmp_path / "documents"
    root.mkdir()
    path = root / "notes.txt"
    path.write_text("PRIVATE_DOCUMENT_REGRESSION_MARKER", encoding="utf-8")
    requests = []

    async def post(provider, endpoint, payload, **kwargs):
        requests.append({"base_url": provider.settings.base_url, "payload": payload})
        if "input" in payload:
            return {"data": [{"embedding": [1.0, 0.0]} for _ in payload["input"]]}
        return {"choices": [{"message": {"role": "assistant", "content": "Document summary"}}]}

    monkeypatch.setattr(OpenAICompatibleProvider, "_post_json", post)
    monkeypatch.setattr(registry, "detect_local_backend", lambda: None)
    monkeypatch.setattr(registry, "detect_onnx_backend", lambda settings: None)
    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: None)
    return path, requests


@pytest.mark.parametrize("operation", ["rebuild", "incremental"])
@pytest.mark.parametrize("local_failure", [False, True])
def test_index_never_uploads_file_text_without_consent(cloud_workspace, monkeypatch, operation, local_failure):
    path, requests = cloud_workspace
    if local_failure:

        class BrokenLocalEmbedding:
            async def embed(self, texts, model=None):
                raise RuntimeError("local embedding unavailable")

        monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: BrokenLocalEmbedding())
    index = FTSIndex()
    if operation == "rebuild":
        assert index.rebuild([str(path.parent)])["files_indexed"] == 1
    else:
        assert index.index_file(path, [str(path.parent)])
    assert requests == []
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_chunk_embeddings").fetchone()[0] > 0


def test_authorized_index_can_still_use_cloud_embeddings(cloud_workspace, monkeypatch):
    path, requests = cloud_workspace
    monkeypatch.setenv("LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD", "true")
    assert FTSIndex().index_file(path, [str(path.parent)])
    assert len(requests) == 1
    assert "PRIVATE_DOCUMENT_REGRESSION_MARKER" in str(requests[0]["payload"]["input"])


def test_task_privacy_overrides_global_tool_network_permissions(cloud_workspace, monkeypatch):
    monkeypatch.setenv("LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD", "true")
    monkeypatch.setenv("LENGRVIS_ALLOW_BROWSER_NETWORK", "true")
    global_settings = registry.get_effective_settings()
    orchestrator = OrchestratorAgent()
    runtime = orchestrator.step_execution_handler._runtime_context(Task(user_goal="read locally", mode="privacy"))
    assert not can_upload_file_content(runtime.settings).allowed
    assert not can_use_browser_network(runtime.settings).allowed
    assert global_settings.mode == "efficiency"
    assert can_upload_file_content(global_settings).allowed


def test_document_privacy_context_selects_its_local_model(cloud_workspace):
    path, requests = cloud_workspace
    settings = registry.get_effective_settings().model_copy(
        update={"mode": "privacy", "provider_name": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
    )
    result = document_tools.summarize(
        {"path": str(path)}, {"settings": settings, "allowed_directories": [str(path.parent)]}
    )
    assert result["summary"] == "Document summary"
    assert requests
    assert all(request["base_url"] == "http://127.0.0.1:11434/v1" for request in requests)


def test_document_privacy_without_local_model_does_not_fall_back_to_cloud(cloud_workspace):
    path, requests = cloud_workspace
    settings = registry.get_effective_settings().model_copy(update={"mode": "privacy"})
    result = document_tools.summarize(
        {"path": str(path)}, {"settings": settings, "allowed_directories": [str(path.parent)]}
    )
    assert result["summary"]
    assert requests == []


def test_private_task_memory_is_not_embedded_using_global_cloud(cloud_workspace, monkeypatch):
    _, requests = cloud_workspace
    monkeypatch.setenv("LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD", "true")
    task = Task(user_goal="remember privately", mode="privacy")
    db.upsert_model("tasks", task)
    memory = asyncio.run(MemoryAgent().remember("PRIVATE_MEMORY_MARKER", task_id=task.id))
    assert memory.embedding_dim > 0
    assert requests == []


def test_private_semantic_search_does_not_upload_its_query(cloud_workspace, monkeypatch):
    path, requests = cloud_workspace
    monkeypatch.setenv("LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD", "true")
    assert FTSIndex().index_file(path, [str(path.parent)])
    requests.clear()
    settings = registry.get_effective_settings().model_copy(update={"mode": "privacy"})
    result = file_tools.semantic_search(
        {"query": "PRIVATE_DOCUMENT_REGRESSION_MARKER"},
        {"allowed_directories": [str(path.parent)], "settings": settings},
    )
    assert result["count"] == 1
    assert requests == []
