from __future__ import annotations

import json
from typing import Any


def preview_text(content: str, max_chars: int) -> str:
    head = max(1, max_chars // 2)
    tail = max(1, max_chars - head)
    return (
        f"{content[:head]}\n"
        f"[Old tool result content cleared: original {len(content)} chars, preview retained for context budget]\n"
        f"{content[-tail:]}"
    )


def content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("type") == "tool_result":
                    parts.append(content_text(item.get("content")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return json_text(content)


def single_line(text: str) -> str:
    return " ".join(str(text).split())


def json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)
