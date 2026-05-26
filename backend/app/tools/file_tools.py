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

from app.core.errors import SecurityError
from app.core.paths import is_sensitive_path, is_system_path, normalize_path, resolve_authorized
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".ts", ".tsx", ".js", ".css", ".yaml", ".yml"}


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


def find_duplicates(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for path in _iter_files(context):
        digest = sha256_file(path)
        groups.setdefault(digest, []).append(str(path))
    duplicates = [{"sha256": digest, "paths": paths} for digest, paths in groups.items() if len(paths) > 1]
    return {"duplicates": duplicates, "count": len(duplicates)}


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
        return {"dry_run": True, "diff_preview": [{"action": "copy", "from": str(src), "to": str(dst)}]}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"changed_paths": [str(dst)], "rollback_info": {"trash_created_file": str(dst)}}


def move_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    src = resolve_authorized(args["source"], _allowed(context))
    dst = resolve_authorized(args["destination"], _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "move", "from": str(src), "to": str(dst)}]}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"changed_paths": [str(dst)], "rollback_info": {"move_back": {"from": str(dst), "to": str(src)}}}


def rename_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    src = resolve_authorized(args["source"], _allowed(context))
    dst = src.with_name(str(args["new_name"]))
    dst = resolve_authorized(dst, _allowed(context))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "rename", "from": str(src), "to": str(dst)}]}
    src.rename(dst)
    return {"changed_paths": [str(dst)], "rollback_info": {"rename_back": {"from": str(dst), "to": str(src)}}}


def trash_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_trash_target(args["path"], context)
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "trash", "path": str(path)}]}
    if send2trash is None:
        raise RuntimeError("send2trash is not installed; permanent deletion is forbidden.")
    send2trash(str(path))
    return {"changed_paths": [str(path)], "rollback_info": {"restore_from_recycle_bin": str(path)}}


def _resolve_trash_target(path_value: str | Path, context: dict[str, Any]) -> Path:
    allowed = _allowed(context)
    if allowed:
        return resolve_authorized(path_value, allowed)

    candidate = normalize_path(path_value)
    raw_path = Path(path_value)
    if ".." in raw_path.parts:
        raise SecurityError("Path traversal is not allowed.")
    if not raw_path.is_absolute():
        raise SecurityError("Explicit absolute path is required when no authorized directories are configured.")
    if is_system_path(candidate) or is_sensitive_path(candidate):
        raise SecurityError("Sensitive or system paths are not allowed.")
    if candidate.exists() and candidate.is_symlink():
        target = candidate.resolve(strict=True)
        if is_system_path(target) or is_sensitive_path(target):
            raise SecurityError("Symbolic link points to a sensitive or system path.")
    return candidate


def write_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    text = str(args.get("text", ""))
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "write_text", "path": str(path), "bytes": len(text)}]}
    backup = None
    if path.exists():
        backup = str(path.with_suffix(path.suffix + ".bak"))
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"changed_paths": [str(path)], "rollback_info": {"backup": backup}}


def generate_markdown_report(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path = resolve_authorized(args["path"], _allowed(context))
    title = args.get("title", "Marvis Report")
    body = args.get("body", "")
    text = f"# {title}\n\n{body}\n"
    if args.get("dry_run", True):
        return {"dry_run": True, "diff_preview": [{"action": "generate_markdown_report", "path": str(path)}]}
    path.write_text(text, encoding="utf-8")
    return {"changed_paths": [str(path)], "rollback_info": {"trash_created_file": str(path)}}


def register(registry) -> None:
    defs = [
        ("file.search_by_name", search_by_name, RiskLevel.R0_READ_ONLY, False, True),
        ("file.search_full_text", search_full_text, RiskLevel.R0_READ_ONLY, False, True),
        ("file.semantic_search", semantic_search, RiskLevel.R0_READ_ONLY, False, True),
        ("file.list_directory", list_directory, RiskLevel.R0_READ_ONLY, False, True),
        ("file.get_metadata", get_metadata, RiskLevel.R0_READ_ONLY, False, True),
        ("file.hash_file", hash_file, RiskLevel.R0_READ_ONLY, False, True),
        ("file.find_duplicates", find_duplicates, RiskLevel.R0_READ_ONLY, False, True),
        ("file.preview_batch_operation", preview_batch_operation, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.create_folder", create_folder, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.copy", copy_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.move", move_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.rename", rename_file, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.trash", trash_file, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, True, True),
        ("file.write_text", write_text, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
        ("file.generate_markdown_report", generate_markdown_report, RiskLevel.R2_REVERSIBLE_MODIFY, True, True),
    ]
    for name, fn, risk, dry_run, auth in defs:
        read_only = risk == RiskLevel.R0_READ_ONLY and not dry_run
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
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
                sensitive_arg_keys=["text"] if name == "file.write_text" else [],
            )
        )
