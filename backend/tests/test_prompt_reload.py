from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.llm import prompts
from app.main import create_app


def test_load_prompt_returns_content(monkeypatch, tmp_path: Path):
    prompt = _write_prompt(monkeypatch, tmp_path, "hello.md", "Hello prompt")

    assert prompt.exists()
    assert prompts.load_prompt("hello.md") == "Hello prompt"


def test_load_prompt_caches(monkeypatch, tmp_path: Path):
    prompt = _write_prompt(monkeypatch, tmp_path, "cached.md", "first")
    prompt_path = str(prompt)
    original_stat = Path.stat
    stat_calls = 0

    def counting_stat(path: Path, *args, **kwargs):  # noqa: ANN001
        nonlocal stat_calls
        if str(path) == str(prompt_path):
            stat_calls += 1
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(prompts, "_dev_mode", lambda settings=None: False)
    monkeypatch.setattr(Path, "stat", counting_stat)

    assert prompts.load_prompt("cached.md") == "first"
    assert prompts.load_prompt("cached.md") == "first"
    assert stat_calls == 1


def test_load_prompt_reloads_on_mtime_change(monkeypatch, tmp_path: Path):
    prompt = _write_prompt(monkeypatch, tmp_path, "dynamic.md", "first")
    monkeypatch.setattr(prompts, "_dev_mode", lambda settings=None: True)

    assert prompts.load_prompt("dynamic.md") == "first"

    first_mtime = prompt.stat().st_mtime
    prompt.write_text("second", encoding="utf-8")
    os.utime(prompt, (first_mtime + 2, first_mtime + 2))

    assert prompts.load_prompt("dynamic.md") == "second"


def test_render_prompt_substitution(monkeypatch, tmp_path: Path):
    _write_prompt(monkeypatch, tmp_path, "template.md", "Hello $name from $place")

    assert (
        prompts.render_prompt("template.md", {"name": "Mavris", "place": "dev"})
        == "Hello Mavris from dev"
    )


def test_prompt_list_api(monkeypatch, tmp_path: Path):
    _write_prompt(monkeypatch, tmp_path, "listed.md", "API prompt")
    monkeypatch.setenv("MAVRIS_DEV", "1")

    response = TestClient(create_app()).get("/api/dev/prompts")

    assert response.status_code == 200
    payload = response.json()
    assert any(item["name"] == "listed.md" for item in payload["prompts"])
    listed = next(item for item in payload["prompts"] if item["name"] == "listed.md")
    assert listed["size"] == len("API prompt")
    assert "modified_at" in listed


def test_prompt_list_api_not_registered_in_production(monkeypatch):
    monkeypatch.delenv("MAVRIS_DEV", raising=False)
    monkeypatch.setenv("MARVIS_MODE", "privacy")

    response = TestClient(create_app()).get("/api/dev/prompts")

    assert response.status_code == 404


def _write_prompt(
    monkeypatch,
    tmp_path: Path,
    name: str,
    content: str,
) -> Path:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    prompt = prompt_dir / name
    prompt.write_text(content, encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)
    prompts.clear_prompt_cache()
    return prompt
