"""Tests for P0-2 MemoryAgent: remember / recall / forget / tag-filter."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.agents import memory_agent as memory_agent_module
from app.agents.memory_agent import MemoryAgent
from app.core import db
from app.core.content_provenance import create_content_envelope
from app.core.schemas import MemoryConflictStatus, MemoryState


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[float(len(text) or 1), 1.0] for text in texts]

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_agent_module, "embed_texts", fake_embed_texts)
    db.init_db()
    yield


def test_remember_persists_with_embedding():
    agent = MemoryAgent()
    memory = asyncio.run(agent.remember("用户偏好按月份归档发票", task_id="t-1", tags=["preference", "invoice"]))
    assert memory.content
    assert memory.embedding_dim >= 1
    assert "preference" in memory.tags
    assert memory.state == MemoryState.ACTIVE
    assert memory.user_confirmed is True
    assert memory.content_envelope is not None
    assert memory.content_envelope.source_kind == "user_input"

    all_memories = agent.list_all()
    assert any(item.id == memory.id for item in all_memories)


def test_recall_returns_top_k_by_similarity():
    agent = MemoryAgent()
    asyncio.run(agent.remember("用户喜欢把发票按月份整理到 D:/Invoices"))
    asyncio.run(agent.remember("用户不希望被云端模型读取本地照片"))
    asyncio.run(agent.remember("最近一次任务把合同移到了 D:/Contracts"))

    results = asyncio.run(agent.recall("发票归档偏好", k=2))
    assert len(results) == 2
    # The invoice memory should be in the recall set.
    contents = " ".join(item.content for item in results)
    assert "发票" in contents


def test_forget_removes_record():
    agent = MemoryAgent()
    memory = asyncio.run(agent.remember("临时记录"))
    assert agent.forget(memory.id) is True
    assert agent.forget(memory.id) is False  # second call no-op


def test_recall_and_record_management_are_namespace_scoped() -> None:
    finance = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="finance")
    legal = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="legal")
    other_user = MemoryAgent(principal_id="bob", workspace_id="northwind", domain_scope="finance")
    inferred = asyncio.run(
        finance.remember(
            "Invoices require user review before filing",
            source="PlannerAgent",
            user_confirmed=False,
        )
    )
    asyncio.run(legal.remember("Legal invoices are retained for seven years"))
    asyncio.run(other_user.remember("Bob files invoices every Friday"))

    assert asyncio.run(finance.recall("invoices", k=10)) == []
    assert legal.promote(inferred.id) is None
    assert other_user.revoke(inferred.id) is None
    assert other_user.forget(inferred.id) is False

    promoted = finance.promote(inferred.id)
    assert promoted is not None
    assert promoted.principal_id == "alice"
    assert promoted.workspace_id == "northwind"
    assert promoted.domain_scope == "finance"
    assert [item.id for item in asyncio.run(finance.recall("invoices", k=10))] == [inferred.id]
    assert inferred.id not in {item.id for item in asyncio.run(legal.recall("invoices", k=10))}
    assert inferred.id not in {item.id for item in asyncio.run(other_user.recall("invoices", k=10))}

    revoked = finance.revoke(inferred.id)
    assert revoked is not None and revoked.state == MemoryState.REVOKED
    assert finance.forget(inferred.id) is True
    assert db.get_memory(
        inferred.id,
        principal_id="alice",
        workspace_id="northwind",
        domain_scope="finance",
    ) is None


def test_version_lineage_supersedes_stale_memory_and_blocks_cross_namespace_parent() -> None:
    agent = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="finance")
    base = asyncio.run(agent.remember("File invoices in the legacy folder", kind="preference"))
    successor = asyncio.run(
        agent.remember(
            "File invoices in the current-year folder",
            kind="preference",
            supersedes=base.id,
        )
    )

    assert successor.version == 2
    assert successor.supersedes == base.id
    stored_base = db.get_memory(
        base.id,
        principal_id="alice",
        workspace_id="northwind",
        domain_scope="finance",
    )
    assert stored_base is not None
    assert stored_base["conflict_status"] == MemoryConflictStatus.SUPERSEDED.value
    assert [item.id for item in asyncio.run(agent.recall("file invoices", k=10))] == [successor.id]

    other_domain = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="legal")
    with pytest.raises(ValueError, match="current namespace"):
        asyncio.run(
            other_domain.remember(
                "Try to replace a finance preference",
                kind="preference",
                supersedes=base.id,
            )
        )


def test_version_lineage_allows_only_one_active_successor() -> None:
    agent = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="finance")
    base = asyncio.run(agent.remember("File invoices in the legacy folder", kind="preference"))
    active = asyncio.run(
        agent.remember(
            "File invoices in the current-year folder",
            kind="preference",
            supersedes=base.id,
        )
    )

    with pytest.raises(ValueError, match="already has an active successor"):
        asyncio.run(
            agent.remember(
                "File invoices in another active folder",
                kind="preference",
                supersedes=base.id,
            )
        )

    quarantined = asyncio.run(
        agent.remember(
            "Candidate replacement awaiting review",
            kind="preference",
            supersedes=base.id,
            source="PlannerAgent",
            user_confirmed=False,
        )
    )
    with pytest.raises(ValueError, match="already has an active successor"):
        agent.promote(quarantined.id)

    assert agent.revoke(active.id) is not None
    promoted = agent.promote(quarantined.id)
    assert promoted is not None and promoted.state == MemoryState.ACTIVE
    with db.connect() as conn:
        row = conn.execute(
            "SELECT successor_memory_id FROM memory_active_successors WHERE parent_memory_id = ?",
            (base.id,),
        ).fetchone()
    assert row is not None and row["successor_memory_id"] == quarantined.id

    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="parent_memory_id"):
            conn.execute(
                """
                INSERT INTO memory_active_successors (parent_memory_id, successor_memory_id)
                VALUES (?, ?)
                """,
                (base.id, active.id),
            )


def test_conflicting_memory_requires_explicit_resolution_before_recall() -> None:
    agent = MemoryAgent(principal_id="alice", workspace_id="northwind", domain_scope="finance")
    memory = asyncio.run(
        agent.remember(
            "Invoices may belong in the archive folder",
            source="PlannerAgent",
            user_confirmed=False,
            conflict_status=MemoryConflictStatus.CONFLICTING,
        )
    )

    assert agent.promote(memory.id) is not None
    assert asyncio.run(agent.recall("invoices archive")) == []

    resolved = agent.promote(memory.id, conflict_status=MemoryConflictStatus.RESOLVED)
    assert resolved is not None
    assert resolved.conflict_status == MemoryConflictStatus.RESOLVED
    assert [item.id for item in asyncio.run(agent.recall("invoices archive"))] == [memory.id]


def test_recall_tag_filter_excludes_other_kinds():
    agent = MemoryAgent()
    asyncio.run(agent.remember("Tagged A", tags=["alpha"]))
    asyncio.run(agent.remember("Tagged B", tags=["beta"]))
    results = asyncio.run(agent.recall("Tagged", tags=["alpha"], k=10))
    assert all("alpha" in item.tags for item in results)
    assert len(results) == 1


def test_recall_without_memories_returns_empty(monkeypatch):
    embed_calls = []

    async def fake_embed_texts(texts):
        embed_calls.append(list(texts))
        return [[1.0]]

    monkeypatch.setattr(memory_agent_module, "embed_texts", fake_embed_texts)
    agent = MemoryAgent()
    results = asyncio.run(agent.recall("anything", k=5))
    assert results == []
    assert embed_calls == []


def test_system_memory_is_quarantined_until_user_promotes_it() -> None:
    agent = MemoryAgent()
    memory = asyncio.run(
        agent.remember(
            "Automatically inferred filing rule",
            source="OrchestratorAgent",
            user_confirmed=False,
        )
    )

    assert memory.state == MemoryState.QUARANTINED
    assert memory.content_envelope is not None
    assert "unreviewed_memory" in memory.content_envelope.taint_flags
    assert asyncio.run(agent.recall("filing rule")) == []

    promoted = agent.promote(memory.id)
    assert promoted is not None
    assert promoted.state == MemoryState.ACTIVE
    assert promoted.user_confirmed is True
    assert asyncio.run(agent.recall("filing rule"))[0].id == memory.id

    revoked = agent.revoke(memory.id)
    assert revoked is not None
    assert revoked.state == MemoryState.REVOKED
    assert asyncio.run(agent.recall("filing rule")) == []


@pytest.mark.parametrize("tamper_kind", ["content", "hmac", "scope", "confirmation"])
def test_recall_quarantines_memory_when_provenance_integrity_fails(tamper_kind: str) -> None:
    agent = MemoryAgent()
    memory = asyncio.run(agent.remember("Trusted filing preference", task_id="task-memory-integrity"))
    row = db.get_memory(memory.id)
    assert row is not None

    if tamper_kind == "content":
        row["content"] = "Tampered filing preference"
    elif tamper_kind == "hmac":
        row["content_envelope"]["integrity_hmac"] = "0" * 64
    elif tamper_kind == "scope":
        row["content_envelope"] = create_content_envelope(
            row["content"],
            source_kind="user_input",
            source_id="other-task",
            trust_level="user_confirmed",
            task_scope="other-task",
            user_confirmed=True,
        ).model_dump(mode="json")
    else:
        row["content_envelope"] = create_content_envelope(
            row["content"],
            source_kind="user_input",
            source_id=memory.task_id,
            trust_level="unknown",
            task_scope=memory.task_id,
            user_confirmed=False,
        ).model_dump(mode="json")
    db.upsert_memory(row)

    assert asyncio.run(agent.recall("filing preference")) == []
    stored = db.get_memory(memory.id)
    assert stored is not None
    assert stored["state"] == MemoryState.QUARANTINED.value
    assert stored["user_confirmed"] is False
    events = db.fetch_many("audit_events", limit=20)
    assert any(event["event_type"] == "memory.recall_integrity_failed" for event in events)
