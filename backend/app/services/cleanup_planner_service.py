from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

try:
    from send2trash import send2trash
except Exception:  # pragma: no cover - optional dependency guard
    send2trash = None

from app.core.audit import record
from app.core import db
from app.core.errors import SecurityError
from app.core.paths import is_sensitive_path, is_system_path, normalize_path, resolve_authorized
from app.core.schemas import Approval, ApprovalStatus, now_iso
from app.policy.approval_binding import (
    args_binding_hmac,
    permission_policy_version,
    preview_hmac,
    settings_fingerprint,
)
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel


CleanupAction = Literal["delete_direct", "trash_with_prompt", "review_only"]

DIRECT_DELETE_DIR_NAMES = {
    ".cache",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".sass-cache",
    ".turbo",
    ".vite",
    "__pycache__",
    "build",
    "cache",
    "caches",
    "coverage",
    "dist",
    "node_modules/.cache",
    "out",
    "target",
    "temp",
    "tmp",
}
DIRECT_DELETE_FILE_SUFFIXES = {
    ".cache",
    ".dmp",
    ".log",
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
}
USER_REVIEW_DIRS = {"desktop", "documents", "downloads", "music", "pictures", "videos"}
DOWNLOAD_REVIEW_EXTENSIONS = {".7z", ".dmg", ".exe", ".gz", ".iso", ".msi", ".rar", ".tar", ".tgz", ".zip"}
MEDIA_EXTENSIONS = {".avi", ".flac", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".rtf", ".xls", ".xlsx"}
CONFIG_EXTENSIONS = {".cfg", ".conf", ".config", ".env", ".ini", ".json", ".toml", ".yaml", ".yml"}
DATABASE_EXTENSIONS = {".accdb", ".db", ".db3", ".mdb", ".sqlite", ".sqlite3", ".sql"}
SENSITIVE_NAME_TERMS = {
    ".aws",
    ".azure",
    ".gnupg",
    ".ssh",
    "api_key",
    "apikey",
    "cookie",
    "credential",
    "credentials",
    "id_rsa",
    "key",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
}
BROWSER_PROFILE_MARKERS = {
    "chrome/user data",
    "edge/user data",
    "firefox/profiles",
    "google/chrome/user data",
    "microsoft/edge/user data",
}
SOURCE_ROOT_MARKERS = {
    ".git",
    ".hg",
    ".svn",
    "Cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
}


class CleanupItem(BaseModel):
    id: str
    path: str
    category: str
    size_bytes: int = 0
    action: CleanupAction
    risk: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    requires_approval: bool = False
    duplicate_group_id: str | None = None
    modified_at: float = 0
    mtime_ns: int = 0
    is_dir: bool = False

    @model_validator(mode="after")
    def _approval_matches_action(self) -> "CleanupItem":
        self.requires_approval = self.action in {"trash_with_prompt", "review_only"}
        return self


class CleanupPlan(BaseModel):
    plan_id: str = ""
    roots: list[str]
    items: list[CleanupItem] = Field(default_factory=list)
    total_reclaimable_bytes: int = 0
    direct_delete_bytes: int = 0
    trash_bytes: int = 0
    review_only_bytes: int = 0
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    content_hash: str = ""

    @model_validator(mode="after")
    def _fill_hashes(self) -> "CleanupPlan":
        self.items = sorted(self.items, key=lambda item: (item.path.casefold(), item.action, item.id))
        self.roots = sorted(str(root) for root in self.roots)
        totals = _summarize_items(self.items)
        self.total_reclaimable_bytes = totals["total_reclaimable_bytes"]
        self.direct_delete_bytes = totals["direct_delete_bytes"]
        self.trash_bytes = totals["trash_bytes"]
        self.review_only_bytes = totals["review_only_bytes"]
        self.risk_summary = totals["risk_summary"]
        self.content_hash = compute_plan_hash(self)
        self.plan_id = cleanup_plan_id(self.content_hash)
        return self


