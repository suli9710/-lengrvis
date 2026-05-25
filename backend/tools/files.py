from __future__ import annotations

from pathlib import Path

from backend.security.paths import resolve_workspace_path


def read_file(root: Path | str | None = None, workspace_root: Path | str | None = None, path: str = "") -> str:
    workspace = Path(root or workspace_root or ".")
    resolved = resolve_workspace_path(root=workspace, path=path)
    return resolved.read_text(encoding="utf-8")


read_workspace_file = read_file
read_text = read_file

