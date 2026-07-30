from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - optional dependency guard
    send2trash = None

from app.core.errors import SecurityError
from app.core.paths import resolve_authorized, resolve_task_path
from app.policy.risk import RiskLevel
from app.services.cleanup_planner_service import CleanupPlannerService
from app.tools import file_tool_schemas as _file_tool_schemas
from app.tools.filesystem_safety import (
    ensure_mutation_path_safe as _ensure_mutation_path_safe,
)
from app.tools.filesystem_safety import (
    safe_copy_file as _safe_copy_file,
)
from app.tools.filesystem_safety import (
    safe_move_file as _safe_move_file,
)
from app.tools.filesystem_safety import (
    safe_write_text as _safe_write_text,
)
from app.tools.managed_backups import create_managed_backup
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import raise_if_tool_aborted
from app.tools.tool_catalog import tool_description, tool_search_hint

_cleanup_plan_schema = _file_tool_schemas._cleanup_plan_schema
_input_schema = _file_tool_schemas.input_schema

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".ts", ".tsx", ".js", ".css", ".yaml", ".yml"}
READ_TEXT_MAX_CHARS = 120000
EDIT_PREVIEW_CONTEXT_CHARS = 240
SEARCH_NAME_DEFAULT_LIMIT = 100
SEARCH_NAME_DEFAULT_MAX_SCANNED = 5000
SEARCH_FULL_TEXT_DEFAULT_LIMIT = 100
SEARCH_FULL_TEXT_DEFAULT_MAX_SCANNED = 5000
SEARCH_FULL_TEXT_DEFAULT_MAX_FILE_BYTES = 1024 * 1024
SEARCH_FULL_TEXT_DEFAULT_MAX_CHARS_PER_FILE = READ_TEXT_MAX_CHARS
FIND_DUPLICATES_DEFAULT_LIMIT = 100
FIND_DUPLICATES_DEFAULT_MAX_SCANNED = 5000
FIND_DUPLICATES_DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
_cleanup_service = CleanupPlannerService()


def _explicit_scope(context: dict[str, Any]) -> str | None:
    value = context.get("explicit_path_scope")
    return str(value) if value else None


def _allowed(context: dict[str, Any]) -> list[str]:
    return list(context.get("allowed_directories") or [])


def _iter_files(context: dict[str, Any]):
    for base in _allowed(context):
        root = resolve_authorized(base, _allowed(context))
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    authorized = resolve_authorized(path, _allowed(context))
                except (OSError, SecurityError, ValueError):
                    authorized = None
                if authorized is not None:
                    yield authorized


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def search_by_name(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).lower()
    limit = _bounded_int(args.get("limit"), SEARCH_NAME_DEFAULT_LIMIT, minimum=1, maximum=500)
    max_scanned = _bounded_int(args.get("max_scanned"), SEARCH_NAME_DEFAULT_MAX_SCANNED, minimum=1, maximum=100000)
    results = []
    scanned = 0
    for path in _iter_files(context):
        scanned += 1
        if not query or query in path.name.lower():
            stat = path.stat()
            results.append({"path": str(path), "name": path.name, "size": stat.st_size, "modified_at": stat.st_mtime})
            if len(results) >= limit:
                return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
        if scanned >= max_scanned:
            return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
    return {"results": results, "count": len(results), "scanned": scanned, "truncated": False}


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def search_full_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).lower()
    limit = _bounded_int(args.get("limit"), SEARCH_FULL_TEXT_DEFAULT_LIMIT, minimum=1, maximum=500)
    max_scanned = _bounded_int(
        args.get("max_scanned"),
        SEARCH_FULL_TEXT_DEFAULT_MAX_SCANNED,
        minimum=1,
        maximum=100000,
    )
    max_file_bytes = _bounded_int(
        args.get("max_file_bytes"),
        SEARCH_FULL_TEXT_DEFAULT_MAX_FILE_BYTES,
        minimum=1,
        maximum=10 * 1024 * 1024,
    )
    max_chars_per_file = _bounded_int(
        args.get("max_chars_per_file"),
        SEARCH_FULL_TEXT_DEFAULT_MAX_CHARS_PER_FILE,
        minimum=1,
        maximum=READ_TEXT_MAX_CHARS,
    )
    results = []
    scanned = 0
    truncated = False
    for path in _iter_files(context):
        scanned += 1
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            if scanned >= max_scanned:
                return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
            continue
        try:
            with path.open("rb") as fh:
                data = fh.read(max_file_bytes + 1)
        except OSError:
            if scanned >= max_scanned:
                return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
            continue

        if len(data) > max_file_bytes:
            truncated = True
            data = data[:max_file_bytes]

        text = data.decode("utf-8", errors="ignore")
        if len(text) > max_chars_per_file:
            truncated = True
            text = text[:max_chars_per_file]

        haystack = text.lower()
        if query in haystack:
            idx = haystack.find(query)
            snippet = text[max(0, idx - 80) : idx + 160]
            results.append({"path": str(path), "snippet": snippet})
            if len(results) >= limit:
                return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
        if scanned >= max_scanned:
            return {"results": results, "count": len(results), "scanned": scanned, "truncated": True}
    return {"results": results, "count": len(results), "scanned": scanned, "truncated": truncated}


