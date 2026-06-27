from __future__ import annotations

from pathlib import Path

from app import config_sources


def test_config_discovery_stops_at_project_root(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project"
    nested = project_root / "backend" / "app"
    nested.mkdir(parents=True)
    project_env = project_root / ".env"
    parent_env = tmp_path / ".env"
    project_env.write_text("LENGRVIS_MODE=efficiency\n", encoding="utf-8")
    parent_env.write_text("LENGRVIS_MODE=unsafe-parent\n", encoding="utf-8")
    monkeypatch.setattr(config_sources, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("LENGRVIS_ENV_FILE", raising=False)
    monkeypatch.delenv("LENGRVIS_CONFIG_DIR", raising=False)

    assert config_sources.find_config_file(".env", "LENGRVIS_ENV_FILE") == project_env


def test_config_discovery_does_not_walk_parents_outside_project(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project"
    outside_child = tmp_path / "outside" / "child"
    project_root.mkdir()
    outside_child.mkdir(parents=True)
    unsafe_parent_env = tmp_path / "outside" / ".env"
    unsafe_parent_env.write_text("LENGRVIS_MODE=unsafe-parent\n", encoding="utf-8")
    monkeypatch.setattr(config_sources, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(outside_child)
    monkeypatch.delenv("LENGRVIS_ENV_FILE", raising=False)
    monkeypatch.delenv("LENGRVIS_CONFIG_DIR", raising=False)

    assert config_sources.find_config_file(".env", "LENGRVIS_ENV_FILE") is None


def test_explicit_config_dir_does_not_walk_to_parent(monkeypatch, tmp_path: Path):
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()
    parent_config = tmp_path / "config.yaml"
    parent_config.write_text("llm:\n  mode: unsafe-parent\n", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("LENGRVIS_CONFIG_FILE", raising=False)

    assert config_sources.find_config_file("config.yaml", "LENGRVIS_CONFIG_FILE") is None
