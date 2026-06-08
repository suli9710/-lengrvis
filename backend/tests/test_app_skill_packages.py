from __future__ import annotations

import asyncio
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.config import AppSettings
from app.core import db
from app.services.skill_service import SkillServiceError, import_skill, list_installed_skills
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
        "product-manifest-showcase",
    }.issubset(names)
    assert {
        "skill.windows_settings.preview",
        "skill.browser_bookmarks.import_to_memory",
        "skill.file_manager.batch_rename",
        "skill.file_manager.archive_by_rules",
        "skill.file_manager.zip_package",
        "skill.product_manifest.showcase",
    }.issubset(tools)


def test_product_manifest_showcase_proves_user_readable_permission_boundaries(test_data_dir: Path):
    package = next(
        package
        for package in scan_skill_directories([test_data_dir / "skills"])
        if package.definition.name == "product-manifest-showcase"
    )
    tool = package.definition.tools[0]
    runtime_tool = package.tool_definitions[0]

    assert package.safety_report.ok is True
    assert package.definition.effective_permissions(tool) == [
        "filesystem.read",
        "filesystem.write",
        "filesystem.delete",
        "ui.control",
        "network.external",
        "messaging.send",
    ]
    assert tool.supports_dry_run is True
    assert tool.smoke_tests[0].name == "product-manifest-boundaries-preview"
    assert "handoff" in tool.rollback_hint.lower() or "hand off" in tool.rollback_hint.lower()
    assert runtime_tool.capabilities == package.definition.effective_permissions(tool)
    assert {"read", "write", "delete", "control", "send"}.issubset(set(runtime_tool.effects))
    assert {"file", "directory", "application", "url", "message"}.issubset(set(runtime_tool.resource_kinds))
    assert runtime_tool.external_network is True
    assert runtime_tool.destructive is True


def test_skill_catalog_exposes_manifest_fields_for_product_manifest_cards(test_data_dir: Path):
    catalog = list_installed_skills(
        AppSettings(
            provider_name="mock",
            skill_directories=[str(test_data_dir / "skills")],
        )
    )
    skill = next(skill for skill in catalog["skills"] if skill["name"] == "product-manifest-showcase")
    tool = skill["tools"][0]

    assert skill["status"] == "ready"
    assert tool["permissions"] == [
        "filesystem.read",
        "filesystem.write",
        "filesystem.delete",
        "ui.control",
        "network.external",
        "messaging.send",
    ]
    assert tool["supports_dry_run"] is True
    assert tool["requires_authorized_path"] is True
    assert "Preview must list" in tool["rollback_hint"]


def test_zip_skill_import_rejects_manifest_schema_path_escape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    db.init_db()

    source = tmp_path / "zip_source" / "bad-zip-skill"
    source.mkdir(parents=True)
    (tmp_path / "outside.json").write_text('{"type":"object"}', encoding="utf-8")
    (source / "handler.py").write_text("print('{\"ok\": true}')\n", encoding="utf-8")
    (source / "skill.yaml").write_text(
        """
name: bad-zip-skill
version: "1.0"
agent_owner: FileAgent
permissions:
  - filesystem.read
tools:
  - name: skill.bad_zip.schema
    input_schema_path: ../outside.json
    execution:
      type: python
      entry: handler.py
""".strip(),
        encoding="utf-8",
    )
    zip_path = tmp_path / "bad-zip-skill.zip"
    with ZipFile(zip_path, "w") as archive:
        for path in source.rglob("*"):
            archive.write(path, Path(source.name) / path.relative_to(source))

    with pytest.raises(SkillServiceError, match="path traversal"):
        asyncio.run(import_skill(str(zip_path)))

    assert list((data_dir / "skills").iterdir()) == []


def test_zip_skill_import_rejects_zip_slip_member(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    db.init_db()

    zip_path = tmp_path / "zip-slip-skill.zip"
    with ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "zip-slip-skill/skill.yaml",
            """
name: zip-slip-skill
version: "1.0"
agent_owner: FileAgent
tools:
  - name: skill.zip_slip.noop
    execution:
      type: python
      entry: handler.py
""".strip(),
        )
        archive.writestr("zip-slip-skill/handler.py", "print('{\"ok\": true}')\n")
        archive.writestr("../evil.txt", "escaped")

    with pytest.raises(SkillServiceError, match="unsafe path"):
        asyncio.run(import_skill(str(zip_path)))

    assert not (tmp_path / "evil.txt").exists()
    install_dir = data_dir / "skills"
    assert not install_dir.exists() or list(install_dir.iterdir()) == []


def test_windows_settings_skill_previews_registry_and_powershell_plan(test_data_dir: Path):
    settings = AppSettings(
        provider_name="mock",
        skill_directories=[str(test_data_dir / "skills")],
        allow_unsafe_local_skill_execution=True,
    )
    registry = register_all_tools(settings=settings)
    tool = registry.get("skill.windows_settings.preview")

    result = tool.execute({"action": "set_theme", "theme": "dark", "dry_run": True}, {"settings": settings})

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["operations"][0]["operation"] == "set_theme"
    command = " ".join(result["operations"][0]["command"])
    assert "AppsUseLightTheme" in command
    assert "SystemUsesLightTheme" in command


def test_browser_bookmark_import_indexes_memory(monkeypatch, tmp_path: Path, test_data_dir: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
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
                                "name": "Lengrvis Docs",
                                "type": "url",
                                "url": "https://example.com/lengrvis",
                                "date_added": "1337",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = AppSettings(
        provider_name="mock",
        data_dir=str(data_dir),
        skill_directories=[str(test_data_dir / "skills")],
        allow_unsafe_local_skill_execution=True,
    )
    registry = register_all_tools(settings=settings)
    tool = registry.get("skill.browser_bookmarks.import_to_memory")

    dry_run = tool.execute({"paths": [str(bookmarks_path)], "dry_run": True}, {"settings": settings})
    result = tool.execute({"paths": [str(bookmarks_path)], "dry_run": False}, {"settings": settings})
    rows = db.list_memories(tags=["bookmark"], limit=20)

    assert dry_run["ok"] is True
    assert dry_run["count"] == 1
    assert result["ok"] is True
    assert result["imported"] == 1
    assert any("Lengrvis Docs" in row["content"] and "https://example.com/lengrvis" in row["content"] for row in rows)


def test_file_manager_skill_batch_rename_and_zip(tmp_path: Path, test_data_dir: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha", encoding="utf-8")
    (workspace / "beta.txt").write_text("beta", encoding="utf-8")
    settings = AppSettings(
        provider_name="mock",
        allowed_directories=[str(workspace)],
        skill_directories=[str(test_data_dir / "skills")],
        allow_unsafe_local_skill_execution=True,
    )
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
    settings = AppSettings(
        provider_name="mock",
        allowed_directories=[str(workspace)],
        skill_directories=[str(test_data_dir / "skills")],
        allow_unsafe_local_skill_execution=True,
    )
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
