"""Aggregate user-facing task artifacts (files a task produced or changed).

The desktop workspace view needs one place that answers "what did this task
actually produce?" instead of forcing the user to read the timeline text.
Artifacts are derived from stored tool results:

- ``ToolResult.changed_paths`` (writes/moves/deletes recorded by tools)
- path-like values inside ``ToolResult.output`` (generated reports, exports)

This endpoint is desktop/local only; it intentionally returns real local
paths so the desktop app can offer "open in folder" actions. Mobile/public
surfaces must keep using the redacted replay/timeline payloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core import db

# Output keys whose string values are treated as produced-artifact paths.
PATH_OUTPUT_KEYS = {
    "destination",
    "export_path",
    "file_path",
    "output_path",
    "report_path",
    "saved_to",
    "target_path",
}
MAX_OUTPUT_SCAN_DEPTH = 3
MAX_ARTIFACTS = 200


def collect_task_artifacts(task_id: str) -> dict[str, Any]:
    tool_calls = {
        str(call.get("id") or ""): call
        for call in db.fetch_many_by_fields("tool_calls", {"task_id": task_id}, limit=1000)
    }
    results = _tool_results_for_calls(list(tool_calls))

    artifacts: dict[str, dict[str, Any]] = {}
    for result in sorted(results, key=lambda item: str(item.get("created_at") or "")):
        call = tool_calls.get(str(result.get("tool_call_id") or ""), {})
        tool_name = str(call.get("tool_name") or "")
        step_id = str(call.get("step_id") or "")
        created_at = str(result.get("created_at") or "")
        if not result.get("ok", False):
            continue

        changed = result.get("changed_paths")
        if isinstance(changed, list):
            for raw in changed:
                _merge_artifact(
                    artifacts, raw, kind="changed", tool_name=tool_name, step_id=step_id, created_at=created_at
                )

        output = result.get("output")
        if isinstance(output, dict):
            for raw in _path_values(output):
                _merge_artifact(
                    artifacts, raw, kind="output", tool_name=tool_name, step_id=step_id, created_at=created_at
                )

        if len(artifacts) >= MAX_ARTIFACTS:
            break

    items = [_annotate_existence(item) for item in artifacts.values()]
    items.sort(key=lambda item: (item["created_at"], item["path"]))
    return {
        "task_id": task_id,
        "artifacts": items,
        "counts": {
            "total": len(items),
            "existing": sum(1 for item in items if item["exists"]),
            "missing": sum(1 for item in items if not item["exists"]),
            "changed": sum(1 for item in items if item["kind"] == "changed"),
            "generated": sum(1 for item in items if item["kind"] == "output"),
        },
    }


def _tool_results_for_calls(call_ids: list[str]) -> list[dict[str, Any]]:
    unique_ids = [call_id for call_id in dict.fromkeys(call_ids) if call_id]
    results: list[dict[str, Any]] = []
    for start in range(0, len(unique_ids), 400):
        chunk = unique_ids[start : start + 400]
        results.extend(db.fetch_many_in("tool_results", "tool_call_id", tuple(chunk), limit=len(chunk) * 4))
    return results


def _merge_artifact(
    artifacts: dict[str, dict[str, Any]],
    raw_path: Any,
    *,
    kind: str,
    tool_name: str,
    step_id: str,
    created_at: str,
) -> None:
    path = str(raw_path or "").strip()
    if not path or len(artifacts) >= MAX_ARTIFACTS:
        return
    key = path.casefold()
    existing = artifacts.get(key)
    if existing is None:
        artifacts[key] = {
            "path": path,
            "kind": kind,
            "tool_name": tool_name,
            "step_id": step_id,
            "created_at": created_at,
        }
        return
    # Keep the newest producer; prefer "output" (generated) over "changed".
    existing["created_at"] = max(str(existing.get("created_at") or ""), created_at)
    if tool_name:
        existing["tool_name"] = tool_name
    if step_id:
        existing["step_id"] = step_id
    if kind == "output":
        existing["kind"] = "output"


def _path_values(payload: dict[str, Any], depth: int = 0) -> list[str]:
    if depth > MAX_OUTPUT_SCAN_DEPTH:
        return []
    found: list[str] = []
    for key, value in payload.items():
        normalized_key = str(key).replace("-", "_").casefold()
        if normalized_key in PATH_OUTPUT_KEYS and isinstance(value, str) and _looks_like_local_path(value):
            found.append(value)
        elif isinstance(value, dict):
            found.extend(_path_values(value, depth + 1))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.extend(_path_values(item, depth + 1))
    return found


def _looks_like_local_path(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 500 or "\n" in text:
        return False
    if text.startswith(("http://", "https://", "ms-settings:", "data:")):
        return False
    try:
        return Path(text).is_absolute()
    except (OSError, ValueError):
        return False


def _annotate_existence(item: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(item)
    try:
        path = Path(str(item["path"]))
        stat = path.stat()
    except (OSError, ValueError):
        annotated["exists"] = False
        annotated["is_dir"] = False
        annotated["size_bytes"] = 0
        return annotated
    annotated["exists"] = True
    annotated["is_dir"] = path.is_dir()
    annotated["size_bytes"] = int(stat.st_size) if not path.is_dir() else 0
    return annotated
