from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


def resolve_workspace_path(
    root: str | Path | None = None,
    workspace_root: str | Path | None = None,
    base_dir: str | Path | None = None,
    base: str | Path | None = None,
    path: str | Path | None = None,
    candidate: str | Path | None = None,
    relative_path: str | Path | None = None,
) -> Path:
    workspace = Path(root or workspace_root or base_dir or base or ".").resolve(strict=False)
    raw = str(path or candidate or relative_path or "")
    if not raw:
        raise ValueError("Path is required.")
    decoded = unquote(raw).replace("\\", "/")
    if decoded.startswith("/") or Path(decoded).is_absolute() or ".." in Path(decoded).parts:
        raise PermissionError("Path escapes workspace.")
    resolved = (workspace / decoded).resolve(strict=False)
    if not (resolved == workspace or resolved.is_relative_to(workspace)):
        raise PermissionError("Path escapes workspace.")
    if resolved.exists():
        try:
            real = resolved.resolve(strict=True)
            if not real.is_relative_to(workspace):
                raise PermissionError("Symlink escapes workspace.")
        except FileNotFoundError:
            pass
    return resolved


validate_workspace_path = resolve_workspace_path
safe_join = resolve_workspace_path
ensure_safe_path = resolve_workspace_path

