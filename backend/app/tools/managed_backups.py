from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_base_settings
from app.core.errors import SecurityError

MANAGED_BACKUP_SCHEMA_VERSION = 1
MANAGED_BACKUP_DIRNAME = "file-tool-backups"


def managed_backup_root() -> Path:
    data_dir = Path(get_base_settings().data_dir).expanduser().resolve(strict=False)
    root = data_dir / MANAGED_BACKUP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve(strict=False)
    try:
        if resolved == data_dir or resolved.is_relative_to(data_dir):
            return resolved
    except ValueError:
        pass
    raise SecurityError("Managed backup directory escapes the app data directory.")


def create_managed_backup(src: Path) -> dict[str, str | int | bool]:
    root = managed_backup_root()
    backup = root / _backup_filename(src)
    shutil.copy2(src, backup)
    return {
        "managed": True,
        "schema": MANAGED_BACKUP_SCHEMA_VERSION,
        "path": str(backup),
        "original_path": str(src),
    }


def resolve_managed_backup_path(path: str | Path) -> Path:
    root = managed_backup_root()
    candidate = Path(path).expanduser().resolve(strict=False)
    try:
        if candidate == root or candidate.is_relative_to(root):
            return candidate
    except ValueError:
        pass
    raise SecurityError("Backup path is outside the managed backup directory.")


def _backup_filename(src: Path) -> str:
    safe_name = _safe_filename(src.name)
    path_hash = hashlib.sha256(str(src.expanduser().resolve(strict=False)).encode("utf-8")).hexdigest()[:16]
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{safe_name}.{path_hash}.{timestamp}.{uuid.uuid4().hex}.bak"


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (safe or "file")[:80]
