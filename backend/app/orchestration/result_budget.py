from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import AppSettings
from app.core.schemas import ToolResult
from app.orchestration.runtime_context import LargeResultReference, TaskRuntimeContext


DEFAULT_PREVIEW_CHARS = 2000


def apply_result_budget(
    result: ToolResult,
    *,
    tool_name: str,
    max_result_size: int,
    runtime: TaskRuntimeContext,
) -> ToolResult:
    if max_result_size <= 0:
        return result
    content = json.dumps(result.output, ensure_ascii=False, default=str)
    if len(content) <= max_result_size:
        return result

    reference = persist_large_result(
        runtime.settings,
        runtime.task.id,
        result.id,
        tool_name,
        content,
    )
    runtime.remember_large_result(result.id, reference)
    result.output = {
        "persisted_result": True,
        "path": reference.path,
        "original_size": reference.original_size,
        "preview": reference.preview,
        "has_more": reference.has_more,
    }
    if result.observation:
        result.observation = f"{result.observation} Large output persisted to {reference.path}."
    else:
        result.observation = f"Large output persisted to {reference.path}."
    return result


def persist_large_result(
    settings: AppSettings,
    task_id: str,
    result_id: str,
    tool_name: str,
    content: str,
) -> LargeResultReference:
    directory = Path(settings.data_dir) / "tasks" / task_id / "tool-results"
    directory.mkdir(parents=True, exist_ok=True)
    safe_tool = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in tool_name)[:80] or "tool"
    path = directory / f"{result_id}_{safe_tool}.json"
    path.write_text(content, encoding="utf-8")
    preview = content[:DEFAULT_PREVIEW_CHARS]
    return LargeResultReference(
        path=str(path),
        original_size=len(content),
        preview=preview,
        has_more=len(content) > len(preview),
    )