def semantic_search(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    limit = int(args.get("limit") or 10)
    vector_module = importlib.import_module("app.indexer.vector_index")
    return vector_module.VectorIndex().search(query, limit=limit, allowed_directories=_allowed(context))


def list_directory(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    if not path.is_dir():
        return {"entries": [], "error": "Path is not a directory."}
    entries = []
    for child in path.iterdir():
        stat = child.stat()
        entries.append({"path": str(child), "name": child.name, "is_dir": child.is_dir(), "size": stat.st_size})
    return {"entries": entries}


def get_metadata(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    stat = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size": stat.st_size,
        "created_at": stat.st_ctime,
        "modified_at": stat.st_mtime,
        "sha256": sha256_file(path) if path.is_file() else "",
    }


def hash_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    return {"path": str(path), "sha256": sha256_file(path)}


def read_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "Path is not a file."}
    max_chars = max(1, min(int(args.get("max_chars") or READ_TEXT_MAX_CHARS), READ_TEXT_MAX_CHARS))
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "path": str(path),
        "text": text[:max_chars],
        "truncated": truncated,
        "chars": len(text),
        "sha256": sha256_file(path),
        "_resource_state": _resource_states(path),
    }


def find_duplicates(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(args.get("limit"), FIND_DUPLICATES_DEFAULT_LIMIT, minimum=1, maximum=500)
    max_scanned = _bounded_int(
        args.get("max_scanned"),
        FIND_DUPLICATES_DEFAULT_MAX_SCANNED,
        minimum=1,
        maximum=100000,
    )
    max_file_bytes = _bounded_int(
        args.get("max_file_bytes"),
        FIND_DUPLICATES_DEFAULT_MAX_FILE_BYTES,
        minimum=1,
        maximum=1024 * 1024 * 1024,
    )
    groups: dict[str, list[str]] = {}
    scanned = 0
    skipped_large = 0
    duplicate_group_count = 0
    for path in _iter_files(context):
        scanned += 1
        try:
            stat = path.stat()
        except OSError:
            if scanned >= max_scanned:
                return _duplicate_result(groups, limit, scanned=scanned, truncated=True, skipped_large=skipped_large)
            continue
        if stat.st_size > max_file_bytes:
            skipped_large += 1
            if scanned >= max_scanned:
                return _duplicate_result(groups, limit, scanned=scanned, truncated=True, skipped_large=skipped_large)
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            if scanned >= max_scanned:
                return _duplicate_result(groups, limit, scanned=scanned, truncated=True, skipped_large=skipped_large)
            continue
        groups.setdefault(digest, []).append(str(path))
        if len(groups[digest]) == 2:
            duplicate_group_count += 1
            if duplicate_group_count >= limit:
                return _duplicate_result(groups, limit, scanned=scanned, truncated=True, skipped_large=skipped_large)
        if scanned >= max_scanned:
            return _duplicate_result(groups, limit, scanned=scanned, truncated=True, skipped_large=skipped_large)
    return _duplicate_result(groups, limit, scanned=scanned, truncated=skipped_large > 0, skipped_large=skipped_large)


def _duplicate_result(
    groups: dict[str, list[str]],
    limit: int,
    *,
    scanned: int,
    truncated: bool,
    skipped_large: int,
) -> dict[str, Any]:
    duplicates = [{"sha256": digest, "paths": paths} for digest, paths in groups.items() if len(paths) > 1][:limit]
    return {
        "duplicates": duplicates,
        "count": len(duplicates),
        "scanned": scanned,
        "truncated": truncated,
        "skipped_large": skipped_large,
    }


def cleanup_scan(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return _cleanup_service.cleanup_scan(args, context)


def cleanup_plan(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    plan = _cleanup_service.create_plan(args, context)
    return {"ok": True, **plan.model_dump(mode="json")}


def dedupe_plan(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    plan = _cleanup_service.create_dedupe_plan(args, context)
    return {"ok": True, **plan.model_dump(mode="json")}


def cleanup_execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return _cleanup_service.execute(args, context)


def cleanup_rollback(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    from app.tools.rollback_tools import rollback_cleanup_result

    return rollback_cleanup_result(args, context)


def preview_batch_operation(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    return {
        "dry_run": True,
        "operation": args.get("operation", "organize_files"),
        "query": query,
        "diff_preview": [
            {
                "action": "preview",
                "from": "(matching authorized files)",
                "to": args.get("target_folder", "(choose target folder after approval)"),
            }
        ],
        "message": "Preview only. Approval is required before any file is moved, copied, renamed, or deleted.",
    }


def create_folder(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    path = resolve_authorized(args["path"], allowed)
    if args.get("dry_run", True):
        return {"dry_run": True, "would_create": str(path)}
    raise_if_tool_aborted(context)
    _safe_mkdir(path, allowed, context)
    return {"changed_paths": [str(path)], "rollback_info": {"delete_folder_if_empty": str(path)}}


def copy_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    src = resolve_authorized(args["source"], allowed)
    dst = resolve_authorized(args["destination"], allowed)
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "copy", "from": str(src), "to": str(dst)}],
            "_resource_state": _resource_states(src, dst),
        }
    raise_if_tool_aborted(context)
    # If the destination already exists we are about to overwrite it. Back it up
    # first so the original content is recoverable; recording only
    # trash_created_file here would send the *copy* to the recycle bin on
    # rollback and leave the overwritten original unrecoverable.
    dst_existed = dst.exists()
    dst_backup = create_managed_backup(dst) if dst_existed else None
    _safe_copy_file(src, dst, allowed, context)
    if dst_backup is not None:
        rollback_info: dict[str, Any] = {"backup": dst_backup}
    else:
        rollback_info = {"trash_created_file": str(dst)}
    return {"changed_paths": [str(dst)], "rollback_info": rollback_info}


def move_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    src = resolve_authorized(args["source"], allowed)
    dst = resolve_authorized(args["destination"], allowed)
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "move", "from": str(src), "to": str(dst)}],
            "_resource_state": _resource_states(src, dst),
        }
    raise_if_tool_aborted(context)
    dst_backup = create_managed_backup(dst) if dst.exists() and dst != src else None
    _safe_move_file(src, dst, allowed, context)
    rollback_info = {"move_back": {"from": str(dst), "to": str(src)}}
    if dst_backup is not None:
        # Overwrote an existing destination: after moving dst back to src, also
        # restore the destination's original content.
        rollback_info["dst_backup"] = dst_backup
    return {"changed_paths": [str(dst)], "rollback_info": rollback_info}


def rename_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    src = resolve_authorized(args["source"], allowed)
    dst = src.with_name(str(args["new_name"]))
    dst = resolve_authorized(dst, allowed)
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "rename", "from": str(src), "to": str(dst)}],
            "_resource_state": _resource_states(src, dst),
        }
    raise_if_tool_aborted(context)
    dst_backup = create_managed_backup(dst) if dst.exists() and dst != src else None
    _safe_move_file(src, dst, allowed, context)
    rollback_info = {"rename_back": {"from": str(dst), "to": str(src)}}
    if dst_backup is not None:
        rollback_info["dst_backup"] = dst_backup
    return {"changed_paths": [str(dst)], "rollback_info": rollback_info}


