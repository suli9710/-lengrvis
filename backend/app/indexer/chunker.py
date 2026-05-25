from __future__ import annotations

import re


def _split_long_unit(unit: str, size: int) -> list[str]:
    return [unit[index : index + size] for index in range(0, len(unit), size)]


def chunk_text(text: str, size: int = 1200, overlap: int = 120) -> list[str]:
    """Split text into stable chunks, preferring paragraph boundaries."""

    normalized = (text or "").strip()
    if not normalized:
        return []

    size = max(1, int(size))
    overlap = max(0, min(int(overlap), size - 1))
    units = [unit.strip() for unit in re.split(r"\n\s*\n|\r?\n", normalized) if unit.strip()]
    if not units:
        return []

    chunks: list[str] = []
    current = ""

    for unit in units:
        for piece in _split_long_unit(unit, size):
            if not current:
                current = piece
                continue

            separator = "\n\n"
            if len(current) + len(separator) + len(piece) <= size:
                current = f"{current}{separator}{piece}"
                continue

            chunks.append(current)
            tail = current[-overlap:].lstrip() if overlap else ""
            if tail and len(tail) + len(separator) + len(piece) <= size:
                current = f"{tail}{separator}{piece}"
            else:
                current = piece

    if current:
        chunks.append(current)
    return chunks
