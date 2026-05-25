from __future__ import annotations

from app.core.schemas import ToolResult


def summarize_result(result: ToolResult) -> str:
    if not result.ok:
        return f"Tool failed: {result.error}"
    if result.observation:
        return result.observation
    if result.changed_paths:
        return f"Tool succeeded; changed paths: {', '.join(result.changed_paths)}"
    return "Tool succeeded without file changes."

