from __future__ import annotations

import asyncio
import concurrent.futures
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.indexer.chunker import chunk_text
from app.llm.mock_provider import MockProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_provider


DEFAULT_MAX_CHARS_TO_LLM = 30000
DEFAULT_CHUNK_CHARS = 1800
DEFAULT_CHUNK_OVERLAP = 180
DEFAULT_MAX_CHUNKS = 18
DEFAULT_QA_TOP_K = 4
SUMMARY_CHUNK_LIMIT = 2600
REPORT_CONTENT_LIMIT = 30000


@dataclass(frozen=True)
class RetrievedChunk:
    index: int
    text: str
    score: float = 0.0

    @property
    def citation(self) -> str:
        return f"[chunk {self.index + 1}]"


def _run_async(coro) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def _provider(task: str = "subagent"):
    try:
        return get_provider(task=task)
    except Exception:
        return None


def _is_mock_provider(provider: Any) -> bool:
    return isinstance(provider, MockProvider) or getattr(provider, "name", "") == "mock"


ProviderResolver = Callable[[str], Any]


def _call_chat(
    messages: list[dict[str, str]],
    *,
    task: str = "subagent",
    temperature: float = 0.2,
    provider_resolver: ProviderResolver | None = None,
) -> str | None:
    resolver = provider_resolver or _provider
    provider = resolver(task)
    if provider is None or _is_mock_provider(provider):
        return None
    try:
        result = _run_async(provider.chat(messages, temperature=temperature))
    except Exception:
        return None
    result_text = str(result or "").strip()
    return result_text or None


def _budgeted_text(text: str, max_chars: int = DEFAULT_MAX_CHARS_TO_LLM) -> str:
    return (text or "").strip()[: max(1, max_chars)]


def chunk_document(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_chunks: int | None = DEFAULT_MAX_CHUNKS,
    max_chars: int = DEFAULT_MAX_CHARS_TO_LLM,
) -> list[str]:
    budgeted = _budgeted_text(text, max_chars=max_chars)
    chunks = chunk_text(budgeted, size=chunk_chars, overlap=overlap)
    if max_chunks is not None:
        return chunks[: max(0, max_chunks)]
    return chunks


def _extractive_summary(text: str, *, max_chars: int = 900) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _extractive_report(title: str, content: str, *, max_chars: int = 6000) -> str:
    excerpt = _extractive_summary(content, max_chars=max_chars)
    return f"# {title}\n\n## Summary\n\n{excerpt}\n\n## Key Points\n\n- {excerpt[:240]}\n\n## Conclusion\n\nGenerated from available source material."


def _tokenize(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\w\u4e00-\u9fff]+", value or "", flags=re.UNICODE) if len(token) > 1]


def _lexical_score(query_tokens: set[str], chunk: str) -> float:
    if not query_tokens:
        return 0.0
    lowered = chunk.lower()
    score = sum(1.0 for token in query_tokens if token in lowered)
    score += min(len(chunk), 2000) / 2000 * 0.01
    return score


def rank_chunks(query: str, chunks: list[str], *, top_k: int = DEFAULT_QA_TOP_K) -> list[RetrievedChunk]:
    if not chunks:
        return []
    query_tokens = set(_tokenize(query))
    scored = [
        RetrievedChunk(index=index, text=chunk, score=_lexical_score(query_tokens, chunk))
        for index, chunk in enumerate(chunks)
    ]
    scored.sort(key=lambda item: (-item.score, item.index))
    return scored[: max(1, top_k)]


def _format_chunks(chunks: list[RetrievedChunk], *, max_chars: int = 9000) -> str:
    blocks: list[str] = []
    used = 0
    for chunk in chunks:
        header = f"{chunk.citation}\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        body = chunk.text[:remaining]
        blocks.append(f"{header}{body}")
        used += len(header) + len(body)
    return "\n\n---\n\n".join(blocks)


def summarize_text(
    text: str,
    *,
    path_label: str | None = None,
    max_chars_to_llm: int = DEFAULT_MAX_CHARS_TO_LLM,
    provider_resolver: ProviderResolver | None = None,
) -> dict[str, Any]:
    fallback = _extractive_summary(text)
    if not (text or "").strip() or text.lstrip().startswith("["):
        return {"summary": fallback, "note": "extractive_fallback"}

    chunks = chunk_document(text, max_chars=max_chars_to_llm)
    if not chunks:
        return {"summary": fallback, "note": "extractive_fallback"}

    if len(chunks) == 1:
        messages = [
            {
                "role": "system",
                "content": load_prompt("document_summary_single_system.md"),
            },
            {"role": "user", "content": render_prompt("document_summary_single_user.md", {"document": chunks[0]})},
        ]
        summary = _call_chat(messages, provider_resolver=provider_resolver)
        if not summary:
            return {"summary": fallback, "note": "extractive_fallback"}
        return {"summary": summary, "note": "llm_summary", "chunks_used": 1}

    partials: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        messages = [
            {
                "role": "system",
                "content": load_prompt("document_summary_chunk_system.md"),
            },
            {
                "role": "user",
                "content": render_prompt(
                    "document_summary_chunk_user.md",
                    {"index": index, "total": len(chunks), "chunk": chunk[:SUMMARY_CHUNK_LIMIT]},
                ),
            },
        ]
        partial = _call_chat(messages, provider_resolver=provider_resolver)
        if not partial:
            return {"summary": fallback, "note": "extractive_fallback", "chunks_used": len(chunks)}
        partials.append(f"Chunk {index}: {partial}")

    reduce_messages = [
        {
            "role": "system",
            "content": load_prompt("document_summary_reduce_system.md"),
        },
        {
            "role": "user",
            "content": render_prompt(
                "document_summary_reduce_user.md",
                {"document_name": path_label or "document", "summaries": "\n\n".join(partials)[:max_chars_to_llm]},
            ),
        },
    ]
    summary = _call_chat(reduce_messages, provider_resolver=provider_resolver)
    if not summary:
        return {"summary": fallback, "note": "extractive_fallback", "chunks_used": len(chunks)}
    return {"summary": summary, "note": "llm_summary", "chunks_used": len(chunks)}


