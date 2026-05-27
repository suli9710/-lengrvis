from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.core.paths import resolve_authorized


READ_STATE_TTL_SECONDS = 30 * 60
PATH_ARG_KEYS = {
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
    "cwd",
}
PREVIEW_PATH_KEYS = {
    "path",
    "paths",
    "from",
    "to",
    "source",
    "destination",
    "target",
    "target_path",
    "target_folder",
}
READ_OUTPUT_PATH_KEYS = {
    "path",
    "paths",
    "file",
    "files",
    "source",
    "sources",
    "root",
    "cwd",
}

_TASK_READ_STATES: dict[str, dict[str, dict[str, Any]]] = {}


class ResourceStateError(RuntimeError):
    def __init__(self, message: str, *, error_code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}

    def to_output(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "error_code": self.error_code,
            "resource_state_error": True,
            **self.details,
        }


class StaleResourceStateError(ResourceStateError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, error_code="STALE_RESOURCE_STATE", details=details)


class ReadBeforeWriteError(ResourceStateError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, error_code="READ_STATE_REQUIRED", details=details)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resource_state(path: Path | str) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve(strict=False)
    state: dict[str, Any] = {
        "kind": "file_resource_state",
        "path": str(resolved),
        "normalized_path": normalize_path_key(resolved),
        "exists": resolved.exists(),
    }
    if not resolved.exists():
        return state
    stat = resolved.stat()
    state.update(
        {
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "inode": getattr(stat, "st_ino", 0),
            "sha256": sha256_file(resolved) if resolved.is_file() else "",
        }
    )
    return state


def resource_states(paths: list[Path | str] | tuple[Path | str, ...]) -> list[dict[str, Any]]:
    return canonical_state_list([resource_state(path) for path in paths])


