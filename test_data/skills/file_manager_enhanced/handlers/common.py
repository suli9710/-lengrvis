from __future__ import annotations

import fnmatch
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _allowed_roots(context: dict[str, Any]) -> list[Path]:
    settings = context.get("settings") or {}
    roots = list(context.get("allowed_directories") or settings.get("allowed_directories") or [])
    return [Path(root).expanduser().resolve() for root in roots if str(root).strip()]


def _authorized(path: str, context: dict[str, Any], *, must_exist: bool = True) -> Path:
    roots = _allowed_roots(context)
    if not roots:
        raise ValueError("allowed_directories is required for file manager skills")
    candidate = Path(path).expanduser()
    if must_exist:
        candidate = candidate.resolve(strict=True)
    else:
        candidate = candidate.parent.resolve(strict=True) / candidate.name
    for root in roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise ValueError(f"path is outside allowed directories: {path}")


def _load() -> tuple[dict[str, Any], dict[str, Any]]:
    payload = json.loads(sys.stdin.read() or "{}")
    return payload.get("args") or {}, payload.get("context") or {}


def _iter_files(directory: Path, match_glob: str, recursive: bool = False) -> list[Path]:
    iterator = directory.rglob(match_glob) if recursive else directory.glob(match_glob)
    return sorted([path for path in iterator if path.is_file()], key=lambda path: path.name.lower())


def _batch_rename(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    directory = _authorized(str(args.get("directory") or ""), context)
    if not directory.is_dir():
        raise ValueError("directory must be a folder")
    template = str(args.get("template") or "").strip()
    if not template:
        raise ValueError("template is required")
    match_glob = str(args.get("match_glob") or "*")
    start_index = int(args.get("start_index") or 1)
    recursive = _bool_arg(args, "recursive", False)
    dry_run = _bool_arg(args, "dry_run", True)

    plan = []
    for offset, path in enumerate(_iter_files(directory, match_glob, recursive=recursive), start=start_index):
        target_name = template.format(n=offset, stem=path.stem, ext=path.suffix, name=path.name)
        target = path.with_name(target_name)
        if target == path:
            continue
        if target.exists():
            plan.append({"from": str(path), "to": str(target), "status": "skipped", "reason": "target exists"})
            continue
        plan.append({"from": str(path), "to": str(target), "status": "planned"})

    if not dry_run:
        for item in plan:
            if item["status"] == "planned":
                Path(item["from"]).rename(item["to"])
                item["status"] = "renamed"
    changed = len([item for item in plan if item["status"] in {"planned", "renamed"}])
    return {"ok": True, "dry_run": dry_run, "renamed": 0 if dry_run else changed, "plan": plan}


def _archive_by_rules(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    source_dir = _authorized(str(args.get("source_dir") or ""), context)
    archive_dir = _authorized(str(args.get("archive_dir") or ""), context, must_exist=False)
    if not source_dir.is_dir():
        raise ValueError("source_dir must be a folder")
    rules = args.get("rules") or []
    if not isinstance(rules, list) or not rules:
        raise ValueError("rules must be a non-empty array")
    unmatched = str(args.get("unmatched_destination") or "").strip()
    dry_run = _bool_arg(args, "dry_run", True)

    plan = []
    for path in _iter_files(source_dir, "*"):
        destination_name = ""
        rule_name = ""
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            glob = str(rule.get("glob") or "*")
            if fnmatch.fnmatchcase(path.name.lower(), glob.lower()):
                destination_name = str(rule.get("destination") or rule.get("name") or "").strip()
                rule_name = str(rule.get("name") or glob)
                break
        if not destination_name and unmatched:
            destination_name = unmatched
            rule_name = "unmatched"
        if not destination_name:
            continue
        target_dir = archive_dir / destination_name
        target = target_dir / path.name
        if target.exists():
            plan.append({"from": str(path), "to": str(target), "rule": rule_name, "status": "skipped", "reason": "target exists"})
        else:
            plan.append({"from": str(path), "to": str(target), "rule": rule_name, "status": "planned"})

    if not dry_run:
        for item in plan:
            if item["status"] == "planned":
                target = Path(item["to"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(item["from"], item["to"])
                item["status"] = "moved"
    changed = len([item for item in plan if item["status"] in {"planned", "moved"}])
    return {"ok": True, "dry_run": dry_run, "moved": 0 if dry_run else changed, "plan": plan}


def _included(path: Path, include_globs: list[str], exclude_globs: list[str]) -> bool:
    name = path.name.lower()
    rel = str(path).replace("\\", "/").lower()
    if include_globs and not any(fnmatch.fnmatchcase(name, glob.lower()) or fnmatch.fnmatchcase(rel, glob.lower()) for glob in include_globs):
        return False
    if any(fnmatch.fnmatchcase(name, glob.lower()) or fnmatch.fnmatchcase(rel, glob.lower()) for glob in exclude_globs):
        return False
    return True


def _zip_package(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    output_zip = _authorized(str(args.get("output_zip") or ""), context, must_exist=False)
    sources = [_authorized(str(path), context) for path in (args.get("source_paths") or []) if str(path).strip()]
    if not sources:
        raise ValueError("source_paths must contain at least one path")
    include_globs = [str(item) for item in (args.get("include_globs") or [])]
    exclude_globs = [str(item) for item in (args.get("exclude_globs") or [])]
    dry_run = _bool_arg(args, "dry_run", True)

    files: list[tuple[Path, str]] = []
    for source in sources:
        if source.is_file() and _included(source, include_globs, exclude_globs):
            files.append((source, source.name))
        elif source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file() and path.resolve() != output_zip.resolve() and _included(path, include_globs, exclude_globs):
                    files.append((path, str(path.relative_to(source))))

    if not dry_run:
        output_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, arcname in files:
                archive.write(path, arcname)
    return {
        "ok": True,
        "dry_run": dry_run,
        "packaged": 0 if dry_run else len(files),
        "output_zip": str(output_zip),
        "files": [{"path": str(path), "arcname": arcname} for path, arcname in files[:200]],
        "count": len(files),
    }


def main(operation: str) -> int:
    args, context = _load()
    try:
        if operation == "batch_rename":
            result = _batch_rename(args, context)
        elif operation == "archive_by_rules":
            result = _archive_by_rules(args, context)
        elif operation == "zip_package":
            result = _zip_package(args, context)
        else:
            result = {"ok": False, "error": f"Unsupported operation: {operation}"}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0
