from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_vector(text: str) -> list[float]:
    digest = [0.0] * 8
    for index, char in enumerate(text):
        digest[index % 8] += float(ord(char) % 251) / 251.0
    return digest


def _stable_id(url: str, title: str) -> str:
    digest = hashlib.sha256(f"{url}\n{title}".encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"mem_bookmark_{digest}"


def _bookmark_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            for current, dirs, names in os.walk(path):
                dirs[:] = [name for name in dirs if name.lower() not in {"cache", "code cache", "gpucache"}]
                for name in names:
                    if name == "Bookmarks" or name.lower().endswith(".json"):
                        files.append(Path(current) / name)
    return list(dict.fromkeys(files))


def _walk(node: dict[str, Any], folder: list[str], browser: str, profile: str, out: list[dict[str, Any]]) -> None:
    if node.get("type") == "url" and node.get("url"):
        out.append(
            {
                "title": str(node.get("name") or "").strip() or str(node.get("url")),
                "url": str(node.get("url")),
                "folder": "/".join(part for part in folder if part),
                "browser": browser,
                "profile": profile,
                "date_added": str(node.get("date_added") or ""),
            }
        )
        return
    children = node.get("children") or []
    next_folder = [*folder, str(node.get("name") or "").strip()] if node.get("name") else folder
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _walk(child, next_folder, browser, profile, out)


def _infer_source(path: Path) -> tuple[str, str]:
    parts = [part.lower() for part in path.parts]
    browser = "edge" if any("edge" in part for part in parts) else "chrome" if any("chrome" in part for part in parts) else "browser"
    profile = path.parent.name if path.parent.name else "default"
    return browser, profile


def _load_bookmarks(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    roots = data.get("roots") if isinstance(data, dict) else None
    if not isinstance(roots, dict):
        return []
    browser, profile = _infer_source(path)
    bookmarks: list[dict[str, Any]] = []
    for root_name, root_node in roots.items():
        if isinstance(root_node, dict):
            _walk(root_node, [str(root_name)], browser, profile, bookmarks)
    return bookmarks


def _init_memory_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                task_id TEXT,
                embedding BLOB,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
            """
        )


def _insert_memory(db_path: Path, bookmark: dict[str, Any]) -> None:
    content = (
        f"Bookmark: {bookmark['title']}\n"
        f"URL: {bookmark['url']}\n"
        f"Folder: {bookmark.get('folder', '')}\n"
        f"Browser: {bookmark.get('browser', 'browser')} {bookmark.get('profile', '')}".strip()
    )
    vector = _hash_vector(content)
    tags = ["bookmark", "browser", str(bookmark.get("browser") or "browser")]
    now = _now_iso()
    body = {
        "id": _stable_id(bookmark["url"], bookmark["title"]),
        "kind": "bookmark",
        "content": content,
        "tags": tags,
        "task_id": "",
        "source": "browser_bookmarks_import",
        "use_count": 0,
        "last_used_at": now,
        "embedding_dim": len(vector),
        "created_at": now,
        "embedding": vector,
        "bookmark": bookmark,
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO memories (id, kind, content, tags, task_id, embedding, data, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body["id"],
                body["kind"],
                body["content"],
                ",".join(tags),
                "",
                None,
                json.dumps(body, ensure_ascii=False),
                now,
                now,
            ),
        )


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    args = payload.get("args") or {}
    context = payload.get("context") or {}
    settings = context.get("settings") or {}
    dry_run = bool(args.get("dry_run", True))
    max_bookmarks = int(args.get("max_bookmarks") or 1000)
    paths = [str(path) for path in (args.get("paths") or []) if str(path).strip()]

    if not paths:
        print(json.dumps({"ok": False, "error": "paths is required for bookmark import or discovery."}))
        return 0

    bookmarks: list[dict[str, Any]] = []
    for path in _bookmark_files(paths):
        bookmarks.extend(_load_bookmarks(path))
        if len(bookmarks) >= max_bookmarks:
            bookmarks = bookmarks[:max_bookmarks]
            break

    if dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "count": len(bookmarks), "bookmarks": bookmarks[:50]}))
        return 0

    data_dir = str(settings.get("data_dir") or "").strip()
    if not data_dir:
        print(json.dumps({"ok": False, "error": "settings.data_dir is required to index bookmarks into Memory."}))
        return 0
    db_path = Path(data_dir) / "marvis.db"
    _init_memory_db(db_path)
    for bookmark in bookmarks:
        _insert_memory(db_path, bookmark)
    print(json.dumps({"ok": True, "imported": len(bookmarks), "count": len(bookmarks), "bookmarks": bookmarks[:50]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
