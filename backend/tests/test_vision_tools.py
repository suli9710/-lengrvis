"""Tests for P0-4 vision / OCR tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from app.config import AppSettings
from app.core import db
from app.llm.mock_provider import MockProvider
from app.tools import vision_tools
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    context = {"allowed_directories": [str(tmp_path)]}
    result = vision_tools.describe_image({"path": str(sample_png)}, context)
    assert result["ok"] is True
    assert "tags" in result and isinstance(result["tags"], list)
    assert result["description"]


def test_ocr_image_tool_returns_text(sample_png, monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    context = {"allowed_directories": [str(tmp_path)]}
    result = vision_tools.ocr_image({"path": str(sample_png)}, context)
    assert "ok" in result
    assert isinstance(result.get("text", ""), str)


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


def test_embed_image_falls_back_to_label_text_embedding_in_privacy_mode(sample_png, monkeypatch, tmp_path):
    monkeypatch.setattr(
        vision_tools,
        "describe_image",
        lambda args, context: {
            "ok": True,
            "path": str(sample_png),
            "description": "A screenshot of a document.",
            "tags": ["screenshot"],
            "structured_labels": {"scene_type": "screenshot", "people_count": 0, "visible_objects": ["document"]},
            "metadata": {},
        },
    )
    context = {
        "allowed_directories": [str(tmp_path)],
        "settings": AppSettings(mode="privacy", provider_name="openai", api_key=""),
    }

    result = vision_tools.embed_image({"path": str(sample_png)}, context)

    assert result["ok"] is True
    assert result["source"] == "label_text_embedding"
    assert result["fallback_used"] is True
    assert result["dim"] > 0


def test_embed_image_runs_local_onnx_session(sample_png, monkeypatch, tmp_path):
    model_path = tmp_path / "clip" / "model.onnx"
    model_path.parent.mkdir()
    model_path.write_bytes(b"placeholder")

    class _Input:
        name = "pixel_values"

    class _Session:
        def get_inputs(self):
            return [_Input()]

        def run(self, output_names, feed):
            assert "pixel_values" in feed
            return [np.asarray([[1.0, 2.0, 2.0]], dtype=np.float32)]

    monkeypatch.setattr(
        "app.acceleration.onnx_sessions.available_execution_providers", lambda: ["CPUExecutionProvider"]
    )
    monkeypatch.setattr("app.acceleration.onnx_sessions.import_onnxruntime", lambda: object())
    monkeypatch.setattr(vision_tools, "create_inference_session", lambda backend: _Session())
    monkeypatch.setattr(vision_tools, "available_execution_providers", lambda: ["CPUExecutionProvider"])
    context = {
        "allowed_directories": [str(tmp_path)],
        "settings": AppSettings(
            mode="privacy",
            image_embedding_backend="cpu",
            onnx_image_embedding_execution_provider="cpu",
            onnx_image_embedding_model_path=str(model_path),
            onnx_image_embedding_model_id="clip-test",
        ),
    }

    result = vision_tools.embed_image({"path": str(sample_png)}, context)

    assert result["ok"] is True
    assert result["source"] == "local_image_embedding_cpu"
    assert result["model"] == "clip-test"
    assert result["embedding"] == pytest.approx([1 / 3, 2 / 3, 2 / 3])


def test_unsupported_extension_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
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
    assert "vision.embed_image" in names
    assert "vision.compare_images" in names


def test_indexer_parser_runs_ocr_on_images(sample_png, monkeypatch):
    """B3: indexer/parsers must route image files through ocr_service."""
    from app.indexer import parsers

    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    text = parsers.parse_file(sample_png)
    assert isinstance(text, str)


def test_indexer_parser_unknown_image_returns_string(tmp_path: Path):
    from app.indexer import parsers

    missing = tmp_path / "ghost.png"
    text = parsers.parse_file(missing)
    assert isinstance(text, str)


# --- path sandbox regression (code review 3-H1) ---------------------------
# _resolve_image previously swallowed SecurityError and fell back to the raw
# path, letting vision tools read arbitrary images outside authorized dirs.


def _outside_image(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    path = outside / "secret.png"
    path.write_bytes(b"png-bytes")
    return path


def test_vision_tools_reject_unauthorized_path(tmp_path: Path):
    from app.core.errors import SecurityError

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    secret = _outside_image(tmp_path)
    context = {"allowed_directories": [str(allowed)]}

    for tool in (vision_tools.describe_image, vision_tools.ocr_image, vision_tools.embed_image):
        with pytest.raises(SecurityError):
            tool({"path": str(secret)}, context)


def test_vision_tools_reject_when_no_authorized_directories(tmp_path: Path, sample_png: Path):
    from app.core.errors import SecurityError

    context = {"allowed_directories": []}
    with pytest.raises(SecurityError):
        vision_tools.describe_image({"path": str(sample_png)}, context)


def test_resolve_image_batch_rejects_unauthorized_directory(tmp_path: Path):
    from app.core.errors import SecurityError

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    _outside_image(tmp_path)
    context = {"allowed_directories": [str(allowed)]}

    with pytest.raises(SecurityError):
        vision_tools._resolve_image_batch({"paths": [str(tmp_path / "outside")]}, context)


def test_compare_images_rejects_unauthorized_path(tmp_path: Path):
    from app.core.errors import SecurityError

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "ok.png"
    inside.write_bytes(b"png-bytes")
    secret = _outside_image(tmp_path)
    context = {"allowed_directories": [str(allowed)]}
    with pytest.raises(SecurityError):
        vision_tools.compare_images({"path_a": str(inside), "path_b": str(secret)}, context)
