"""Tests for P0-4 vision / OCR tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import AppSettings
from app.core import db
from app.llm.mock_provider import MockProvider
from app.tools import vision_tools
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    # A 1x1 transparent PNG (smallest valid file).
    import base64

    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    path = tmp_path / "tiny.png"
    path.write_bytes(data)
    return path


def test_mock_provider_vision_returns_string():
    provider = MockProvider()
    result = asyncio.run(provider.vision("foo.png", "what is this"))
    assert "mock-vision" in result.lower()


def test_mock_provider_ocr_returns_string():
    provider = MockProvider()
    text = asyncio.run(provider.ocr("foo.png"))
    assert "mock-ocr" in text.lower()


def test_describe_image_tool_uses_provider(sample_png, monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    context = {"allowed_directories": [str(tmp_path)]}
    result = vision_tools.describe_image({"path": str(sample_png)}, context)
    assert result["ok"] is True
    assert "tags" in result and isinstance(result["tags"], list)
    assert "Privacy mode requires" in result["description"]


def test_ocr_image_tool_returns_text(sample_png, monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    context = {"allowed_directories": [str(tmp_path)]}
    result = vision_tools.ocr_image({"path": str(sample_png)}, context)
    assert result["ok"] is False
    assert "local OCR" in result["error"] or "local OCR" in result.get("source", "")


def test_vision_tools_use_injected_local_provider(sample_png, monkeypatch, tmp_path):
    class _LocalVisionProvider:
        name = "local-test"

        async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
            return "local image description"

        async def ocr(self, image_path: str) -> str:
            return "local ocr text"

    monkeypatch.setattr(vision_tools, "get_provider", lambda task="vision": _LocalVisionProvider())
    monkeypatch.setattr(
        "app.indexer.ocr_service.get_provider",
        lambda settings=None, task="ocr": _LocalVisionProvider(),
    )
    context = {
        "allowed_directories": [str(tmp_path)],
        "settings": AppSettings(mode="efficiency", provider_name="openai", api_key="sk-test"),
    }

    description = vision_tools.describe_image({"path": str(sample_png)}, context)
    ocr = vision_tools.ocr_image({"path": str(sample_png)}, context)

    assert description["ok"] is True
    assert description["description"] == "local image description"
    assert ocr["ok"] is True
    assert ocr["text"] == "local ocr text"


def test_unsupported_extension_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    fake = tmp_path / "note.txt"
    fake.write_text("hello", encoding="utf-8")
    context = {"allowed_directories": [str(tmp_path)]}
    result = vision_tools.describe_image({"path": str(fake)}, context)
    assert result["ok"] is False
    assert "image" in result["error"].lower()


def test_vision_tools_registered():
    registry = register_all_tools()
    names = {tool.name for tool in registry.list()}
    assert "vision.describe_image" in names
    assert "vision.ocr_image" in names
    assert "vision.compare_images" in names


def test_indexer_parser_runs_ocr_on_images(sample_png, monkeypatch):
    """B3: indexer/parsers must route image files through ocr_service."""
    from app.indexer import parsers

    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    text = parsers.parse_file(sample_png)
    assert isinstance(text, str)
    assert text == ""


def test_indexer_parser_unknown_image_returns_string(tmp_path: Path):
    from app.indexer import parsers

    missing = tmp_path / "ghost.png"
    text = parsers.parse_file(missing)
    assert isinstance(text, str)
