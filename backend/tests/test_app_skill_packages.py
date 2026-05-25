from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from app.config import AppSettings
from app.core import db
from app.skills.loader import scan_skill_directories
from app.tools.registry import register_all_tools


def test_app_skill_packages_load_from_test_data(test_data_dir: Path):
    packages = scan_skill_directories([test_data_dir / "skills"])
    names = {package.definition.name for package in packages}
    tools = {tool.name for package in packages for tool in package.tool_definitions}

    assert {
        "windows-settings-automation",
        "browser-bookmarks-import",
        "file-manager-enhanced",
    }.issubset(names)
    assert {
        "skill.windows_settings.preview",
        "skill.browser_bookmarks.import_to_memory",
        "skill.file_manager.batch_rename",
        "skill.file_manager.archive_by_rules",
        "skill.file_manager.zip_package",
    }.issubset(tools)


def test_windows_settings_skill_previews_registry_and_powershell_plan(test_data_dir: Path):
    registry = register_all_tools(skill_directories=[str(test_data_dir / "skills")])
    tool = registry.get("skill.windows_settings.preview")

    result = tool.execute({"action": "set_theme", "theme": "dark", "dry_run": True}, {})

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["operations"][0]["operation"] == "set_theme"
    command = " ".join(result["operations"][0]["command"])
    assert "AppsUseLightTheme" in command
    assert "SystemUsesLightTheme" in command


def test_browser_bookmark_import_indexes_memory(monkeypatch, tmp_path: Path, test_data_dir: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MARVIS_DATA_DIR", str(data_dir))
    db.init_db()
    bookmarks_path = tmp_path / "Bookmarks"
    bookmarks_path.write_text(
        json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "name": "Bookmarks Bar",
                        "type": "folder",
                        "children": [
                            {
                                "name": "Mavris Docs",
                                "type": "url",
                                "url": "https://example.com/mavris",
                                "date_added": "1337",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings(provider_name="mock", data_dir=str(data_dir), skill_directories=[str(test_data_dir / "skills")])
    registry = register_all_tools(settings=settings)
    tool = registry.get("skill.browser_bookmarks.import_to_memory")

    dry_run = tool.execute({"paths": [str(bookmarks_path)], "dry_run": True}, {"settings": settings})
    result = tool.execute({"paths": [str(bookmarks_path)], "dry_run": False}, {"settings": settings})
    rows = db.list_memories(tags=["bookmark"], limit=20)

    assert dry_run["ok"] is True
    assert dry_run["count"] == 1
    assert result["ok"] is True
    assert result["imported"] == 1
    assert any("Mavris Docs" in row["content"] and "https://example.com/mavris" in row["content"] for row in rows)


def test_file_manager_skill_batch_rename_and_zip(tmp_path: Path, test_data_dir: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (workspace / "beta.txt").write_text("beta", encoding="utf-8")
    settings = AppSettings(provider_name="mock", allowed_directories=[str(workspace)], skill_directories=[str(test_data_dir / "skills")])
    registry = register_all_tools(settings=settings)
    context = {"settings": settings, "allowed_directories": settings.allowed_directories}

    rename = registry.get("skill.file_manager.batch_rename").execute(
        {
            "directory": str(workspace),
            "match_glob": "*.txt",
            "template": "note-{n:02d}{ext}",
            "dry_run": False,
        },
        context,
    )
    zip_result = registry.get("skill.file_manager.zip_package").execute(
        {
            "source_paths": [str(workspace)],
            "output_zip": str(workspace / "bundle.zip"),
            "include_globs": ["*.txt"],
            "dry_run": False,
        },
        context,
    )

    assert rename["ok"] is True
    assert rename["renamed"] == 2
    assert (workspace / "note-01.txt").exists()
    assert (workspace / "note-02.txt").exists()
    assert zip_result["ok"] is True
    assert zip_result["packaged"] == 2
    with ZipFile(workspace / "bundle.zip") as archive:
        assert sorted(archive.namelist()) == ["note-01.txt", "note-02.txt"]


def test_file_manager_skill_archive_by_rules(tmp_path: Path, test_data_dir: Path):
    workspace = tmp_path / "workspace"
    downloads = workspace / "downloads"
    archive = workspace / "archive"
    downloads.mkdir(parents=True)
    (downloads / "invoice.pdf").write_text("invoice", encoding="utf-8")
    (downloads / "photo.jpg").write_text("photo", encoding="utf-8")
    settings = AppSettings(provider_name="mock", allowed_directories=[str(workspace)], skill_directories=[str(test_data_dir / "skills")])
    registry = register_all_tools(settings=settings)

    result = registry.get("skill.file_manager.archive_by_rules").execute(
        {
            "source_dir": str(downloads),
            "archive_dir": str(archive),
            "rules": [
                {"name": "documents", "glob": "*.pdf", "destination": "docs"},
                {"name": "images", "glob": "*.jpg", "destination": "images"},
            ],
            "dry_run": False,
        },
        {"settings": settings, "allowed_directories": settings.allowed_directories},
    )

    assert result["ok"] is True
    assert result["moved"] == 2
    assert (archive / "docs" / "invoice.pdf").exists()
    assert (archive / "images" / "photo.jpg").exists()
