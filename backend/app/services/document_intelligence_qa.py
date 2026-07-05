from __future__ import annotations

from typing import Any

from app.services import document_service
from app.services.document_intelligence_models import DocumentBlock, ProviderResolver


def _rank_blocks(query: str, blocks: list[DocumentBlock], *, top_k: int) -> list[DocumentBlock]:
    candidates = [block for block in blocks if block.text.strip()]
    if not candidates:
        return []
    ranked_chunks = document_service.rank_chunks(query, [block.text for block in candidates], top_k=max(1, top_k))
    return [candidates[item.index] for item in ranked_chunks]


def _format_cited_blocks(blocks: list[DocumentBlock], *, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for block in blocks:
        prefix = f"{block.citation}\n"
        remaining = max_chars - used - len(prefix)
        if remaining <= 0:
            break
        body = block.text[:remaining]
        parts.append(f"{prefix}{body}")
        used += len(prefix) + len(body)
    return "\n\n---\n\n".join(parts)


def _document_qa_messages(question: str, source_blocks: str) -> list[dict[str, str]]:
    return [
        dict(
            role="system",
            content=(
                "Answer the question using only the cited source blocks. "
                "Keep citations in square brackets next to supported claims."
            ),
        ),
        dict(
            role="user",
            content=f"Question: {question}\n\nSource blocks:\n{source_blocks}",
        ),
    ]


def _document_report_messages(title: str, source_blocks: str) -> list[dict[str, str]]:
    return [
        dict(
            role="system",
            content=(
                "Write a concise report grounded in the cited source blocks. "
                "Every factual bullet or paragraph must keep a citation."
            ),
        ),
        dict(
            role="user",
            content=f"Title: {title}\n\nSource blocks:\n{source_blocks}",
        ),
    ]


def _source_block_payload(block: DocumentBlock) -> dict[str, Any]:
    return {
        "id": block.id,
        "citation": block.citation,
        "kind": block.kind,
        "page": block.page,
        "index": block.index,
        "text": block.text[:1200],
        "metadata": dict(block.metadata),
    }


def _fallback_cited_answer(question: str, blocks: list[DocumentBlock]) -> str:
    excerpts = []
    for block in blocks[:2]:
        excerpt = " ".join(block.text.split())[:420]
        excerpts.append(f"{block.citation} {excerpt}")
    return f"Relevant source excerpts for '{question}':\n\n" + "\n\n".join(excerpts)


def _fallback_cited_report(title: str, blocks: list[DocumentBlock]) -> str:
    bullets = []
    for block in blocks:
        excerpt = " ".join(block.text.split())[:360]
        bullets.append(f"- {excerpt} {block.citation}")
    return f"# {title}\n\n## Source-Grounded Findings\n\n" + "\n".join(bullets)


def _call_chat(messages: list[dict[str, str]], *, provider_resolver: ProviderResolver | None) -> str | None:
    return document_service._call_chat(  # noqa: SLF001 - shared service helper keeps provider behavior consistent.
        messages,
        task="subagent",
        temperature=0.2,
        provider_resolver=provider_resolver,
    )
