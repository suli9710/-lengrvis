from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.core.paths import resolve_authorized
from app.llm.registry import get_effective_settings


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".md",
    ".csv",
}
APP_EXTENSIONS = {".exe", ".lnk", ".cmd", ".bat", ".ps1", ".msi"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | DOCUMENT_EXTENSIONS | APP_EXTENSIONS
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".codex_remote",
    "vendor",
    "dist",
    "build",
}
MAX_SCAN_FILES = 25000
MAX_SCAN_ENTRIES = 12000
MAX_SCAN_SECONDS = 4.0
MAX_CANDIDATE_ITEMS = 1200
MAX_SCAN_DEPTH = 6
USERPROFILE_ENV_KEYS = ("USERPROFILE", "HOME")
ONEDRIVE_ENV_KEYS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
DEFAULT_LIBRARY_DIR_NAMES = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "桌面", "文档", "下载", "图片", "视频", "音乐")
IMAGE_LIBRARY_DIR_NAMES = ("Desktop", "Downloads", "Pictures", "Videos", "桌面", "下载", "图片", "视频")
DOCUMENT_LIBRARY_DIR_NAMES = ("Desktop", "Documents", "Downloads", "桌面", "文档", "下载")
APP_LIBRARY_DIR_NAMES = ("Desktop", "Downloads", "桌面", "下载")


@dataclass(frozen=True)
class LibrarySection:
    id: str
    kind: str
    extensions: set[str]


@dataclass
class ScanBudget:
    started_at: float
    entries: int = 0
    timed_out: bool = False
    entry_limited: bool = False
    item_limited: bool = False


SECTIONS: dict[str, LibrarySection] = {
    "apps": LibrarySection("apps", "app", APP_EXTENSIONS),
    "documents": LibrarySection("documents", "document", DOCUMENT_EXTENSIONS),
    "document_ocr": LibrarySection("document_ocr", "document", DOCUMENT_EXTENSIONS),
    "papers": LibrarySection("papers", "document", DOCUMENT_EXTENSIONS),
    "courseware": LibrarySection("courseware", "document", DOCUMENT_EXTENSIONS),
    "reports": LibrarySection("reports", "document", DOCUMENT_EXTENSIONS),
    "gallery": LibrarySection("gallery", "image", IMAGE_EXTENSIONS),
    "image_ocr": LibrarySection("image_ocr", "image", IMAGE_EXTENSIONS),
    "people": LibrarySection("people", "image", IMAGE_EXTENSIONS),
    "places": LibrarySection("places", "image", IMAGE_EXTENSIONS),
    "timeline": LibrarySection("timeline", "image", IMAGE_EXTENSIONS),
}


def list_local_library(section: str = "gallery", query: str = "", limit: int = 240) -> dict:
    settings = get_effective_settings()
    section_meta = SECTIONS.get(section, SECTIONS["gallery"])
    library_roots = _library_roots(list(settings.allowed_directories or []), section=section_meta.id)
    safe_limit = max(1, min(int(limit or 240), 500))
    normalized_query = query.strip().lower()
    scan_budget = ScanBudget(started_at=time.monotonic())

    items = []
    scanned = 0
    for path in _iter_library_files(library_roots, section_meta.extensions, scan_budget):
        scanned += 1
        if scanned > MAX_SCAN_FILES:
            break
        if normalized_query and normalized_query not in path.name.lower() and normalized_query not in str(path).lower():
            continue
        if not _matches_section(path, section_meta.id):
            continue
        items.append(_library_item(path, section_meta.kind))
        if len(items) >= MAX_CANDIDATE_ITEMS:
            scan_budget.item_limited = True
            break

    items.sort(key=lambda item: item["modified_at"] or 0, reverse=True)
    limited_items = items[:safe_limit]
    return {
        "section": section_meta.id,
        "roots": library_roots,
        "items": limited_items,
        "count": len(limited_items),
        "total": len(items),
        "scanned": scanned,
        "truncated": (
            len(items) > safe_limit
            or scanned > MAX_SCAN_FILES
            or scan_budget.timed_out
            or scan_budget.entry_limited
            or scan_budget.item_limited
        ),
        "stats": _stats(limited_items),
    }


def preview_local_image(path: str) -> FileResponse:
    settings = get_effective_settings()
    library_roots = _library_roots(list(settings.allowed_directories or []))
    try:
        resolved = resolve_authorized(path, library_roots)
    except Exception as exc:  # noqa: BLE001 - convert security/path failures to HTTP responses.
        raise HTTPException(status_code=403, detail="未授权访问该文件") from exc
    if not resolved.is_file() or resolved.suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="图片不存在")
    mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    return FileResponse(resolved, media_type=mime_type, filename=resolved.name)


def _library_roots(allowed_directories: list[str], section: str | None = None) -> list[str]:
    roots: list[Path] = []
    for raw in allowed_directories:
        _add_existing_root(raw, roots)

    user_bases: list[Path] = []
    for env_key in USERPROFILE_ENV_KEYS:
        raw_base = os.environ.get(env_key)
        if raw_base:
            user_bases.append(Path(raw_base).expanduser())
    for env_key in ONEDRIVE_ENV_KEYS:
        raw_base = os.environ.get(env_key)
        if raw_base:
            user_bases.append(Path(raw_base).expanduser())

    for base in user_bases:
        for dirname in _known_dir_names_for_section(section):
            _add_existing_root(base / dirname, roots)

    return [str(root) for root in roots]


