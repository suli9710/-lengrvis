from __future__ import annotations

import hashlib
import importlib
import shutil
from pathlib import Path
from typing import Any

try:
    from send2trash import send2trash
except Exception:  # pragma: no cover - optional dependency guard
    send2trash = None

from app.core.paths import resolve_authorized
from app.policy.risk import RiskLevel
from app.services.cleanup_planner_service import CleanupPlannerService
from app.tools.schemas import ToolDefinition


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".ts", ".tsx", ".js", ".css", ".yaml", ".yml"}
READ_TEXT_MAX_CHARS = 120000
EDIT_PREVIEW_CONTEXT_CHARS = 240
_cleanup_service = CleanupPlannerService()


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
                    yield resolve_authorized(path, _allowed(context))
                except Exception:
                    continue


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def search_by_name(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).lower()
    results = []
    for path in _iter_files(context):
        if not query or query in path.name.lower():
            stat = path.stat()
            results.append({"path": str(path), "name": path.name, "size": stat.st_size, "modified_at": stat.st_mtime})
    return {"results": results[:100], "count": len(results)}


def search_full_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).lower()
    results = []
    for path in _iter_files(context):
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if query in text.lower():
            idx = text.lower().find(query)
            snippet = text[max(0, idx - 80) : idx + 160]
            results.append({"path": str(path), "snippet": snippet})
    return {"results": results[:100], "count": len(results)}


def semantic_search(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", ""))
    limit = int(args.get("limit") or 10)
    vector_module = importlib.import_module("app.indexer.vector_index")
    return vector_module.VectorIndex().search(query, limit=limit)


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
    groups: dict[str, list[str]] = {}
    for path in _iter_files(context):
        digest = sha256_file(path)
        groups.setdefault(digest, []).append(str(path))
    duplicates = [{"sha256": digest, "paths": paths} for digest, paths in groups.items() if len(paths) > 1]
    return {"duplicates": duplicates, "count": len(duplicates)}


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
    path = resolve_authorized(args["path"], _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "would_create": str(path)}
    path.mkdir(parents=True, exist_ok=True)
    return {"changed_paths": [str(path)], "rollback_info": {"delete_folder_if_empty": str(path)}}


def copy_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    src = resolve_authorized(args["source"], _allowed(context))
    dst = resolve_authorized(args["destination"], _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "copy", "from": str(src), "to": str(dst)}], "_resource_state": _resource_states(src, dst)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"changed_paths": [str(dst)], "rollback_info": {"trash_created_file": str(dst)}}


def move_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    src = resolve_authorized(args["source"], _allowed(context))
    dst = resolve_authorized(args["destination"], _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "move", "from": str(src), "to": str(dst)}], "_resource_state": _resource_states(src, dst)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"changed_paths": [str(dst)], "rollback_info": {"move_back": {"from": str(dst), "to": str(src)}}}


def rename_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    src = resolve_authorized(args["source"], _allowed(context))
    dst = src.with_name(str(args["new_name"]))
    dst = resolve_authorized(dst, _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "rename", "from": str(src), "to": str(dst)}], "_resource_state": _resource_states(src, dst)}
    src.rename(dst)
    return {"changed_paths": [str(dst)], "rollback_info": {"rename_back": {"from": str(dst), "to": str(src)}}}


def trash_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_trash_target(args["path"], context)
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "trash", "path": str(path)}], "_resource_state": _resource_states(path)}
    if send2trash is None:
        raise RuntimeError("send2trash is not installed; permanent deletion is forbidden.")
    send2trash(str(path))
    return {"changed_paths": [str(path)], "rollback_info": {"restore_from_recycle_bin": str(path)}}


def _resolve_trash_target(path_value: str | Path, context: dict[str, Any]) -> Path:
    return resolve_authorized(path_value, _allowed(context))


def write_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    text = str(args.get("text", ""))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "write_text", "path": str(path), "bytes": len(text)}], "_resource_state": _resource_states(path)}
    backup = None
    if path.exists():
        backup = str(path.with_suffix(path.suffix + ".bak"))
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"changed_paths": [str(path)], "rollback_info": {"backup": backup}}


def edit_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    old_string = str(args.get("old_string") or "")
    new_string = str(args.get("new_string") or "")
    replace_all = bool(args.get("replace_all", False))
    if not old_string:
        return {"ok": False, "error": "old_string is required.", "error_code": "OLD_STRING_REQUIRED"}
    if not path.is_file():
        return {"ok": False, "path": str(path), "error": "Path is not a file.", "error_code": "PATH_NOT_FILE"}

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

    backup = str(path.with_suffix(path.suffix + ".bak"))
    shutil.copy2(path, backup)
    path.write_text(edited, encoding="utf-8")
    return {
        "ok": True,
        "changed_paths": [str(path)],
        "match_count": match_count,
        "replacements": replacements,
        "rollback_info": {"backup": backup},
    }


def generate_markdown_report(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    title = args.get("title", "Marvis Report")
    body = args.get("body", "")
    text = f"# {title}\n\n{body}\n"
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "generate_markdown_report", "path": str(path)}], "_resource_state": _resource_states(path)}
    path.write_text(text, encoding="utf-8")
    return {"changed_paths": [str(path)], "rollback_info": {"trash_created_file": str(path)}}


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


def _input_schema(name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "file.search_by_name": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        "file.search_full_text": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "file.semantic_search": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "file.list_directory": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.get_metadata": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.hash_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.read_text": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.find_duplicates": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        "file.cleanup_scan": _cleanup_plan_schema(read_only=True),
        "file.cleanup_plan": _cleanup_plan_schema(read_only=True),
        "file.dedupe_plan": _cleanup_plan_schema(read_only=True),
        "file.cleanup_execute": {
            "type": "object",
            "properties": {
                "roots": {"type": "array", "items": {"type": "string"}},
                "plan_id": {"type": "string"},
                "content_hash": {"type": "string"},
                "selected_item_ids": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"},
                "approved": {"type": "boolean"},
                "approval_id": {"type": "string"},
                "threshold_mb": {"type": "number"},
                "older_than_days": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["roots", "plan_id", "content_hash", "selected_item_ids"],
            "additionalProperties": False,
        },
        "file.cleanup_rollback": {
            "type": "object",
            "properties": {
                "rollback_info": {"type": "object"},
                "dry_run": {"type": "boolean"},
                "approved": {"type": "boolean"},
                "approval_id": {"type": "string"},
            },
            "required": ["rollback_info"],
            "additionalProperties": False,
        },
        "file.preview_batch_operation": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "operation": {"type": "string"},
                "target_folder": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "file.create_folder": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.copy": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
        "file.move": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
        "file.rename": {
            "type": "object",
            "properties": {"source": {"type": "string"}, "new_name": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["source", "new_name"],
            "additionalProperties": False,
        },
        "file.trash": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.write_text": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path", "text"],
            "additionalProperties": False,
        },
        "file.edit_text": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
        "file.generate_markdown_report": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }
    return schemas.get(name, {"type": "object", "properties": {}, "additionalProperties": False})


def _cleanup_plan_schema(*, read_only: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "roots": {"type": "array", "items": {"type": "string"}},
            "threshold_mb": {"type": "number"},
            "older_than_days": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    if not read_only:
        schema["properties"]["dry_run"] = {"type": "boolean"}
    return schema


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
                description=name.replace(".", " "),
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
                sensitive_arg_keys=["text"] if name == "file.write_text" else ["old_string", "new_string"] if name == "file.edit_text" else [],
            )
        )
