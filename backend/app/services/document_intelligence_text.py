from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Iterable
from typing import Any

from app.observability.best_effort import log_best_effort_failure
from app.services.document_intelligence_models import DocumentBlock, DocumentIR, DocumentTable

logger = logging.getLogger(__name__)


def _normalize_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, page in enumerate(pages or [], start=1):
        page_number = int(page.get("page") or index)
        normalized.append(
            {
                "page": page_number,
                "text": str(page.get("text") or ""),
                "metadata": dict(page.get("metadata") or {}),
            }
        )
    return normalized or [{"page": 1, "text": "", "metadata": {}}]


def _blocks_from_pages(pages: list[dict[str, Any]]) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    for page in pages:
        page_number = int(page.get("page") or 1)
        page_metadata = dict(page.get("metadata") or {})
        for part in _split_text_blocks(str(page.get("text") or "")):
            blocks.append(
                DocumentBlock(
                    id=f"block-{len(blocks) + 1}",
                    text=part,
                    kind=_guess_block_kind(part),
                    page=page_number,
                    index=len(blocks),
                    metadata=page_metadata,
                )
            )
    return blocks


def _split_text_blocks(text: str) -> list[str]:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" in cleaned:
        candidates = re.split(r"\n\s*\n+", cleaned)
    else:
        candidates = cleaned.splitlines()
    return [candidate.strip() for candidate in candidates if candidate.strip()]


def _guess_block_kind(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(("# ", "## ", "### ")):
        return "heading"
    if stripped.startswith(("- ", "* ", "1. ")):
        return "list"
    return "paragraph"


def _stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _headers_from_rows(rows: list[list[str]]) -> list[str]:
    return list(rows[0]) if rows else []


def _rows_to_text(rows: list[list[str]], *, title: str = "") -> str:
    lines = [f"# {title}"] if title else []
    lines.extend("\t".join(row) for row in rows)
    return "\n".join(line for line in lines if line)


def _tables_to_text(tables: Iterable[DocumentTable]) -> str:
    chunks = []
    for table in tables:
        chunks.append(_rows_to_text(table.rows, title=table.caption or table.id))
    return "\n\n".join(chunks)


def _rows_from_plain_table_text(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "\t" in stripped:
            rows.append([part.strip() for part in stripped.split("\t")])
        elif "|" in stripped:
            rows.append([part.strip() for part in stripped.strip("|").split("|")])
        else:
            rows.append([part.strip() for part in re.split(r"\s{2,}", stripped) if part.strip()])
    return [row for row in rows if row]


def _coerce_docling_tables(raw_tables: Iterable[Any]) -> list[DocumentTable]:
    tables: list[DocumentTable] = []
    for raw in raw_tables:
        rows: list[list[str]] = []
        export = getattr(raw, "export_to_dataframe", None)
        if callable(export):
            try:
                dataframe = export()
                rows = [list(map(_stringify_cell, dataframe.columns))]
                rows.extend([list(map(_stringify_cell, row)) for row in dataframe.values.tolist()])
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log_best_effort_failure(logger, "document_intelligence.docling_table", exc)
                rows = []
        if not rows:
            text = str(raw or "").strip()
            rows = _rows_from_plain_table_text(text)
        if rows:
            tables.append(
                DocumentTable(
                    id=f"table-{len(tables) + 1}",
                    rows=rows,
                    headers=_headers_from_rows(rows),
                    page=1,
                    metadata={"source": "docling"},
                )
            )
    return tables


def _paragraph_blocks(ir: DocumentIR) -> list[DocumentBlock]:
    return [block for block in ir.blocks if block.text.strip() and block.kind in {"paragraph", "heading", "list"}]


def _normalize_for_diff(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _blocks_are_changed_pair(left: str, right: str) -> bool:
    left_norm = _normalize_for_diff(left)
    right_norm = _normalize_for_diff(right)
    if not left_norm or not right_norm:
        return False
    left_tokens = _meaningful_diff_tokens(left_norm)
    right_tokens = _meaningful_diff_tokens(right_norm)
    if not left_tokens or not right_tokens:
        return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio() >= 0.75
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return overlap >= 0.45


def _meaningful_diff_tokens(text: str) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "be",
        "for",
        "in",
        "is",
        "of",
        "or",
        "the",
        "this",
        "to",
        "with",
    }
    return {token for token in re.findall(r"[\w\u4e00-\u9fff]+", text, flags=re.UNICODE) if token not in stop_words}
