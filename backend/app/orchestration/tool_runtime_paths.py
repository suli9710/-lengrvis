from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.audit import record
from app.core.errors import SecurityError
from app.core.paths import resolve_task_path
from app.orchestration.tool_runtime_support import _safe_runtime_error_text
from app.policy.policy_engine import BROWSER_WRITE_TOOLS
from app.policy.risk import is_modifying_or_higher
from app.tools.schemas import ToolDefinition

AUTHORIZED_PATH_ARG_KEYS = {
    "path",
    "paths",
    "source",
    "sources",
    "source_path",
    "source_paths",
    "destination",
    "destinations",
    "destination_path",
    "destination_paths",
    "dest",
    "dst",
    "target",
    "targets",
    "target_path",
    "target_paths",
    "target_folder",
    "target_folders",
    "folder",
    "folders",
    "directory",
    "directories",
    "dir",
    "dirs",
    "file",
    "files",
    "file_path",
    "file_paths",
    "input_path",
    "input_paths",
    "output_file",
    "output_files",
    "output_path",
    "output_paths",
    "output_zip",
    "root",
    "roots",
    "workspace_path",
    "working_directory",
}


def authorized_path_error(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> str:
    try:
        ensure_authorized_paths(tool, args, context)
    except SecurityError as exc:
        error = _safe_runtime_error_text(exc)
        record("tool.path_authorization_failed", "ToolRuntime", {"tool": tool.name, "error": error})
        return error
    return ""


def ensure_authorized_paths(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> None:
    if not tool.requires_authorized_path:
        return
    allowed_directories = [str(path) for path in context.get("allowed_directories") or []]
    explicit_scope = context.get("explicit_path_scope")
    explicit_scope_text = str(explicit_scope) if explicit_scope else None
    for arg_name, value in candidate_authorized_paths(args):
        try:
            resolve_task_path(
                value,
                allowed_directories,
                explicit_scope_text=explicit_scope_text,
            )
        except SecurityError as exc:
            raise SecurityError(f"{tool.name} path argument '{arg_name}' is not authorized: {exc}") from exc
        except OSError as exc:
            raise SecurityError(f"{tool.name} path argument '{arg_name}' could not be resolved: {exc}") from exc


def candidate_authorized_paths(args: dict[str, Any]) -> list[tuple[str, str | Path]]:
    candidates: list[tuple[str, str | Path]] = []
    collect_candidate_authorized_paths(args, "", candidates, top_level=True)
    return candidates


def collect_candidate_authorized_paths(
    value: Any,
    arg_name: str,
    candidates: list[tuple[str, str | Path]],
    *,
    top_level: bool,
) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_name = f"{arg_name}.{key}" if arg_name else key
            if is_authorized_path_arg_key(key, top_level=top_level):
                append_authorized_path_values(child, child_name, candidates)
            elif isinstance(child, dict | list | tuple | set):
                collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)
        return
    if isinstance(value, list | tuple | set):
        for index, child in enumerate(value):
            child_name = f"{arg_name}[{index}]" if arg_name else f"[{index}]"
            collect_candidate_authorized_paths(child, child_name, candidates, top_level=False)


def append_authorized_path_values(
    value: Any,
    arg_name: str,
    candidates: list[tuple[str, str | Path]],
) -> None:
    if isinstance(value, str | Path) and str(value).strip():
        candidates.append((arg_name, value))
        return
    if isinstance(value, list | tuple | set):
        for index, child in enumerate(value):
            child_name = f"{arg_name}[{index}]"
            append_authorized_path_values(child, child_name, candidates)
        return
    if isinstance(value, dict):
        collect_candidate_authorized_paths(value, arg_name, candidates, top_level=False)


def is_authorized_path_arg_key(key: str, *, top_level: bool) -> bool:
    normalized = key.replace("-", "_").casefold()
    return (
        normalized in AUTHORIZED_PATH_ARG_KEYS
        or normalized.endswith("_path")
        or normalized.endswith("_paths")
        or normalized.endswith("_directory")
        or normalized.endswith("_directories")
        or normalized.endswith("_folder")
        or normalized.endswith("_folders")
        or normalized.endswith("_dir")
        or normalized.endswith("_dirs")
        or normalized.endswith("_file")
        or normalized.endswith("_files")
        or (
            top_level
            and normalized in {"source", "sources", "destination", "destinations", "dest", "dst", "target", "targets"}
        )
    )


def write_lock_keys(tool: ToolDefinition, args: dict[str, Any]) -> list[str]:
    if not needs_completion_barrier(tool, args):
        return []

    keys: set[str] = set()
    if tool.concurrency_key:
        keys.add(f"tool:{tool.concurrency_key.casefold()}")
    for value in candidate_write_paths(args):
        path = normalize_lock_path(value)
        if not path:
            continue
        keys.add(path)
        parent = str(Path(path).parent)
        if parent and parent != path:
            keys.add(parent)
    if not keys and is_write_tool(tool):
        keys.add(f"tool:{tool.name.casefold()}")
    if not keys:
        keys.add(f"tool:{tool.name.casefold()}")
    return sorted(keys)


def needs_completion_barrier(tool: ToolDefinition, args: dict[str, Any]) -> bool:
    if tool.concurrency_key or is_write_tool(tool):
        return True
    return not tool.is_concurrency_safe(args)


def is_write_tool(tool: ToolDefinition) -> bool:
    risk = getattr(tool, "risk_level", None)
    # >= R2 (includes R4_FORBIDDEN_OR_HANDOFF, e.g. all MCP tools) so a
    # runtime backstop never treats a higher-risk tool as a non-write.
    if risk is not None and is_modifying_or_higher(risk):
        return True
    if getattr(tool, "supports_dry_run", False):
        return True
    name = getattr(tool, "name", "")
    return name in BROWSER_WRITE_TOOLS or any(
        token in name for token in (".copy", ".move", ".rename", ".trash", ".write", ".create", ".delete", ".uninstall")
    )


def candidate_write_paths(args: dict[str, Any]) -> list[Any]:
    result: list[Any] = []
    for key in (
        "path",
        "source",
        "destination",
        "target",
        "target_path",
        "target_folder",
        "folder",
        "directory",
        "output_path",
    ):
        value = args.get(key)
        if value:
            result.append(value)
    return result


def normalize_lock_path(value: Any) -> str:
    if not isinstance(value, str | Path):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False)).casefold()
    except OSError:
        return text.casefold()
