from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.services import skill_service
from app.skills.loader import load_skill_package
from app.tools.registry import registry as tool_registry


def test_extract_zip_safely_rejects_zip_bomb(tmp_path: Path):
    # SEC-007 regression: a tiny archive that expands to a huge / absurd ratio
    # must be rejected before anything is written to disk.
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("skill.yaml", b"\0" * (8 * 1024 * 1024))
    with pytest.raises(skill_service.SkillServiceError):
        skill_service._extract_zip_safely(bomb, tmp_path / "out")
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_extract_zip_safely_rejects_too_many_members(tmp_path: Path):
    crowded = tmp_path / "crowded.zip"
    with zipfile.ZipFile(crowded, "w") as archive:
        for index in range(skill_service.SKILL_ZIP_MAX_MEMBERS + 1):
            archive.writestr(f"f{index}.txt", b"x")
    with pytest.raises(skill_service.SkillServiceError):
        skill_service._extract_zip_safely(crowded, tmp_path / "out2")


def test_refresh_runtime_registry_logs_mcp_definition_failures(monkeypatch, caplog, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    db.init_db()
    recorded: list[tuple[str, str, dict]] = []

    class FailingMcpRegistry:
        def load_from_settings(self, settings):  # noqa: ARG002
            return None

        async def adapt_to_tool_definitions(self):
            raise RuntimeError("mcp exploded")

    monkeypatch.setattr(skill_service, "get_mcp_registry", lambda: FailingMcpRegistry())
    monkeypatch.setattr(skill_service, "register_all_tools", lambda **kwargs: None)
    monkeypatch.setattr(skill_service, "tool_registry", SimpleNamespace(list=lambda: []))
    monkeypatch.setattr(
        skill_service,
        "record",
        lambda event, actor, payload, **kwargs: recorded.append((event, actor, payload)),
    )

    with caplog.at_level(logging.WARNING, logger=skill_service.logger.name):
        result = asyncio.run(skill_service.refresh_runtime_registry())

    assert result["ok"] is True
    assert "skill.refresh_runtime_registry.mcp_definitions" in caplog.text
    assert "mcp exploded" in caplog.text
    assert recorded == [("mcp.refresh_load_failed", "SkillService", {"error": "mcp exploded"})]


def _write_skill(root: Path, name: str = "route-demo") -> Path:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    (skill_root / "skill.yaml").write_text(
        f"""
name: {name}
version: "1.0.0"
agent_owner: FileAgent
risk: R0_READ_ONLY
tools:
  - name: skill.{name.replace("-", "_")}.echo
    description: Echo text from route demo.
    execution:
      type: python
      entry: echo.py
""".strip(),
        encoding="utf-8",
    )
    (skill_root / "echo.py").write_text(
        "import json, sys\n"
        "payload=json.loads(sys.stdin.read() or '{}')\n"
        "print(json.dumps({'ok': True, 'echo': payload.get('args', {}).get('text', '')}))\n",
        encoding="utf-8",
    )
    return skill_root


def test_skill_routes_list_import_and_refresh(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.delenv("LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION", raising=False)
    db.init_db()

    source = _write_skill(tmp_path / "source")
    client = TestClient(create_app())

    empty_response = client.get("/api/skills")
    assert empty_response.status_code == 200
    assert empty_response.json()["count"] == 0

    import_response = client.post("/api/skills/import", json={"path": str(source)})
    assert import_response.status_code == 200
    payload = import_response.json()
    assert payload["skill"]["name"] == "route-demo"
    assert payload["skill"]["signature"]["status"] == "unsigned"
    assert payload["upgrade_diff"]["kind"] == "new_install"
    assert payload["upgrade_diff"]["signature_status"] == "unsigned"
    assert payload["refresh"]["tool_count"] > 0
    execution_result = tool_registry.get("skill.route_demo.echo").execute({"text": "ok"}, {})
    assert execution_result["policy"] == "local_skill_execution_disabled"

    (source / "skill.yaml").write_text(
        """
name: route-demo
version: "1.0.0"
agent_owner: FileAgent
risk: R0_READ_ONLY
permissions:
  - filesystem.read
tools:
  - name: skill.route_demo.echo
    description: Echo text from route demo.
    execution:
      type: python
      entry: echo.py
""".strip(),
        encoding="utf-8",
    )
    reimport_response = client.post("/api/skills/import", json={"path": str(source)})
    assert reimport_response.status_code == 200
    reimport_payload = reimport_response.json()
    assert reimport_payload["upgrade_diff"]["kind"] == "upgrade_or_replace"
    assert reimport_payload["upgrade_diff"]["permission_changes"] == [
        {"tool": "skill.route_demo.echo", "from": ["legacy.unspecified"], "to": ["filesystem.read"]}
    ]

    list_response = client.get("/api/skills")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert list_response.json()["skills"][0]["status"] == "ready"

    refresh_response = client.post("/api/skills/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["skill_count"] == 1


def test_skill_route_imports_product_manifest_showcase_into_real_catalog(
    monkeypatch,
    tmp_path: Path,
    test_data_dir: Path,
):
    data_dir = tmp_path / "data"
    install_dir = data_dir / "skills"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(install_dir))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(test_data_dir))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.delenv("LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION", raising=False)
    db.init_db()

    source = test_data_dir / "skills" / "product_manifest_showcase"
    client = TestClient(create_app())

    import_response = client.post("/api/skills/import", json={"path": str(source)})
    assert import_response.status_code == 200
    import_payload = import_response.json()
    assert import_payload["skill"]["name"] == "product-manifest-showcase"
    assert import_payload["refresh"]["skill_count"] == 1

    list_response = client.get("/api/skills")
    assert list_response.status_code == 200
    catalog = list_response.json()
    assert catalog["count"] == 1
    assert Path(catalog["install_directory"]).resolve(strict=False) == install_dir.resolve(strict=False)

    skill = next(skill for skill in catalog["skills"] if skill["name"] == "product-manifest-showcase")
    assert skill["version"] == "0.1.0"
    assert skill["agent_owner"] == "AppAgent"
    assert skill["risk"] == "R3_DESTRUCTIVE_OR_SYSTEM"
    assert skill["status"] == "ready"
    assert skill["error"] == ""
    assert skill["safety"]["issues"] == []
    assert skill["root"] == import_payload["skill"]["root"]
    assert skill["manifest_path"] == import_payload["skill"]["manifest_path"]
    assert source.resolve(strict=True) != Path(skill["root"]).resolve(strict=True)
    assert install_dir.resolve(strict=False) in Path(skill["manifest_path"]).resolve(strict=True).parents

    tool = skill["tools"][0]
    assert tool["name"] == "skill.product_manifest.showcase"
    assert tool["agent_owner"] == "AppAgent"
    assert tool["risk"] == "R3_DESTRUCTIVE_OR_SYSTEM"
    assert tool["permissions"] == [
        "filesystem.read",
        "filesystem.write",
        "filesystem.delete",
        "ui.control",
        "network.external",
        "messaging.send",
    ]
    assert "legacy.unspecified" not in tool["permissions"]
    assert tool["supports_dry_run"] is True
    assert tool["requires_authorized_path"] is True
    assert tool["execution_type"] == "python"
    assert tool["entry"] == "handlers/intent.py"
    assert {"path", "message", "endpoint", "dry_run"}.issubset(tool["input_schema"]["properties"])
    assert tool["input_schema"]["required"] == ["path", "message"]
    assert tool["smoke_tests"] == [
        {
            "name": "product-manifest-boundaries-preview",
            "description": (
                "Dry-run preview lists file read/write, UI, network, messaging, delete, "
                "and rollback or handoff boundaries."
            ),
            "has_args": True,
            "arg_keys": ["dry_run", "endpoint", "message", "path"],
            "expected_keys": ["dry_run", "ok"],
        }
    ]
    rendered = list_response.text
    assert "sample.txt" not in rendered
    assert "https://example.invalid/webhook" not in rendered
    assert '"message":"hello"' not in rendered
    assert "Preview must list each file, UI, network, messaging, and delete operation" in tool["rollback_hint"]
    assert "hand off to the user" in tool["rollback_hint"]

    installed_package = load_skill_package(Path(skill["root"]))
    installed_tool = installed_package.definition.tools[0]
    assert installed_tool.smoke_tests[0].name == "product-manifest-boundaries-preview"
    assert installed_tool.smoke_tests[0].args["dry_run"] is True
    assert installed_tool.smoke_tests[0].expected == {"ok": True, "dry_run": True}
    assert installed_package.definition.effective_permissions(installed_tool) == tool["permissions"]


def test_skill_route_imports_zip(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    source = _write_skill(tmp_path / "source", name="zip-demo")
    zip_path = tmp_path / "zip-demo.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in source.rglob("*"):
            archive.write(path, Path(source.name) / path.relative_to(source))

    response = TestClient(create_app()).post("/api/skills/import", json={"path": str(zip_path)})

    assert response.status_code == 200
    assert response.json()["skill"]["name"] == "zip-demo"


def test_skill_route_reports_invalid_import(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "skill.yaml").write_text(
        "name: bad skill\nversion: 1\nagent_owner: FileAgent\ntools: []\n",
        encoding="utf-8",
    )

    response = TestClient(create_app()).post("/api/skills/import", json={"path": str(bad)})

    assert response.status_code == 400
    assert "Invalid skill.yaml" in response.json()["error"]["message"]


def test_skill_route_rejects_system_path_import(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    response = TestClient(create_app()).post(
        "/api/skills/import",
        json={"path": "C:/Windows/System32/drivers/etc/hosts"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "skill_import_path_denied"


def test_skill_route_rejects_import_outside_whitelist(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    skill_root = outside / "outside-skill"
    skill_root.mkdir()
    (skill_root / "skill.yaml").write_text(
        """
name: outside-skill
version: "1.0"
agent_owner: FileAgent
tools: []
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "allowed"))
    db.init_db()

    response = TestClient(create_app()).post("/api/skills/import", json={"path": str(skill_root)})

    assert response.status_code == 400
    assert "outside authorized directories" in response.json()["error"]["message"]