def _known_dir_names_for_section(section: str | None) -> tuple[str, ...]:
    section_meta = SECTIONS.get(section or "")
    if not section_meta:
        return DEFAULT_LIBRARY_DIR_NAMES
    if section_meta.kind == "image":
        return IMAGE_LIBRARY_DIR_NAMES
    if section_meta.kind == "app":
        return APP_LIBRARY_DIR_NAMES
    return DOCUMENT_LIBRARY_DIR_NAMES


def _add_existing_root(raw_root: str | Path, roots: list[Path]) -> None:
    try:
        root = Path(raw_root).expanduser().resolve(strict=False)
    except OSError:
        return
    if not root.exists():
        return

    remaining: list[Path] = []
    for existing in roots:
        try:
            if root == existing or root.is_relative_to(existing):
                return
            if existing.is_relative_to(root):
                continue
        except ValueError:
            remaining.append(existing)
            continue
        remaining.append(existing)
    roots[:] = remaining
    roots.append(root)


def _iter_library_files(allowed_directories: list[str], extensions: set[str], budget: ScanBudget) -> Iterable[Path]:
    seen: set[str] = set()
    for raw_root in allowed_directories:
        if _scan_budget_exhausted(budget):
            break
        try:
            root = resolve_authorized(raw_root, allowed_directories)
        except Exception:
            continue
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in extensions and _remember(root, seen):
                yield root
            continue
        stack = [(root, 0)]
        while stack:
            if _scan_budget_exhausted(budget):
                break
            current, depth = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        budget.entries += 1
                        if _scan_budget_exhausted(budget):
                            break
                        if _should_skip_entry(entry.name):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if depth < MAX_SCAN_DEPTH:
                                    stack.append((Path(entry.path), depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                path = Path(entry.path)
                                if path.suffix.lower() in extensions and _remember(path, seen):
                                    yield path
                        except OSError:
                            continue
            except OSError:
                continue


def _scan_budget_exhausted(budget: ScanBudget) -> bool:
    if budget.entries >= MAX_SCAN_ENTRIES:
        budget.entry_limited = True
        return True
    if time.monotonic() - budget.started_at >= MAX_SCAN_SECONDS:
        budget.timed_out = True
        return True
    return False


def _should_skip_entry(name: str) -> bool:
    lower_name = name.lower()
    return name.startswith("~$") or lower_name in SKIP_DIR_NAMES or lower_name in {"appdata", "$recycle.bin"}


def _remember(path: Path, seen: set[str]) -> bool:
    key = str(path).lower()
    if key in seen:
        return False
    seen.add(key)
    return True


def _matches_section(path: Path, section: str) -> bool:
    name = path.stem.lower()
    suffix = path.suffix.lower()
    if section == "papers":
        return suffix == ".pdf" or any(token in name for token in ("paper", "thesis", "论文", "期刊", "journal"))
    if section == "courseware":
        return suffix in {".ppt", ".pptx"} or any(token in name for token in ("course", "lesson", "lecture", "课件", "课程", "讲义"))
    if section == "reports":
        return any(token in name for token in ("report", "weekly", "monthly", "summary", "报告", "周报", "月报", "总结"))
    return True


def _library_item(path: Path, kind: str) -> dict:
    try:
        stat = path.stat()
    except OSError:
        stat = None
    mime_type = mimetypes.guess_type(str(path))[0] or ""
    item = {
        "id": _stable_id(path),
        "path": str(path),
        "name": path.name,
        "parent": str(path.parent),
        "kind": kind,
        "extension": path.suffix.lower(),
        "mime_type": mime_type,
        "size": stat.st_size if stat else 0,
        "created_at": stat.st_ctime if stat else 0,
        "modified_at": stat.st_mtime if stat else 0,
        "preview_url": f"/api/library/preview?path={_url_token(str(path))}" if kind == "image" else "",
        "group_label": _group_label(path, kind),
    }
    if kind == "image":
        width, height = _image_dimensions(path)
        item["width"] = width
        item["height"] = height
    return item


def _stable_id(path: Path) -> str:
    raw = str(path).encode("utf-8", errors="ignore")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _url_token(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _group_label(path: Path, kind: str) -> str:
    if kind == "image":
        return "本地图片"
    if kind == "app":
        return "本地应用"
    suffix = path.suffix.lower()
    if suffix in {".ppt", ".pptx"}:
        return "课件"
    if suffix == ".pdf":
        return "PDF"
    if suffix in {".doc", ".docx"}:
        return "文档"
    if suffix in {".xls", ".xlsx", ".csv"}:
        return "表格"
    return "文件"


def _image_dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _stats(items: list[dict]) -> dict:
    total_size = sum(int(item.get("size") or 0) for item in items)
    by_extension: dict[str, int] = {}
    for item in items:
        ext = str(item.get("extension") or "")
        by_extension[ext] = by_extension.get(ext, 0) + 1
    return {"size": total_size, "by_extension": by_extension}