def trash_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_trash_target(args["path"], context)
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "trash", "path": str(path)}],
            "_resource_state": _resource_states(path),
        }
    if send2trash is None:
        raise RuntimeError("send2trash is not installed; permanent deletion is forbidden.")
    raise_if_tool_aborted(context)
    _ensure_mutation_path_safe(path, _allowed(context), include_self=True, context=context)
    send2trash(str(path))
    return {"changed_paths": [str(path)], "rollback_info": {"restore_from_recycle_bin": str(path)}}


def _resolve_trash_target(path_value: str | Path, context: dict[str, Any]) -> Path:
    allowed = _allowed(context)
    scope = _explicit_scope(context)
    if allowed:
        return resolve_authorized(path_value, allowed)
    return resolve_task_path(path_value, allowed, explicit_scope_text=scope)


def write_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    path = resolve_authorized(args["path"], allowed)
    text = str(args.get("text", ""))
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "write_text", "path": str(path), "bytes": len(text)}],
            "_resource_state": _resource_states(path),
        }
    raise_if_tool_aborted(context)
    backup = None
    if path.exists():
        _ensure_mutation_path_safe(path, allowed, include_self=True)
        backup = create_managed_backup(path)
    _safe_write_text(path, text, allowed, context)
    return {"changed_paths": [str(path)], "rollback_info": {"backup": backup}}


