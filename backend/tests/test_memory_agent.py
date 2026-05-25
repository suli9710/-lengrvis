"""Tests for P0-2 MemoryAgent: remember / recall / forget / tag-filter."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.memory_agent import MemoryAgent
from app.core import db


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def test_remember_persists_with_embedding():
    agent = MemoryAgent()
    memory = asyncio.run(agent.remember("用户偏好按月份归档发票", task_id="t-1", tags=["preference", "invoice"]))
    assert memory.content
    assert memory.embedding_dim >= 1
    assert "preference" in memory.tags

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


def test_recall_tag_filter_excludes_other_kinds():
    agent = MemoryAgent()
    asyncio.run(agent.remember("Tagged A", tags=["alpha"]))
    asyncio.run(agent.remember("Tagged B", tags=["beta"]))
    results = asyncio.run(agent.recall("Tagged", tags=["alpha"], k=10))
    assert all("alpha" in item.tags for item in results)
    assert len(results) == 1


def test_recall_without_memories_returns_empty():
    agent = MemoryAgent()
    results = asyncio.run(agent.recall("anything", k=5))
    assert results == []
