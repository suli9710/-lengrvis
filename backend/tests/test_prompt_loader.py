from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agents.app_agent import AppAgent
from app.agents.browser_agent import BrowserAgent
from app.agents.computer_agent import ComputerAgent
from app.agents.document_agent import DocumentAgent
from app.agents.file_agent import FileAgent
from app.agents.human_gate_agent import HumanGateAgent
from app.agents.memory_agent import MemoryAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.safety_review_agent import SafetyReviewAgent
from app.agents.search_agent import SearchAgent
from app.llm import prompts


@pytest.fixture(autouse=True)
def _clear_prompt_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MAVRIS_DEV", raising=False)
    monkeypatch.delenv("MARVIS_DEV", raising=False)
    monkeypatch.delenv("MARVIS_PROMPT_HOT_RELOAD", raising=False)
    monkeypatch.delenv("MAVRIS_PROMPT_HOT_RELOAD", raising=False)
    monkeypatch.delenv("MARVIS_ENV", raising=False)
    monkeypatch.delenv("MAVRIS_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    prompts.stop_prompt_watcher()
    prompts.clear_prompt_cache()
    yield
    prompts.stop_prompt_watcher()
    prompts.clear_prompt_cache()


def test_render_prompt_substitutes_variables():
    rendered = prompts.render_prompt("supervisor_user.md", {"mode": "privacy", "message": "hello"})

    assert "Mode: privacy" in rendered
    assert "User message: hello" in rendered


def test_development_prompt_hot_reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt = prompt_dir / "dynamic.md"
    prompt.write_text("first", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)
    monkeypatch.setenv("MAVRIS_DEV", "1")

    assert prompts.load_prompt("dynamic.md") == "first"

    first_mtime = prompt.stat().st_mtime
    prompt.write_text("second", encoding="utf-8")
    os.utime(prompt, (first_mtime + 2, first_mtime + 2))

    assert prompts.load_prompt("dynamic.md") == "second"


def test_invalidate_prompt_cache_forces_reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt = prompt_dir / "watched.md"
    prompt.write_text("first", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)

    assert prompts.load_prompt("watched.md") == "first"

    prompt.write_text("second", encoding="utf-8")

    assert prompts.load_prompt("watched.md") == "first"

    prompts.invalidate_prompt_cache(prompt)

    assert "watched.md" not in prompts._CACHE
    assert prompts.load_prompt("watched.md") == "second"


def test_production_prompt_cache_can_skip_reload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt = prompt_dir / "cached.md"
    prompt.write_text("first", encoding="utf-8")
    monkeypatch.setattr(prompts, "PROMPT_DIR", prompt_dir)
    monkeypatch.setenv("MARVIS_ENV", "production")

    assert prompts.load_prompt("cached.md") == "first"

    first_mtime = prompt.stat().st_mtime
    prompt.write_text("second", encoding="utf-8")
    os.utime(prompt, (first_mtime + 2, first_mtime + 2))

    assert prompts.load_prompt("cached.md") == "first"


def test_prompt_path_rejects_escape():
    with pytest.raises(ValueError):
        prompts.load_prompt("../secret.md")


def test_all_base_agents_with_prompt_files_load_markdown():
    agents = [
        AppAgent(),
        BrowserAgent(),
        ComputerAgent(),
        DocumentAgent(),
        FileAgent(),
        HumanGateAgent(),
        MemoryAgent(),
        PlannerAgent(),
        SafetyReviewAgent(),
        SearchAgent(),
    ]

    for agent in agents:
        assert agent.prompt_file, f"{agent.name} should declare a prompt_file"
        prompt = agent.system_prompt()
        assert agent.name in prompt
        assert len(prompt) > 80


def test_llm_message_prompts_are_loaded_from_prompt_files():
    root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "\"role\": \"system\"" not in text and "\"role\": \"user\"" not in text:
            continue
        for marker in (
            "\"content\": \"",
            "'content': '",
            "\"content\": f\"",
            "'content': f'",
        ):
            if marker in text:
                offenders.append(f"{path.relative_to(root)} contains inline LLM message content via {marker}")

    assert offenders == []