def edit_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    path = resolve_authorized(args["path"], allowed)
    old_string = str(args.get("old_string") or "")
    new_string = str(args.get("new_string") or "")
    replace_all = bool(args.get("replace_all", False))
    if not old_string:
        return {"ok": False, "error": "old_string is required.", "error_code": "OLD_STRING_REQUIRED"}
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "Path is not a file.", "error_code": "PATH_NOT_FILE"}

    _ensure_mutation_path_safe(path, allowed, include_self=True)
    text = path.read_text(encoding="utf-8", errors="replace")
    match_count = text.count(old_string)
    if match_count == 0:
        return {
            "ok": False,
            "path": str(path),
            "error": "old_string was not found.",
            "error_code": "NO_MATCH",
            "match_count": 0,
            "_resource_state": _resource_states(path),
        }
    if not replace_all and match_count != 1:
        return {
            "ok": False,
            "path": str(path),
            "error": "old_string must match exactly once unless replace_all is true.",
            "error_code": "NON_UNIQUE_MATCH",
            "match_count": match_count,
            "_resource_state": _resource_states(path),
        }

    edited = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    replacements = match_count if replace_all else 1
    if args.get("dry_run", True):
        return {
            "ok": True,
            "dry_run": True,
            "path": str(path),
            "match_count": match_count,
            "replacements": replacements,
            "diff_preview": [
                {
                    "action": "edit_text",
                    "path": str(path),
                    "replace_all": replace_all,
                    "match_count": match_count,
                    "old_preview": _text_preview(old_string),
                    "new_preview": _text_preview(new_string),
                }
            ],
            "_resource_state": _resource_states(path),
        }

    raise_if_tool_aborted(context)
    backup = create_managed_backup(path)
    _safe_write_text(path, edited, allowed, context)
    return {
        "ok": True,
        "changed_paths": [str(path)],
        "match_count": match_count,
        "replacements": replacements,
        "rollback_info": {"backup": backup},
    }


