from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.tools.registry import registry as tool_registry


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
        "import json, sys\npayload=json.loads(sys.stdin.read() or '{}')\nprint(json.dumps({'ok': True, 'echo': payload.get('args', {}).get('text', '')}))\n",
        encoding="utf-8",
    )
    return skill_root


def test_skill_routes_list_import_and_refresh(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MARVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MARVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
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
    assert payload["refresh"]["tool_count"] > 0
    assert tool_registry.get("skill.route_demo.echo").execute({"text": "ok"}, {})["echo"] == "ok"

    list_response = client.get("/api/skills")
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert list_response.json()["skills"][0]["status"] == "ready"

    refresh_response = client.post("/api/skills/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["skill_count"] == 1


def test_skill_route_imports_zip(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("MARVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MARVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MARVIS_SKILL_DIRECTORIES", str(data_dir / "skills"))
    db.init_db()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "skill.yaml").write_text("name: bad skill\nversion: 1\nagent_owner: FileAgent\ntools: []\n", encoding="utf-8")

    response = TestClient(create_app()).post("/api/skills/import", json={"path": str(bad)})

    assert response.status_code == 400
    assert "Invalid skill.yaml" in response.json()["error"]["message"]
