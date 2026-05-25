"""T04: document summarize / QA / report generation use LLMs with stable fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.llm.mock_provider import MockProvider
from app.config import AppSettings
from app.services import document_service
from app.tools import document_tools


class _StubProvider:
    name = "stub"

    def __init__(self, replies: list[str] | None = None, raise_on_chat: bool = False) -> None:
        self.replies = replies or ["<<LLM RESPONSE>>"]
        self.raise_on_chat = raise_on_chat
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, model=None, temperature=None, tools=None) -> str:  # noqa: ANN001, ARG002
        self.calls.append(messages)
        if self.raise_on_chat:
            raise RuntimeError("provider unavailable")
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


@pytest.fixture
def sample_text(tmp_path: Path) -> Path:
    path = tmp_path / "contract.txt"
    path.write_text(
        "\n\n".join(
            [
                "Clause 1: Payment must be completed within 30 days after the contract takes effect.",
                "Clause 2: The breaching party must compensate actual losses caused by the breach.",
                "Clause 3: This agreement is governed by the laws of the People's Republic of China.",
            ]
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def context(tmp_path: Path) -> dict[str, Any]:
    return {"allowed_directories": [str(tmp_path)]}


def _inject_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(document_tools, "_provider", lambda task="subagent": provider)


def test_summarize_calls_llm_chat(monkeypatch, sample_text, context):
    stub = _StubProvider(replies=["The contract requires 30 day payment, breach compensation, and PRC law."])
    _inject_provider(monkeypatch, stub)

    result = document_tools.summarize({"path": str(sample_text)}, context)

    assert result["note"] == "llm_summary"
    assert "30 day payment" in result["summary"]
    assert stub.calls, "provider.chat must be invoked"
    assert "Payment must be completed" in stub.calls[0][-1]["content"]


def test_long_summarize_uses_chunked_map_reduce(monkeypatch, tmp_path: Path, context):
    path = tmp_path / "long-policy.txt"
    paragraphs = [
        f"Section {index}: Payment controls require approval level {index} and monthly reconciliation."
        for index in range(120)
    ]
    path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    stub = _StubProvider(replies=["partial summary", "final structured summary"])
    _inject_provider(monkeypatch, stub)

    result = document_tools.summarize({"path": str(path)}, context)

    assert result["note"] == "llm_summary"
    assert result["summary"] == "final structured summary"
    assert result["chunks_used"] > 1
    assert len(stub.calls) > 1
    assert "Chunk 1" in stub.calls[0][-1]["content"]


def test_summarize_falls_back_when_provider_raises(monkeypatch, sample_text, context):
    stub = _StubProvider(raise_on_chat=True)
    _inject_provider(monkeypatch, stub)

    result = document_tools.summarize({"path": str(sample_text)}, context)

    assert result["note"] == "extractive_fallback"
    assert "Payment must be completed" in result["summary"]


def test_summarize_falls_back_with_mock_provider(monkeypatch, sample_text, context):
    _inject_provider(monkeypatch, MockProvider())

    result = document_tools.summarize({"path": str(sample_text)}, context)

    assert result["note"] == "extractive_fallback"
    assert "Mock response" not in result["summary"]
    assert "Payment must be completed" in result["summary"]


def test_qa_calls_llm_with_relevant_chunks_and_citations(monkeypatch, sample_text, context):
    stub = _StubProvider(replies=["Payment is due within 30 days. [chunk 1]"])
    _inject_provider(monkeypatch, stub)

    result = document_tools.qa(
        {"path": str(sample_text), "question": "When is payment due?"},
        context,
    )

    assert result["note"] == "llm_qa"
    assert "30 days" in result["answer"]
    assert result["citations"] >= 1
    assert result["citation_labels"]
    assert result["source_chunks"][0]["text"]
    assert "Question: When is payment due?" in stub.calls[0][-1]["content"]
    assert "[chunk" in stub.calls[0][-1]["content"]


def test_qa_fallback_returns_ranked_source_chunks(monkeypatch, sample_text, context):
    stub = _StubProvider(raise_on_chat=True)
    _inject_provider(monkeypatch, stub)

    result = document_tools.qa(
        {"path": str(sample_text), "question": "Which law governs the agreement?"},
        context,
    )

    assert result["note"] == "extractive_fallback"
    assert "People's Republic of China" in result["answer"] or any(
        "People's Republic of China" in chunk["text"] for chunk in result["source_chunks"]
    )
    assert result["citation_labels"]


def test_qa_without_question_skips_llm(monkeypatch, sample_text, context):
    stub = _StubProvider()
    _inject_provider(monkeypatch, stub)

    result = document_tools.qa({"path": str(sample_text), "question": ""}, context)

    assert result["note"] == "no_question"
    assert not stub.calls, "no question must not trigger LLM"


def test_generate_report_calls_llm(monkeypatch, context):
    stub = _StubProvider(replies=["# Monthly Report\n\n## Executive Summary\nBusiness is stable."])
    _inject_provider(monkeypatch, stub)

    result = document_tools.generate_report(
        {"title": "Monthly Report", "content": "Revenue increased by 12% and churn remained flat."},
        context,
    )

    assert result["note"] == "llm_report"
    assert "## Executive Summary" in result["report"]
    assert stub.calls
    assert "Revenue increased by 12%" in stub.calls[0][-1]["content"]


def test_generate_report_falls_back_when_provider_raises(monkeypatch, context):
    stub = _StubProvider(raise_on_chat=True)
    _inject_provider(monkeypatch, stub)

    result = document_tools.generate_report(
        {"title": "Monthly Report", "content": "Revenue increased by 12% and churn remained flat."},
        context,
    )

    assert result["note"] == "extractive_fallback"
    assert "# Monthly Report" in result["report"]
    assert "Revenue increased by 12%" in result["report"]


def test_generate_report_empty_content_returns_placeholder(monkeypatch, context):
    stub = _StubProvider()
    _inject_provider(monkeypatch, stub)

    result = document_tools.generate_report({"title": "x", "content": ""}, context)

    assert "No content provided" in result["report"]
    assert not stub.calls


def test_chunk_text_splits_by_paragraph_size():
    chunks = document_tools._chunk_text("a" * 1000 + "\n\n" + "b" * 1000, chunk_chars=400)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_rank_chunks_prioritises_overlap():
    chunks = ["irrelevant text here", "this chunk has the answer keyword", "filler"]

    ranked = document_tools._rank_chunks("answer keyword", chunks)

    assert ranked[0] == "this chunk has the answer keyword"


def test_service_budget_limits_chunk_count():
    text = "\n\n".join(f"paragraph {index} " + ("x" * 200) for index in range(200))

    chunks = document_service.chunk_document(text, chunk_chars=500, overlap=50, max_chunks=5, max_chars=5000)

    assert len(chunks) == 5


def test_tool_respects_document_llm_budget(monkeypatch, tmp_path: Path):
    path = tmp_path / "budget.txt"
    path.write_text("A" * 5000, encoding="utf-8")
    context = {
        "allowed_directories": [str(tmp_path)],
        "settings": AppSettings(provider_name="mock", document_max_chars_to_llm=1200),
    }
    stub = _StubProvider(replies=["budgeted summary"])
    _inject_provider(monkeypatch, stub)

    result = document_tools.summarize({"path": str(path)}, context)

    assert result["note"] == "llm_summary"
    assert len(stub.calls[0][-1]["content"]) <= 1200
