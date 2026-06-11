from __future__ import annotations

import re


def fts_match_query(query: str) -> str:
    """Format a user query for FTS5 trigram substring matching.

    FTS5 trigram ignores tokens shorter than three Unicode characters, so
    one- and two-character CJK terms in multi-token queries may not match.
    """
    cleaned = str(query or "").strip()
    if not cleaned:
        return '""'
    tokens = re.findall(r"\S+", cleaned, flags=re.UNICODE)
    if len(tokens) <= 1:
        return f'"{cleaned.replace(chr(34), chr(34) * 2)}"'
    parts = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:8]]
    return " OR ".join(parts)