class CleanupPlannerService:
    def cleanup_scan(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        plan = self.create_plan(args, context)
        return {
            "ok": True,
            "roots": plan.roots,
            "items": [item.model_dump(mode="json") for item in plan.items],
            "count": len(plan.items),
            "risk_summary": plan.risk_summary,
        }

    def create_plan(self, args: dict[str, Any], context: dict[str, Any]) -> CleanupPlan:
        options = _scan_options(args)
        roots = _resolve_roots(args.get("roots") or context.get("allowed_directories") or [], context)
        items: list[CleanupItem] = []
        seen_paths: set[str] = set()

        for root in roots:
            root_key = _normalized_key(root)
            if _is_source_repo_root(root):
                item = _item_for_path(
                    root,
                    category="source_repo_root",
                    action="review_only",
                    risk="high",
                    confidence=0.98,
                    reason="Source repository roots are review-only cleanup targets.",
                    is_dir=True,
                )
                items.append(item)
                seen_paths.add(_normalized_key(root))

            for path in _walk_root(root, max_scanned=options["max_scanned"]):
                key = _normalized_key(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                item = _classify_path(path, options)
                if item is not None:
                    items.append(item)

        duplicate_items = self._duplicate_items(roots, options)
        existing_item_paths = {_normalized_key(item.path) for item in items}
        for item in duplicate_items:
            if _normalized_key(item.path) not in existing_item_paths:
                items.append(item)

        if options["limit"] > 0:
            items = items[: options["limit"]]
        return CleanupPlan(roots=[str(root) for root in roots], items=items)

    def create_dedupe_plan(self, args: dict[str, Any], context: dict[str, Any]) -> CleanupPlan:
        options = _scan_options(args)
        roots = _resolve_roots(args.get("roots") or context.get("allowed_directories") or [], context)
        return CleanupPlan(roots=[str(root) for root in roots], items=self._duplicate_items(roots, options))

    def execute(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        selected_ids = [str(item_id) for item_id in args.get("selected_item_ids") or []]
        plan = self.create_plan(args, context)
        expected_plan_id = str(args.get("plan_id") or "")
        expected_hash = str(args.get("content_hash") or "")
        if expected_plan_id != plan.plan_id or expected_hash != plan.content_hash:
            raise SecurityError("Cleanup plan validation failed: plan_id/content_hash do not match current scan.")
        if not selected_ids:
            return {
                "ok": True,
                "dry_run": bool(args.get("dry_run", True)),
                "plan_id": plan.plan_id,
                "content_hash": plan.content_hash,
                "changed_paths": [],
                "rollback_info": {},
                "audit": [],
            }

        items_by_id = {item.id: item for item in plan.items}
        unknown = [item_id for item_id in selected_ids if item_id not in items_by_id]
        if unknown:
            raise SecurityError(f"Cleanup plan validation failed: unknown selected item ids: {', '.join(unknown)}.")

        selected = [items_by_id[item_id] for item_id in selected_ids]
        dry_run = bool(args.get("dry_run", True))
        review_only = [item for item in selected if item.action == "review_only"]
        executable = [item for item in selected if item.action != "review_only"]

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "plan_id": plan.plan_id,
                "content_hash": plan.content_hash,
                "diff_preview": [_preview_item(item) for item in selected],
                "skipped": [_preview_item(item) for item in review_only],
                "_resource_state": [_resource_state(Path(root)) for root in plan.roots],
            }

        if executable:
            _claim_valid_cleanup_approval(args, context)

        trash_items = [item for item in executable if item.action == "trash_with_prompt"]
        if trash_items and (not args.get("approved") or not args.get("approval_id")):
            raise SecurityError("Cleanup recycle-bin execution requires approved=true and approval_id.")

        changed_paths: list[str] = []
        rollback_info: dict[str, Any] = {}
        audit: list[dict[str, Any]] = []
        direct_deleted: list[dict[str, str]] = []
        recycle_restore_paths: list[str] = []

        for item in executable:
            path = resolve_authorized(item.path, _allowed(context))
            if item.action == "delete_direct":
                if not is_direct_delete_allowed(path):
                    raise SecurityError(f"Direct deletion is not allowed for non-whitelisted cleanup item: {path}")
                self._delete_direct(path)
                changed_paths.append(str(path))
                detail = {"path": str(path), "reason": "Permanent direct cleanup deletion is not recoverable."}
                direct_deleted.append(detail)
                audit.append({"action": "delete_direct", **detail})
                _record_cleanup_event("file.cleanup_execute.delete_direct", detail, context)
                continue

            if item.action == "trash_with_prompt":
                if send2trash is None:
                    raise RuntimeError("send2trash is not installed; recycle-bin cleanup is unavailable.")
                send2trash(str(path))
                changed_paths.append(str(path))
                recycle_restore_paths.append(str(path))
                detail = {
                    "path": str(path),
                    "approval_id": str(args.get("approval_id") or ""),
                    "rollback": "restore_from_recycle_bin",
                }
                audit.append({"action": "trash_with_prompt", **detail})
                _record_cleanup_event("file.cleanup_execute.trash_with_prompt", detail, context)

        if direct_deleted:
            rollback_info["permanent_delete_unrecoverable"] = direct_deleted
        if recycle_restore_paths:
            rollback_info["restore_from_recycle_bin"] = recycle_restore_paths

        return {
            "ok": True,
            "dry_run": False,
            "plan_id": plan.plan_id,
            "content_hash": plan.content_hash,
            "changed_paths": changed_paths,
            "rollback_info": rollback_info,
            "audit": audit,
            "skipped": [_preview_item(item) for item in review_only],
        }

    def _delete_direct(self, path: Path) -> None:
        if path.is_dir():
            path.rmdir()
            return
        path.unlink()

    def _duplicate_items(self, roots: list[Path], options: dict[str, Any]) -> list[CleanupItem]:
        size_groups: dict[int, list[Path]] = {}
        for root in roots:
            for path in _walk_root(root, files_only=True, max_scanned=options["max_scanned"]):
                try:
                    if _is_system_or_sensitive(path):
                        continue
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size <= 0:
                    continue
                size_groups.setdefault(stat.st_size, []).append(path)

        items: list[CleanupItem] = []
        group_index = 0
        for paths in size_groups.values():
            if len(paths) < 2:
                continue
            hash_groups: dict[str, list[Path]] = {}
            for path in paths:
                digest = _sha256_file(path)
                hash_groups.setdefault(digest, []).append(path)
            for digest, duplicates in hash_groups.items():
                if len(duplicates) < 2:
                    continue
                duplicates = sorted(duplicates, key=lambda item: str(item).casefold())
                group_index += 1
                duplicate_group_id = f"dup_{group_index}_{digest[:10]}"
                for duplicate in duplicates[1:]:
                    if _high_risk_file(duplicate):
                        action: CleanupAction = "review_only"
                        risk = "high"
                        reason = "Duplicate file matches a high-risk name or extension and must be reviewed manually."
                    else:
                        action = "trash_with_prompt"
                        risk = "medium"
                        reason = f"Duplicate of retained copy {duplicates[0].name}; recycle-bin approval required."
                    items.append(
                        _item_for_path(
                            duplicate,
                            category="duplicate",
                            action=action,
                            risk=risk,
                            confidence=0.98,
                            reason=reason,
                            duplicate_group_id=duplicate_group_id,
                        )
                    )
                    if len(items) >= options["limit"] > 0:
                        return items
        return items


def compute_plan_hash(plan: CleanupPlan) -> str:
    payload = {
        "roots": sorted(str(root) for root in plan.roots),
        "items": [
            {
                key: value
                for key, value in item.model_dump(mode="json").items()
                if key not in {"confidence"}
            }
            for item in sorted(plan.items, key=lambda value: (value.path.casefold(), value.action, value.id))
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cleanup_plan_id(content_hash: str) -> str:
    return f"cleanup_{content_hash[:16]}"


def is_direct_delete_allowed(path: str | Path) -> bool:
    candidate = normalize_path(path)
    if is_system_path(candidate) or is_sensitive_path(candidate) or _is_sensitive_by_name(candidate):
        return False
    if candidate.is_dir():
        try:
            return not any(candidate.iterdir())
        except OSError:
            return False
    return _is_recreatable_artifact(candidate) or _is_lengrvis_manifest_temp(candidate)


def _resolve_roots(raw_roots: Any, context: dict[str, Any]) -> list[Path]:
    roots = raw_roots if isinstance(raw_roots, list) else [raw_roots]
    allowed = _allowed(context)
    resolved: list[Path] = []
    for raw in roots:
        if not raw:
            continue
        path = resolve_authorized(raw, allowed)
        if path.exists():
            resolved.append(path)
    return sorted(set(resolved), key=lambda item: str(item).casefold())


def _allowed(context: dict[str, Any]) -> list[str]:
    return [str(path) for path in context.get("allowed_directories") or []]


def _scan_options(args: dict[str, Any]) -> dict[str, Any]:
    threshold_raw = args.get("threshold_mb", args.get("large_threshold_mb", 200))
    threshold_mb = float(200 if threshold_raw is None else threshold_raw)
    old_days_raw = args.get("older_than_days", 30)
    old_days = int(30 if old_days_raw is None else old_days_raw)
    return {
        "large_threshold_bytes": max(0, int(threshold_mb * 1024 * 1024)),
        "old_download_seconds": max(0, old_days * 24 * 60 * 60),
        "limit": max(0, int(args.get("limit") or 500)),
        "max_scanned": max(1, int(args.get("max_scanned") or 5000)),
    }


def _walk_root(root: Path, *, files_only: bool = False, max_scanned: int = 5000):
    if root.is_file():
        yield root
        return
    scanned = 0
    try:
        for path in root.rglob("*"):
            scanned += 1
            if scanned > max_scanned:
                break
            if path.is_symlink():
                continue
            if files_only and not path.is_file():
                continue
            yield path
    except OSError:
        return


def _classify_path(path: Path, options: dict[str, Any]) -> CleanupItem | None:
    try:
        if _is_system_or_sensitive(path):
            return _item_for_path(
                path,
                category="sensitive_or_system",
                action="review_only",
                risk="critical",
                confidence=1.0,
                reason="System, browser profile, credential, or sensitive paths are review-only.",
                is_dir=path.is_dir(),
            )
        if path.is_dir() and not any(path.iterdir()):
            return _item_for_path(
                path,
                category="empty_directory",
                action="delete_direct",
                risk="low",
                confidence=0.95,
                reason="Empty directories inside authorized roots are direct-delete cleanup items.",
                is_dir=True,
            )
        if path.is_dir():
            return None

        if is_direct_delete_allowed(path):
            return _item_for_path(
                path,
                category=_direct_category(path),
                action="delete_direct",
                risk="low",
                confidence=0.94,
                reason="Re-creatable cache, temp, build, or Lengrvis manifest temporary file.",
            )
        if _high_risk_file(path):
            return _item_for_path(
                path,
                category=_high_risk_category(path),
                action="review_only",
                risk="high",
                confidence=0.99,
                reason="Config, credential-like, browser profile, database, or source-control material requires manual review.",
            )

        stat = path.stat()
        if _is_download_cleanup_candidate(path, stat.st_mtime, stat.st_size, options):
            return _item_for_path(
                path,
                category=_category_for_extension(path),
                action="trash_with_prompt",
                risk="medium",
                confidence=0.86,
                reason="Old installer/archive or large media in Downloads should go to the recycle bin after approval.",
            )
        if _is_user_file_cleanup_candidate(path, stat.st_size, options):
            return _item_for_path(
                path,
                category=_category_for_extension(path),
                action="trash_with_prompt",
                risk="medium",
                confidence=0.74,
                reason="Large user file in Desktop/Documents/Pictures/Videos/Music requires recycle-bin approval.",
            )
        if stat.st_size >= options["large_threshold_bytes"] > 0:
            return _item_for_path(
                path,
                category="unknown_large_file",
                action="review_only",
                risk="medium",
                confidence=0.7,
                reason="Unknown large files are review-only until the user confirms they are disposable.",
            )
    except OSError:
        return None
    return None


def _item_for_path(
    path: Path,
    *,
    category: str,
    action: CleanupAction,
    risk: str,
    confidence: float,
    reason: str,
    duplicate_group_id: str | None = None,
    is_dir: bool | None = None,
) -> CleanupItem:
    try:
        stat = path.stat()
        size_bytes = 0 if path.is_dir() else int(stat.st_size)
        modified_at = float(stat.st_mtime)
        mtime_ns = int(stat.st_mtime_ns)
    except OSError:
        size_bytes = 0
        modified_at = 0
        mtime_ns = 0
    normalized = str(normalize_path(path))
    item_id = _stable_item_id(normalized, action, category, size_bytes, mtime_ns, duplicate_group_id)
    return CleanupItem(
        id=item_id,
        path=normalized,
        category=category,
        size_bytes=size_bytes,
        action=action,
        risk=risk,
        confidence=confidence,
        reason=reason,
        requires_approval=action in {"trash_with_prompt", "review_only"},
        duplicate_group_id=duplicate_group_id,
        modified_at=modified_at,
        mtime_ns=mtime_ns,
        is_dir=bool(path.is_dir() if is_dir is None else is_dir),
    )


def _stable_item_id(
    path: str,
    action: str,
    category: str,
    size_bytes: int,
    mtime_ns: int,
    duplicate_group_id: str | None,
) -> str:
    blob = json.dumps(
        {
            "path": path.casefold(),
            "action": action,
            "category": category,
            "size_bytes": size_bytes,
            # Millisecond precision: NTFS lazy timestamp flushes can change
            # st_mtime_ns between two stats of an unmodified file, which made
            # the item id (and approval binding) flaky under IO load.
            "mtime_ms": mtime_ns // 1_000_000,
            "duplicate_group_id": duplicate_group_id or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cleanup_item_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"


def _summarize_items(items: list[CleanupItem]) -> dict[str, Any]:
    direct = sum(item.size_bytes for item in items if item.action == "delete_direct")
    trash = sum(item.size_bytes for item in items if item.action == "trash_with_prompt")
    review = sum(item.size_bytes for item in items if item.action == "review_only")
    by_action: dict[str, int] = {"delete_direct": 0, "trash_with_prompt": 0, "review_only": 0}
    by_risk: dict[str, int] = {}
    for item in items:
        by_action[item.action] = by_action.get(item.action, 0) + 1
        by_risk[item.risk] = by_risk.get(item.risk, 0) + 1
    return {
        "total_reclaimable_bytes": direct + trash,
        "direct_delete_bytes": direct,
        "trash_bytes": trash,
        "review_only_bytes": review,
        "risk_summary": {
            "items": len(items),
            "by_action": by_action,
            "by_risk": by_risk,
            "requires_approval": sum(1 for item in items if item.requires_approval),
        },
    }


def _preview_item(item: CleanupItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "path": item.path,
        "action": item.action,
        "category": item.category,
        "size_bytes": item.size_bytes,
        "risk": item.risk,
        "reason": item.reason,
        "requires_approval": item.requires_approval,
        "duplicate_group_id": item.duplicate_group_id,
    }


def _resource_state(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=False)
    state: dict[str, Any] = {"path": str(resolved), "exists": resolved.exists()}
    if not resolved.exists():
        return state
    try:
        stat = resolved.stat()
    except OSError:
        return state
    state.update(
        {
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
            "size": 0 if resolved.is_dir() else stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "inode": getattr(stat, "st_ino", 0),
        }
    )
    return state


def _is_recreatable_artifact(path: Path) -> bool:
    normalized_parts = _normalized_parts(path)
    joined = "/".join(normalized_parts)
    if "node_modules/.cache" in joined:
        return True
    parent_parts = normalized_parts[:-1]
    broad_generated_dirs = DIRECT_DELETE_DIR_NAMES - {"temp", "tmp"}
    if any(part in broad_generated_dirs for part in parent_parts):
        return True
    if parent_parts and parent_parts[-1] in {"temp", "tmp"}:
        return True
    return path.suffix.casefold() in DIRECT_DELETE_FILE_SUFFIXES and any(
        part in broad_generated_dirs or part in {"logs", "log"} for part in parent_parts
    )


def _is_lengrvis_manifest_temp(path: Path) -> bool:
    name = path.name.casefold()
    parent = _normalized_key(path.parent)
    return (
        path.suffix.casefold() in {".tmp", ".temp"}
        and ("manifest" in name or "skill.yaml" in name)
        and (
            "lengrvis" in name
            or "lengrvis" in name
            or "lengrvis" in name
            or ".lengrvis" in parent
            or ".lengrvis" in parent
            or ".lengrvis" in parent
        )
    )


def _high_risk_file(path: Path) -> bool:
    return _is_sensitive_by_name(path) or path.suffix.casefold() in CONFIG_EXTENSIONS | DATABASE_EXTENSIONS


def _high_risk_category(path: Path) -> str:
    if path.suffix.casefold() in DATABASE_EXTENSIONS:
        return "database"
    if path.suffix.casefold() in CONFIG_EXTENSIONS:
        return "configuration"
    return "sensitive_name"


def _direct_category(path: Path) -> str:
    if _is_lengrvis_manifest_temp(path):
        return "lengrvis_manifest_temp"
    if path.suffix.casefold() in {".pyc", ".pyo"}:
        return "cache"
    return "recreatable_artifact"


def _category_for_extension(path: Path) -> str:
    ext = path.suffix.casefold()
    if ext in DOWNLOAD_REVIEW_EXTENSIONS:
        return "installer_or_archive"
    if ext in MEDIA_EXTENSIONS:
        return "media"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return "user_file"


def _is_download_cleanup_candidate(path: Path, modified_at: float, size_bytes: int, options: dict[str, Any]) -> bool:
    if "downloads" not in _normalized_parts(path):
        return False
    ext = path.suffix.casefold()
    old = time.time() - modified_at >= options["old_download_seconds"]
    large = size_bytes >= options["large_threshold_bytes"] > 0
    return (ext in DOWNLOAD_REVIEW_EXTENSIONS and old) or (ext in MEDIA_EXTENSIONS and (old or large))


def _is_user_file_cleanup_candidate(path: Path, size_bytes: int, options: dict[str, Any]) -> bool:
    parts = set(_normalized_parts(path))
    if not parts & (USER_REVIEW_DIRS - {"downloads"}):
        return False
    return size_bytes >= options["large_threshold_bytes"] > 0


def _is_source_repo_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any((path / marker).exists() for marker in SOURCE_ROOT_MARKERS)


def _is_system_or_sensitive(path: Path) -> bool:
    return is_system_path(path) or is_sensitive_path(path) or _is_sensitive_by_name(path) or _is_browser_profile_path(path)


def _is_sensitive_by_name(path: Path) -> bool:
    text = _normalized_key(path)
    name = path.name.casefold()
    return any(term in text or term in name for term in SENSITIVE_NAME_TERMS)


def _is_browser_profile_path(path: Path) -> bool:
    text = _normalized_key(path)
    return any(marker in text for marker in BROWSER_PROFILE_MARKERS)


def _normalized_parts(path: Path) -> list[str]:
    return [part.casefold() for part in path.parts if part]


def _normalized_key(path: str | Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_cleanup_event(event_type: str, payload: dict[str, Any], context: dict[str, Any]) -> None:
    try:
        record(event_type, "CleanupPlannerService", payload, task_id=context.get("task_id"))
    except Exception:
        return


def _claim_valid_cleanup_approval(args: dict[str, Any], context: dict[str, Any]) -> Approval:
    if args.get("approved") is not True:
        raise SecurityError("Cleanup live execution requires approved=true and a valid approved approval_id.")
    approval_id = str(args.get("approval_id") or "").strip()
    if not approval_id:
        raise SecurityError("Cleanup live execution requires a valid approved approval_id.")

    data = db.fetch_one("approvals", approval_id)
    if not data:
        raise SecurityError("Cleanup live execution requires an approval_id that exists in the approval database.")
    approval = Approval.model_validate(data)
    binding_error = _cleanup_approval_binding_error(approval, args, context, allow_consumed=False)
    if binding_error:
        raise SecurityError(binding_error)

    claimed = db.claim_approval_for_execution(approval.id, now_iso())
    if not claimed:
        raise SecurityError("Cleanup approval has already been consumed or is no longer approved.")
    claimed_approval = Approval.model_validate(claimed)
    binding_error = _cleanup_approval_binding_error(claimed_approval, args, context, allow_consumed=True)
    if binding_error:
        raise SecurityError(binding_error)
    return claimed_approval


def _cleanup_approval_binding_error(
    approval: Approval,
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    allow_consumed: bool,
) -> str:
    if approval.approval_type != "tool_call":
        return "Cleanup approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"Cleanup approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "Cleanup approval has already been consumed."
    required = {
        "tool_name": approval.tool_name,
        "args_binding_hmac": approval.args_binding_hmac,
        "preview_hmac": approval.preview_hmac,
        "settings_fingerprint": approval.settings_fingerprint,
        "permission_policy_version": approval.permission_policy_version,
        "tool_version": approval.tool_version,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return f"Cleanup approval lacks binding metadata: {', '.join(missing)}."
    if approval.tool_name != "file.cleanup_execute":
        return "Cleanup approval tool name does not match file.cleanup_execute."
    if approval.risk_level and approval.risk_level != RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value:
        return "Cleanup approval risk level does not match cleanup_execute."
    if approval.tool_version != "1":
        return "Cleanup approval tool version does not match cleanup_execute."

    expected_args = args_binding_hmac(
        "file.cleanup_execute",
        args,
        task_id=approval.task_id,
        step_id=approval.step_id,
    )
    if not _hmac_equal(approval.args_binding_hmac, expected_args):
        return "Cleanup approval arguments do not match the current cleanup_execute request."

    expected_preview = preview_hmac(approval.diff_preview)
    if not _hmac_equal(approval.preview_hmac, expected_preview):
        return "Cleanup approval preview was modified after review."

    expected_settings = settings_fingerprint(
        context.get("settings"),
        allowed_directories=_allowed(context),
    )
    if not _hmac_equal(approval.settings_fingerprint, expected_settings):
        return "Cleanup runtime settings changed after approval preview."

    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not _hmac_equal(approval.permission_policy_version, expected_policy):
        return "Cleanup permission policy changed after approval preview."
    return ""


def _hmac_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))
