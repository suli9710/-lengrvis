"""Cross-mode context isolation at the real cloud provider sending boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agents.memory_agent import MemoryAgent
from app.config import AppSettings
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, MatterStatus, MemoryProposal
from app.core.schemas import Memory, Task
from app.core.session_context import get_session_context_store
from app.indexer import embedding_service
from app.llm import registry
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.services.butler_memory import save_proposals
from app.services.butler_service import ButlerCoordinator

PRIVATE_PREFERENCE = "本地偏好是蓝色宋体"
PRIVATE_FACT = "我负责青山项目"
PUBLIC_PREFERENCE = "普通偏好是表格"


@pytest.fixture
def send_boundary(monkeypatch, tmp_path):
    monkeypatch.setattr("app.core.outbound_url._system_resolve_outbound_ips", lambda host: ("93.184.216.34",))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    settings = AppSettings(
        mode="efficiency",
        provider_name="openai_compatible",
        base_url="https://example.com/v1",
        api_key="context-test-placeholder",  # noqa: S106 - inert test credential.
        allow_mock_fallback=False,
        allow_file_content_upload=False,
    )
    local = SimpleNamespace(kind="ollama", base_url="http://127.0.0.1:11434/v1", models=["test-local"])
    monkeypatch.setattr(registry, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(embedding_service, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(registry, "detect_local_backend", lambda: local)
    monkeypatch.setattr(registry, "detect_onnx_backend", lambda _: None)
    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda _: None)
    calls = []

    async def chat(provider, messages, output_schema):
        calls.append(("chat", provider.settings.base_url, json.dumps(messages, ensure_ascii=False)))
        return {"action": "ask", "reason": "need information", "missing_fields": ["format"]}

    async def embed(provider, texts, model=None):
        calls.append(("embed", provider.settings.base_url, json.dumps(texts, ensure_ascii=False)))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(OpenAICompatibleProvider, "structured_chat", chat)
    monkeypatch.setattr(OpenAICompatibleProvider, "embed", embed)
    db.init_db()
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["efficiency", "hybrid"])
@pytest.mark.parametrize("coordination", [True, False])
async def test_private_matters_and_memories_never_enter_cloud_planner(send_boundary, mode, coordination):
    coordinator = ButlerCoordinator()
    private = store.save(ButlerMatter(goal="PRIVATE_PAUSED_GOAL", mode="privacy", status=MatterStatus.PAUSED))
    completed = await coordinator.remember_preference(PRIVATE_PREFERENCE, "privacy")
    fact = store.save(ButlerMatter(goal=PRIVATE_FACT, mode="privacy", memory_inputs=[PRIVATE_FACT]))
    saved = await save_proposals(
        fact, [MemoryProposal(content=fact.goal, evidence=fact.goal, kind="fact", reason="context")]
    )
    assert saved.memory_ids
    for memory_id in saved.memory_ids:
        persisted = db.get_memory(memory_id)
        assert persisted["source_mode"] == "privacy" and not persisted["allow_cloud_context"]
    await coordinator.remember_preference(PUBLIC_PREFERENCE, "efficiency")
    public = store.save(ButlerMatter(goal="PUBLIC_RELATED_GOAL", mode="hybrid", status=MatterStatus.PAUSED))
    current = store.save(
        ButlerMatter(goal="PUBLIC_CURRENT_GOAL", mode=mode, depends_on=[private.id, completed["matter"]["id"]])
    )
    store.set_control(coordination_enabled=coordination)
    send_boundary.clear()
    await coordinator.decide(current)
    cloud = [body for _, url, body in send_boundary if url == "https://example.com/v1"]
    assert cloud and any("PUBLIC_CURRENT_GOAL" in body for body in cloud)
    assert any(PUBLIC_PREFERENCE in body for body in cloud)
    for marker in (private.goal, PRIVATE_PREFERENCE, fact.goal):
        assert all(marker not in body for body in cloud)
    assert any(public.goal in body for body in cloud) == coordination

    # Persisted local records still work for a later privacy planner.
    send_boundary.clear()
    await coordinator.decide(current.model_copy(update={"mode": "privacy"}))
    local = [body for operation, url, body in send_boundary if operation == "chat" and "127.0.0.1" in url]
    assert local and PRIVATE_PREFERENCE in local[0] and fact.goal in local[0]
    assert not any(url == "https://example.com/v1" for _, url, _ in send_boundary)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "tampered", "copied"])
async def test_missing_or_invalid_memory_source_policy_stays_local(send_boundary, policy):
    agent = MemoryAgent()
    memory = await agent.remember("LOCAL_ONLY_MARKER", mode="privacy", kind="preference", embedding=[1.0, 0.0])
    row = db.get_memory(memory.id)
    if policy == "legacy":
        for key in ("source_mode", "allow_cloud_context", "context_policy_hmac"):
            row.pop(key)
    else:
        row.update(source_mode="efficiency", allow_cloud_context=True)
        if policy == "copied":
            public = await agent.remember("PUBLIC_OTHER", mode="efficiency", embedding=[1.0, 0.0])
            row["context_policy_hmac"] = public.context_policy_hmac
    db.upsert_memory(row)
    agent.promote(memory.id)
    send_boundary.clear()
    await ButlerCoordinator().decide(ButlerMatter(goal="PUBLIC_QUERY", mode="hybrid"))
    assert all("LOCAL_ONLY_MARKER" not in body for _, url, body in send_boundary if "example.com" in url)
    assert memory.id in {item.id for item in await agent.recall("LOCAL_ONLY_MARKER", mode="privacy")}


@pytest.mark.asyncio
async def test_private_memory_policy_survives_superseding_promotion_and_global_changes(send_boundary):
    agent = MemoryAgent()
    task = Task(user_goal="private", mode="privacy")
    db.upsert_model("tasks", task)
    original = await agent.remember("PRIVATE_INITIAL", task_id=task.id, mode="efficiency", embedding=[1.0, 0.0])
    assert original.source_mode == "privacy" and not original.allow_cloud_context
    cloud_task = Task(user_goal="new version", mode="efficiency")
    db.upsert_model("tasks", cloud_task)
    send_boundary.clear()
    replacement = await agent.remember("PRIVATE_REPLACEMENT", task_id=cloud_task.id, supersedes=original.id)
    assert replacement.source_mode == "privacy" and not replacement.allow_cloud_context
    assert all("example.com" not in url for _, url, _ in send_boundary)
    assert agent.promote(replacement.id).allow_cloud_context is False
    assert await agent.recall("replacement", mode="hybrid") == []


@pytest.mark.asyncio
async def test_unattributed_session_preferences_cannot_bypass_memory_filter(send_boundary):
    now = datetime.now(UTC)
    session = get_session_context_store()
    session.learn_preference(
        "inferred",
        {
            "value": "PRIVATE_INFERENCE",
            "source": "local observation",
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
        },
    )
    await ButlerCoordinator().decide(ButlerMatter(goal="PUBLIC_QUERY", mode="efficiency"))
    assert all("PRIVATE_INFERENCE" not in body for _, url, body in send_boundary if "example.com" in url)


@pytest.mark.asyncio
async def test_automatic_revision_of_private_memory_prepares_embeddings_locally(send_boundary):
    agent = MemoryAgent()
    original = await agent.remember("报告使用蓝色宋体", mode="privacy", kind="preference", embedding=[1.0, 0.0])
    matter = store.save(
        ButlerMatter(goal="以后报告改用黑色宋体", mode="efficiency", memory_inputs=["以后报告改用黑色宋体"])
    )
    send_boundary.clear()
    saved = await save_proposals(
        matter,
        [
            MemoryProposal(
                content=matter.goal, evidence=matter.goal, kind="preference", reason="修订偏好", supersedes=original.id
            )
        ],
    )
    assert saved.memory_ids
    replacement = db.get_memory(saved.memory_ids[0])
    assert replacement["source_mode"] == "privacy" and not replacement["allow_cloud_context"]
    assert send_boundary and all("example.com" not in url for _, url, _ in send_boundary)


def test_legacy_memory_policy_defaults_are_conservative():
    memory = Memory(content="legacy")
    assert memory.source_mode == "" and not memory.allow_cloud_context and not memory.context_policy_hmac