def capture_tool_resource_state(
    tool: Any,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    preview: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    paths = candidate_resource_paths(tool, args, context, preview=preview)
    return resource_states(paths)


def attach_dry_run_resource_state(
    output: dict[str, Any],
    tool: Any,
    args: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if args.get("dry_run") is not True or not _is_modifying_tool(tool):
        return output
    if output.get("_resource_state"):
        output["_resource_state"] = canonical_state_list(output["_resource_state"])
        return output
    states = capture_tool_resource_state(tool, args, context, preview=output)
    if states:
        output["_resource_state"] = states
    return output


def candidate_resource_paths(
    tool: Any,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    preview: dict[str, Any] | None = None,
) -> list[Path]:
    values: list[Any] = []
    _collect_path_values(args, "", values, top_level=True, path_keys=PATH_ARG_KEYS)
    if preview:
        _collect_preview_path_values(preview, values)
    values.extend(_tool_specific_path_values(str(getattr(tool, "name", "")), args, context))

    allowed = _allowed_directories(context)
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = _resolve_candidate_path(value, allowed)
        if path is None:
            continue
        key = normalize_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def remember_read_states_for_tool(
    tool: Any,
    args: dict[str, Any],
    output: dict[str, Any],
    context: dict[str, Any],
) -> None:
    if args.get("dry_run") is True or not _is_read_tool(tool):
        return
    runtime = context.get("runtime")
    states = _states_from_output(output)
    if not states:
        paths = read_result_paths(args, output, context)
        states = resource_states(paths)
    if not states:
        return

    task_id = _task_id(context, runtime)
    now = time.time()
    for state in states:
        if not state.get("path"):
            continue
        key = normalize_path_key(str(state["path"]))
        cached = {"state": state, "read_at": now, "tool_name": str(getattr(tool, "name", ""))}
        if task_id:
            _TASK_READ_STATES.setdefault(task_id, {})[key] = cached
        if runtime is not None:
            try:
                runtime.remember_file(str(state["path"]), partial_view=False, size=int(state.get("size") or 0))
                runtime.extra_context.setdefault("_resource_read_states", {})[key] = cached
            except Exception:
                pass


def validate_write_preconditions(
    *,
    tool: Any,
    args: dict[str, Any],
    context: dict[str, Any],
    current_state: list[dict[str, Any]],
    expected_approval_state: Any | None = None,
) -> None:
    if args.get("dry_run") is True or not _is_modifying_tool(tool):
        return
    expected_states = canonical_state_list(expected_approval_state or [])
    if expected_states:
        mismatch = compare_resource_states(expected_states, current_state)
        if mismatch:
            raise StaleResourceStateError(
                "Resource state changed after preview; run a fresh preview before writing.",
                details={"resource_state_mismatch": mismatch, "replan_recommended": True},
            )

    existing_file_states = [state for state in current_state if state.get("exists") and state.get("is_file")]
    if not existing_file_states:
        return
    approval_by_path = _states_by_path(expected_states)
    missing_read: list[dict[str, Any]] = []
    stale_read: list[dict[str, Any]] = []
    for state in existing_file_states:
        key = state.get("normalized_path") or normalize_path_key(str(state.get("path") or ""))
        approved_state = approval_by_path.get(key)
        if approved_state and compare_single_resource_state(approved_state, state) == {}:
            continue
        read_state = _read_state_for_path(key, context)
        if read_state is None:
            missing_read.append(_public_state_ref(state))
            continue
        if not _read_state_is_recent(read_state):
            missing_read.append(_public_state_ref(state))
            continue
        mismatch = compare_single_resource_state(read_state["state"], state)
        if mismatch:
            stale_read.append({"path": state.get("path"), "mismatch": mismatch})
    if stale_read:
        raise StaleResourceStateError(
            "File changed since it was read; read it again before writing.",
            details={"stale_read_state": stale_read, "replan_recommended": True},
        )
    if missing_read:
        raise ReadBeforeWriteError(
            "Existing files must be read or preview-approved before writing.",
            details={"missing_read_state": missing_read, "replan_recommended": True},
        )


def compare_resource_states(expected: Any, current: Any) -> dict[str, Any]:
    expected_list = canonical_state_list(expected or [])
    current_list = canonical_state_list(current or [])
    if expected_list == current_list:
        return {}
    expected_by_path = _states_by_path(expected_list)
    current_by_path = _states_by_path(current_list)
    if expected_by_path and current_by_path:
        missing = sorted(set(expected_by_path) - set(current_by_path))
        extra = sorted(set(current_by_path) - set(expected_by_path))
        changed = []
        for key in sorted(set(expected_by_path) & set(current_by_path)):
            mismatch = compare_single_resource_state(expected_by_path[key], current_by_path[key])
            if mismatch:
                changed.append({"path": current_by_path[key].get("path") or expected_by_path[key].get("path"), "diff": mismatch})
        if not missing and not extra and not changed:
            return {}
        return {"missing": missing, "extra": extra, "changed": changed}
    return {"expected": expected_list, "current": current_list}


def compare_single_resource_state(expected: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    left = _comparison_projection(expected)
    right = _comparison_projection(current)
    diff: dict[str, Any] = {}
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            diff[key] = {"expected": left.get(key), "current": right.get(key)}
    return diff


def canonical_state_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        state = dict(item)
        if state.get("path") and not state.get("normalized_path"):
            state["normalized_path"] = normalize_path_key(str(state["path"]))
        normalized.append(state)
    return sorted(normalized, key=lambda item: (str(item.get("normalized_path") or item.get("path") or ""), json.dumps(item, sort_keys=True, default=str)))


def read_result_paths(args: dict[str, Any], output: dict[str, Any], context: dict[str, Any]) -> list[Path]:
    values: list[Any] = []
    _collect_path_values(args, "", values, top_level=True, path_keys=PATH_ARG_KEYS)
    _collect_path_values(output, "", values, top_level=True, path_keys=READ_OUTPUT_PATH_KEYS)
    allowed = _allowed_directories(context)
    paths: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = _resolve_candidate_path(value, allowed)
        if path is None:
            continue
        if path.is_dir():
            continue
        key = normalize_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def normalize_path_key(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=False)).casefold()
    except OSError:
        return str(path).casefold()


def _states_from_output(output: dict[str, Any]) -> list[dict[str, Any]]:
    states = output.get("_resource_state")
    if states:
        return canonical_state_list(states)
    states = output.get("_resource_state_after") or output.get("_resource_state_before")
    return canonical_state_list(states or [])


def _read_state_for_path(key: str, context: dict[str, Any]) -> dict[str, Any] | None:
    runtime = context.get("runtime")
    if runtime is not None:
        try:
            cached = runtime.extra_context.get("_resource_read_states", {}).get(key)
            if cached:
                return cached
        except Exception:
            pass
    task_id = _task_id(context, runtime)
    if not task_id:
        return None
    return _TASK_READ_STATES.get(task_id, {}).get(key)


def _read_state_is_recent(cached: dict[str, Any]) -> bool:
    try:
        ttl = max(0, int(os.environ.get("MARVIS_READ_STATE_TTL_SECONDS", READ_STATE_TTL_SECONDS)))
        if ttl <= 0:
            return True
        return (time.time() - float(cached.get("read_at") or 0)) <= ttl
    except (TypeError, ValueError):
        return False


def _states_by_path(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for state in states:
        key = str(state.get("normalized_path") or "")
        if not key and state.get("path"):
            key = normalize_path_key(str(state["path"]))
        if key:
            result[key] = state
    return result


def _comparison_projection(state: dict[str, Any]) -> dict[str, Any]:
    if "path" not in state and "normalized_path" not in state:
        return dict(state)
    keys = ("normalized_path", "exists", "is_file", "is_dir", "size", "mtime_ns", "inode", "sha256")
    return {key: state.get(key) for key in keys if key in state}


def _public_state_ref(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": state.get("path"),
        "exists": state.get("exists"),
        "size": state.get("size"),
        "mtime_ns": state.get("mtime_ns"),
    }


def _task_id(context: dict[str, Any], runtime: Any | None = None) -> str:
    if context.get("task_id"):
        return str(context["task_id"])
    if runtime is not None:
        try:
            return str(runtime.task.id)
        except Exception:
            return ""
    return ""


def _allowed_directories(context: dict[str, Any]) -> list[str]:
    return [str(path) for path in (context.get("allowed_directories") or [])]


def _collect_preview_path_values(value: Any, values: list[Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            text_key = str(key).replace("-", "_").casefold()
            if text_key in PREVIEW_PATH_KEYS or text_key.endswith("_path"):
                _append_path_values(child, values)
            elif isinstance(child, (dict, list, tuple, set)):
                _collect_preview_path_values(child, values)
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            _collect_preview_path_values(child, values)


def _collect_path_values(
    value: Any,
    arg_name: str,
    values: list[Any],
    *,
    top_level: bool,
    path_keys: set[str],
) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_name = f"{arg_name}.{key}" if arg_name else key
            normalized = key.replace("-", "_").casefold()
            if (
                normalized in path_keys
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
                or (top_level and normalized in {"source", "sources", "destination", "destinations", "dest", "dst", "target", "targets"})
            ):
                _append_path_values(child, values)
            elif isinstance(child, (dict, list, tuple, set)):
                _collect_path_values(child, child_name, values, top_level=False, path_keys=path_keys)
        return
    if isinstance(value, (list, tuple, set)):
        for index, child in enumerate(value):
            child_name = f"{arg_name}[{index}]" if arg_name else f"[{index}]"
            _collect_path_values(child, child_name, values, top_level=False, path_keys=path_keys)


def _append_path_values(value: Any, values: list[Any]) -> None:
    if isinstance(value, (str, Path)) and str(value).strip():
        values.append(value)
        return
    if isinstance(value, (list, tuple, set)):
        for child in value:
            _append_path_values(child, values)
        return
    if isinstance(value, dict):
        _collect_path_values(value, "", values, top_level=False, path_keys=PATH_ARG_KEYS)


def _tool_specific_path_values(tool_name: str, args: dict[str, Any], context: dict[str, Any]) -> list[Any]:
    if tool_name == "file.rename" and args.get("source") and args.get("new_name"):
        try:
            source = _resolve_candidate_path(args["source"], _allowed_directories(context))
        except Exception:
            source = None
        if source is not None:
            return [source.with_name(str(args["new_name"]))]
    if tool_name == "file.edit_text" and args.get("path"):
        return [args["path"]]
    return []


def _resolve_candidate_path(value: Any, allowed_directories: list[str]) -> Path | None:
    if not isinstance(value, (str, Path)):
        return None
    text = str(value).strip()
    if not text or text.startswith("(") or "://" in text:
        return None
    try:
        if allowed_directories:
            return resolve_authorized(text, allowed_directories)
        path = Path(text).expanduser()
        if not path.is_absolute() and path.parts and path.parts[0] in {".", ".."}:
            return None
        return path.resolve(strict=False)
    except (OSError, SecurityError, ValueError):
        return None


def _is_modifying_tool(tool: Any) -> bool:
    effects = {str(item).casefold() for item in (getattr(tool, "effects", None) or [])}
    if effects & {"write", "delete", "move", "send", "submit", "type", "external_post", "browser_write"}:
        return True
    risk = getattr(tool, "risk_level", None)
    risk_value = getattr(risk, "value", str(risk or ""))
    if risk_value.startswith(("R2", "R3")):
        return True
    name = str(getattr(tool, "name", ""))
    return any(token in name for token in (".copy", ".move", ".rename", ".trash", ".write", ".edit", ".create", ".delete", ".uninstall"))


def _is_read_tool(tool: Any) -> bool:
    if _is_modifying_tool(tool):
        return False
    if getattr(tool, "is_read_only", None):
        try:
            if tool.is_read_only():
                return True
        except Exception:
            pass
    effects = {str(item).casefold() for item in (getattr(tool, "effects", None) or [])}
    if effects & {"read", "list", "search", "inspect"}:
        return True
    name = str(getattr(tool, "name", ""))
    return any(token in name for token in (".read", ".metadata", ".hash", ".search", ".grep", ".glob", ".list"))