def answer_question(
    text: str,
    question: str,
    *,
    path_label: str | None = None,
    max_chars_to_llm: int = DEFAULT_MAX_CHARS_TO_LLM,
    provider_resolver: ProviderResolver | None = None,
) -> dict[str, Any]:
    cleaned_question = (question or "").strip()
    fallback = _extractive_summary(text, max_chars=1100)
    if not cleaned_question:
        return {"question": cleaned_question, "answer": fallback, "note": "no_question", "citations": 0, "citation_labels": []}
    if not (text or "").strip() or text.lstrip().startswith("["):
        return {
            "question": cleaned_question,
            "answer": fallback,
            "note": "extractive_fallback",
            "citations": 0,
            "citation_labels": [],
        }

    chunks = chunk_document(text, max_chunks=None, max_chars=max_chars_to_llm)
    relevant = rank_chunks(cleaned_question, chunks)
    if not relevant:
        return {"question": cleaned_question, "answer": fallback, "note": "extractive_fallback", "citations": []}

    context_block = _format_chunks(relevant)
    messages = [
        {
            "role": "system",
            "content": load_prompt("document_qa_system.md"),
        },
        {
            "role": "user",
            "content": render_prompt(
                "document_qa_user.md",
                {
                    "document_name": path_label or "document",
                    "question": cleaned_question,
                    "source_chunks": context_block,
                },
            ),
        },
    ]
    answer = _call_chat(messages, provider_resolver=provider_resolver)
    if not answer:
        return {
            "question": cleaned_question,
            "answer": _fallback_qa_answer(cleaned_question, relevant),
            "note": "extractive_fallback",
            "citations": len(relevant),
            "citation_labels": [chunk.citation for chunk in relevant],
            "source_chunks": _source_chunk_payload(relevant),
        }
    return {
        "question": cleaned_question,
        "answer": answer,
        "note": "llm_qa",
        "citations": len(relevant),
        "citation_labels": [chunk.citation for chunk in relevant],
        "source_chunks": _source_chunk_payload(relevant),
    }


def _fallback_qa_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    excerpts = []
    for chunk in chunks[:2]:
        excerpt = _extractive_summary(chunk.text, max_chars=360)
        excerpts.append(f"{chunk.citation} {excerpt}")
    return f"Relevant source excerpts for '{question}':\n\n" + "\n\n".join(excerpts)


def _source_chunk_payload(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "index": chunk.index,
            "citation": chunk.citation,
            "score": round(chunk.score, 4) if math.isfinite(chunk.score) else 0,
            "text": chunk.text[:1200],
        }
        for chunk in chunks
    ]


def generate_report(
    content: str,
    *,
    title: str = "Report",
    max_chars_to_llm: int = DEFAULT_MAX_CHARS_TO_LLM,
    provider_resolver: ProviderResolver | None = None,
) -> dict[str, Any]:
    cleaned_title = (title or "Report").strip() or "Report"
    cleaned_content = (content or "").strip()
    if not cleaned_content:
        return {"report": f"# {cleaned_title}\n\nNo content provided."}

    fallback = _extractive_report(cleaned_title, cleaned_content)
    chunks = chunk_document(
        cleaned_content,
        chunk_chars=2400,
        overlap=200,
        max_chunks=14,
        max_chars=min(max_chars_to_llm, REPORT_CONTENT_LIMIT),
    )
    if not chunks:
        return {"report": fallback, "note": "extractive_fallback"}

    source = "\n\n---\n\n".join(f"[section {index + 1}]\n{chunk}" for index, chunk in enumerate(chunks))
    messages = [
        {
            "role": "system",
            "content": load_prompt("document_report_system.md"),
        },
        {
            "role": "user",
            "content": render_prompt(
                "document_report_user.md",
                {"title": cleaned_title, "source": source[:REPORT_CONTENT_LIMIT]},
            ),
        },
    ]
    report = _call_chat(messages, provider_resolver=provider_resolver)
    if not report:
        return {"report": fallback, "note": "extractive_fallback"}
    return {"report": report, "note": "llm_report", "chunks_used": len(chunks)}
