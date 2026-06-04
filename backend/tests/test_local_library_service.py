from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config import AppSettings
from app.services import local_library_service


def test_local_library_only_uses_explicitly_allowed_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    pictures = tmp_path / "user" / "Pictures"
    documents = tmp_path / "user" / "Documents"
    workspace.mkdir()
    pictures.mkdir(parents=True)
    documents.mkdir(parents=True)
    (workspace / "project-shot.png").write_bytes(b"workspace")
    (pictures / "holiday.png").write_bytes(b"picture")
    (documents / "notes.png").write_bytes(b"document image")

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.delenv("HOME", raising=False)
    for key in local_library_service.ONEDRIVE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        local_library_service,
        "get_effective_settings",
        lambda: AppSettings(allowed_directories=[str(workspace)]),
    )

    result = local_library_service.list_local_library(section="gallery", limit=20)

    names = {item["name"] for item in result["items"]}
    assert names == {"project-shot.png"}
    assert str(workspace.resolve(strict=False)) in result["roots"]
    assert str(pictures.resolve(strict=False)) not in result["roots"]
    assert str(documents.resolve(strict=False)) not in result["roots"]


def test_local_library_preview_requires_explicit_authorized_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pictures = tmp_path / "user" / "Pictures"
    pictures.mkdir(parents=True)
    image = pictures / "preview.png"
    image.write_bytes(b"image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.delenv("HOME", raising=False)
    for key in local_library_service.ONEDRIVE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(local_library_service, "get_effective_settings", lambda: AppSettings(allowed_directories=[]))

    with pytest.raises(HTTPException) as exc_info:
        local_library_service.preview_local_image(str(image))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as outside_exc_info:
        local_library_service.preview_local_image(str(outside))
    assert outside_exc_info.value.status_code == 403
