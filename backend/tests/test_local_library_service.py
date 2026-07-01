from __future__ import annotations

import builtins
import json
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.core.errors import SecurityError
from app.main import app
from app.services import local_library_service


@pytest.fixture(autouse=True)
def local_library_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))


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
    assert result["index_status"]["status"] == "empty"
    assert result["scope_summary"]["display_label"] == "1 个授权范围"
    assert result["scope_summary"]["root_labels"] == ["授权范围 1"]
    assert result["scope_summary"]["has_authorized_roots"] is True
    assert result["scope_summary"]["raw_paths_available_for_local_actions"] is True
    assert result["scope_summary"]["shareable_summary_has_raw_paths"] is False
    assert result["items"][0]["path_label"] == "project-shot.png"
    assert result["items"][0]["parent_label"] == "授权范围 1"
    shareable_dump = json.dumps(
        {
            "scope_summary": result["scope_summary"],
            "item_labels": [
                {
                    "path_label": item["path_label"],
                    "parent_label": item["parent_label"],
                    "group_label": item["group_label"],
                }
                for item in result["items"]
            ],
        },
        ensure_ascii=False,
    )
    assert str(workspace.resolve(strict=False)) not in shareable_dump
    assert str(pictures.resolve(strict=False)) not in shareable_dump
    assert str(documents.resolve(strict=False)) not in shareable_dump


def test_local_library_empty_scope_summary_is_beginner_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    documents = tmp_path / "private-user-name" / "Documents"
    documents.mkdir(parents=True)
    (documents / "payroll-private.pdf").write_text("private", encoding="utf-8")

    monkeypatch.setenv("USERPROFILE", str(tmp_path / "private-user-name"))
    monkeypatch.delenv("HOME", raising=False)
    for key in local_library_service.ONEDRIVE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(local_library_service, "get_effective_settings", lambda: AppSettings(allowed_directories=[]))

    result = local_library_service.list_local_library(section="documents", limit=20)
    shareable_dump = json.dumps(result["scope_summary"], ensure_ascii=False)

    assert result["items"] == []
    assert result["roots"] == []
    assert result["scope_summary"] == {
        "root_count": 0,
        "root_labels": [],
        "has_authorized_roots": False,
        "display_label": "未选择授权目录",
        "raw_paths_available_for_local_actions": True,
        "shareable_summary_has_raw_paths": False,
    }
    assert "private-user-name" not in shareable_dump
    assert "payroll-private.pdf" not in shareable_dump


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


@pytest.mark.parametrize("exc", [SecurityError("blocked"), OSError("bad path"), ValueError("bad path")])
def test_local_library_preview_converts_expected_path_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    monkeypatch.setattr(local_library_service, "get_effective_settings", lambda: AppSettings(allowed_directories=["."]))
    monkeypatch.setattr(
        local_library_service, "resolve_authorized", lambda *_args, **_kwargs: (_ for _ in ()).throw(exc)
    )

    with pytest.raises(HTTPException) as exc_info:
        local_library_service.preview_local_image("preview.png")

    assert exc_info.value.status_code == 403


def test_local_library_preview_does_not_swallow_unexpected_path_bugs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_library_service, "get_effective_settings", lambda: AppSettings(allowed_directories=["."]))
    monkeypatch.setattr(
        local_library_service,
        "resolve_authorized",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("path resolver bug")),
    )

    with pytest.raises(RuntimeError, match="path resolver bug"):
        local_library_service.preview_local_image("preview.png")


def test_iter_library_files_skips_expected_root_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_library_service,
        "resolve_authorized",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SecurityError("blocked root")),
    )
    budget = local_library_service.ScanBudget(started_at=local_library_service.time.monotonic())

    assert list(local_library_service._iter_library_files(["bad-root"], {".png"}, budget)) == []


def test_iter_library_files_does_not_swallow_unexpected_root_bugs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_library_service,
        "resolve_authorized",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("root resolver bug")),
    )
    budget = local_library_service.ScanBudget(started_at=local_library_service.time.monotonic())

    with pytest.raises(RuntimeError, match="root resolver bug"):
        list(local_library_service._iter_library_files(["bad-root"], {".png"}, budget))


def test_image_dimensions_degrade_for_expected_pillow_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_import = builtins.__import__

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "PIL":
            raise ImportError("pillow unavailable")
        return real_import(name, global_vars, local_vars, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert local_library_service._image_dimensions(tmp_path / "sample.png") == (0, 0)


def test_image_dimensions_does_not_swallow_unexpected_pillow_bugs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_import = builtins.__import__

    class BuggyImage:
        @staticmethod
        def open(_path: Path):
            raise RuntimeError("pillow bug")

    def fake_import(name, global_vars=None, local_vars=None, fromlist=(), level=0):
        if name == "PIL":
            module = types.ModuleType("PIL")
            module.Image = BuggyImage
            return module
        return real_import(name, global_vars, local_vars, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="pillow bug"):
        local_library_service._image_dimensions(tmp_path / "sample.png")


def test_local_library_preview_uses_short_lived_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "preview.png"
    image.write_bytes(b"image")

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setattr(
        local_library_service,
        "get_effective_settings",
        lambda: AppSettings(allowed_directories=[str(workspace)]),
    )

    result = local_library_service.list_local_library(section="gallery", limit=20)
    preview_url = result["items"][0]["preview_url"]
    query = parse_qs(urlparse(preview_url).query)

    assert query["path"] == [str(image)]
    assert query["expires"]
    assert query["signature"]

    client = TestClient(app, client=("127.0.0.1", 50100))
    assert client.get("/api/library/preview", params={"path": str(image)}).status_code == 401
    assert client.get(preview_url).status_code == 200
    assert client.post(preview_url).status_code == 401
    assert client.get(preview_url.replace("signature=", "signature=bad")).status_code == 401
