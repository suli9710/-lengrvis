from __future__ import annotations

import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.config import AppSettings
from app.core import db
from app.indexer import ocr_service
from app.tools import document_tools, vision_tools

FIXTURE_TEXT = "OFFLINE OCR INVOICE 042"
PDF_FIXTURE_TEXT = "SCANNED PDF OCR FALLBACK 314"


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


@pytest.fixture
def ocr_png(tmp_path: Path) -> Path:
    path = tmp_path / "offline-ocr.png"
    _write_metadata_png(path, FIXTURE_TEXT)
    return path


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "scanned.pdf"
    _write_image_only_pdf(path, PDF_FIXTURE_TEXT)
    return path


def test_privacy_mode_local_ocr_without_vision_endpoint(ocr_png: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_provider(task: str = "ocr") -> Any:
        raise AssertionError(f"provider should not be used in privacy OCR path: {task}")

    monkeypatch.setattr(ocr_service, "get_provider", fail_provider)
    settings = AppSettings(mode="privacy", provider_name="openai", api_key="", allow_mock_fallback=False)

    result = ocr_service.ocr_image_result(ocr_png, settings=settings)

    assert result.ok is True
    assert result.text == FIXTURE_TEXT
    assert result.source == "local_metadata"
    assert result.fallback_used is False


def test_vision_tool_ocr_uses_local_result_in_privacy_mode(ocr_png: Path) -> None:
    context = {
        "allowed_directories": [str(ocr_png.parent)],
        "settings": AppSettings(mode="privacy", provider_name="openai", api_key=""),
    }

    result = vision_tools.ocr_image({"path": str(ocr_png)}, context)

    assert result["ok"] is True
    assert result["text"] == FIXTURE_TEXT
    assert result["source"] == "local_metadata"


def test_local_ocr_tries_accelerated_backend_before_tesseract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain_png = tmp_path / "plain.png"
    _write_metadata_png(plain_png, "")
    calls: list[str] = []

    def accelerated(path: Path, **kwargs: Any) -> ocr_service.OCRResult:
        calls.append("accelerated")
        return ocr_service.OCRResult(ok=True, text="ACCELERATED OCR TEXT", source="local_openvino")

    monkeypatch.setattr(ocr_service, "accelerated_ocr_image", accelerated)
    monkeypatch.setattr(ocr_service, "_ocr_text_with_tesseract", lambda path: calls.append("tesseract") or "TESS")

    result = ocr_service.local_ocr_image(plain_png)

    assert result.ok is True
    assert result.text == "ACCELERATED OCR TEXT"
    assert result.source == "local_openvino"
    assert calls == ["accelerated"]


def test_accelerated_ocr_smoke_reports_missing_runtime_without_user_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr_service, "_available_accelerated_runtime", lambda backend: "")
    settings = AppSettings(ocr_backend="openvino", ocr_openvino_model_dir="")

    result = ocr_service.accelerated_ocr_smoke(settings=settings)

    assert result["ok"] is False
    assert result["selected_backend"] == "openvino"
    assert "runtime not installed" in result["error"]
    assert result["smoke"] == "noop"


def test_accelerated_ocr_runs_onnx_session_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "plain.png"
    model_path = tmp_path / "ocr" / "model.onnx"
    model_path.parent.mkdir()
    model_path.write_bytes(b"placeholder")
    _write_metadata_png(image_path, "")

    class _Input:
        name = "pixel_values"

    class _Session:
        def get_inputs(self):
            return [_Input()]

        def run(self, output_names, feed):
            assert "pixel_values" in feed
            return [np.asarray(["ONNX OCR TEXT"], dtype=object)]

    monkeypatch.setattr(ocr_service, "available_execution_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(
        "app.acceleration.onnx_sessions.available_execution_providers", lambda: ["CPUExecutionProvider"]
    )
    monkeypatch.setattr("app.acceleration.onnx_sessions.import_onnxruntime", lambda: object())
    monkeypatch.setattr("app.acceleration.onnx_sessions.create_inference_session", lambda backend: _Session())
    monkeypatch.setattr(ocr_service, "create_inference_session", lambda backend: _Session())
    settings = AppSettings(ocr_backend="cpu", ocr_execution_provider="cpu", ocr_openvino_model_dir=str(model_path))

    result = ocr_service.accelerated_ocr_image(image_path, settings=settings)

    assert result.ok is True
    assert result.text == "ONNX OCR TEXT"
    assert result.source == "local_cpu"


def test_cloud_vision_provider_is_fallback_when_local_ocr_has_no_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain_png = tmp_path / "plain.png"
    _write_metadata_png(plain_png, "")

    class CloudOCRProvider:
        name = "cloud-test"

        async def ocr(self, image_path: str) -> str:
            return "CLOUD OCR FALLBACK TEXT"

    monkeypatch.setattr(ocr_service, "get_provider", lambda settings=None, task="ocr": CloudOCRProvider())
    settings = AppSettings(
        mode="efficiency",
        provider_name="openai",
        api_key="sk-test",
        allow_file_content_upload=True,
    )

    result = ocr_service.ocr_image_result(plain_png, settings=settings)

    assert result.ok is True
    assert result.text == "CLOUD OCR FALLBACK TEXT"
    assert result.source == "vision_provider"
    assert result.fallback_used is True


def test_image_pdf_extract_text_uses_ocr_fallback(image_pdf: Path) -> None:
    text = document_tools.extract_text_from_path(image_pdf)

    assert PDF_FIXTURE_TEXT in text


def test_text_pdf_does_not_call_ocr_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "text.pdf"
    _write_text_pdf(path, "A normal PDF text layer with enough searchable content.")

    def fail_pdf_image(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("text PDF should not run image OCR fallback")

    monkeypatch.setattr(ocr_service, "_ocr_pdf_image", fail_pdf_image)
    text = ocr_service.extract_pdf_text_with_ocr_fallback(path)

    assert "normal PDF text layer" in text


def _write_metadata_png(path: Path, text: str) -> None:
    from PIL import Image, ImageDraw, PngImagePlugin

    image = Image.new("RGB", (260, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.text((12, 24), text or "NO OCR TEXT", fill="black")
    metadata = PngImagePlugin.PngInfo()
    if text:
        metadata.add_text("lengrvis_ocr_text", text)
    image.save(path, pnginfo=metadata)


def _write_image_only_pdf(path: Path, ocr_text: str) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (260, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.text((12, 30), ocr_text, fill="black")
    width, height = image.size
    compressed = zlib.compress(image.tobytes())
    content = f"q {width} 0 0 {height} 0 0 cm /Im1 Do Q".encode()
    objects = [
        _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _pdf_object(
            3,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                "/Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>"
            ).encode(),
        ),
        _pdf_object(
            4,
            (
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(compressed)} /LengrvisOCRText ({ocr_text}) >>\nstream\n"
            ).encode()
            + compressed
            + b"\nendstream",
        ),
        _pdf_object(5, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"),
    ]
    _write_pdf(path, objects)


def _write_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        _pdf_object(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _pdf_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _pdf_object(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        ),
        _pdf_object(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        _pdf_object(5, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"),
    ]
    _write_pdf(path, objects)


def _pdf_object(number: int, body: bytes) -> bytes:
    return f"{number} 0 obj\n".encode() + body + b"\nendobj\n"


def _write_pdf(path: Path, objects: list[bytes]) -> None:
    data = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(data))
        data += obj
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(data)