def generate_markdown_report(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed(context)
    path = resolve_authorized(args["path"], allowed)
    title = args.get("title", "Lengrvis Report")
    body = args.get("body", "")
    text = f"# {title}\n\n{body}\n"
    if args.get("dry_run", True):
        return {
            "dry_run": True,
            "diff_preview": [{"action": "generate_markdown_report", "path": str(path)}],
            "_resource_state": _resource_states(path),
        }
    raise_if_tool_aborted(context)
    _safe_write_text(path, text, allowed, context)
    return {"changed_paths": [str(path)], "rollback_info": {"trash_created_file": str(path)}}


def _safe_mkdir(path: Path, allowed: list[str], context: dict[str, Any] | None = None) -> None:
    raise_if_tool_aborted(context)
    _ensure_mutation_path_safe(path, allowed, include_self=True)
    path.mkdir(parents=True, exist_ok=True)
    _ensure_mutation_path_safe(path, allowed, include_self=True)


def _safe_copy_existing_file(
    src: Path,
    dst: Path,
    allowed: list[str],
    context: dict[str, Any] | None = None,
) -> None:
    raise_if_tool_aborted(context)
    _safe_copy_file(src, dst, allowed, context)


def _resource_states(*paths: Path) -> list[dict[str, Any]]:
    return [_resource_state(path) for path in paths]


def _resource_state(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    state: dict[str, Any] = {"path": str(resolved), "exists": resolved.exists()}
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


def _text_preview(value: str) -> str:
    if len(value) <= EDIT_PREVIEW_CONTEXT_CHARS:
        return value
    return value[:EDIT_PREVIEW_CONTEXT_CHARS]


def register(registry) -> None:
    defs = [
        ("file.search_by_name", search_by_name, RiskLevel.R0_READ_ONLY, False, True),
        ("file.search_full_text", search_full_text, RiskLevel.R0_READ_ONLY, False, True),
        ("file.semantic_search", semantic_search, RiskLevel.R0_READ_ONLY, False, True),
        ("file.list_directory", list_directory, RiskLevel.R0_READ_ONLY, False, True),
        ("file.get_metadata", get_metadata, RiskLevel.R0_READ_ONLY, False, True),
        ("file.hash_file", hash_file, RiskLevel.R0_READ_ONLY, False, True),
        ("file.read_text", read_text, RiskLevel.R0_READ_ONLY, False, True),
        ("file.find_duplicates", find_duplicates, RiskLevel.R0_READ_ONLY, False, True),
        ("file.cleanup_scan", cleanup_scan, RiskLevel.R0_READ_ONLY, False, True),
        ("file.cleanup_plan", cleanup_plan, RiskLevel.R0_READ_ONLY, False, True),
        ("file.dedupe_plan", dedupe_plan, RiskLevel.R0_READ_ONLY, False, True),
        ("file.cleanup_execute", cleanup_execute, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True, True),
        ("file.cleanup_rollback", cleanup_rollback, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True, True),
        ("file.preview_batch_operation", preview_batch_operation, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.create_folder", create_folder, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.copy", copy_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.move", move_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.rename", rename_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.trash", trash_file, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True, True),
        ("file.write_text", write_text, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.edit_text", edit_text, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.generate_markdown_report", generate_markdown_report, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
    ]
    for name, fn, risk, dry_run, auth in defs:
        read_only = risk == RiskLevel.R0_READ_ONLY and not dry_run
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema=_input_schema(name),
                output_schema={},
                risk_level=risk,
                agent_owner="FileAgent",
                supports_dry_run=dry_run,
                requires_authorized_path=auth,
                execute=fn,
                capabilities=["filesystem"] if auth else ["filesystem_preview"],
                effects=["read", "list", "search"] if read_only else ["write"],
                resource_kinds=["file", "directory"],
                fast_path_eligible=read_only,
                trust_tier="builtin",
                sensitive_arg_keys=_sensitive_arg_keys(name),
            )
        )


def _sensitive_arg_keys(name: str) -> list[str]:
    if name == "file.write_text":
        return ["text"]
    if name == "file.edit_text":
        return ["old_string", "new_string"]
    return []
