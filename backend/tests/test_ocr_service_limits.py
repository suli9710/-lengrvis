from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app.indexer import ocr_service
from app.indexer.ocr_service import OCRResult


class _FakePage:
    def __init__(self, images: list[object]) -> None:
        self.images = images

    def extract_text(self) -> str:
        return ""


def test_pdf_ocr_fallback_limits_embedded_image_count(monkeypatch, tmp_path: Path):
    images = [
        SimpleNamespace(name="first.png", data=b"one"),
        SimpleNamespace(name="second.png", data=b"two"),
        SimpleNamespace(name="third.png", data=b"three"),
    ]
    reader = SimpleNamespace(pages=[_FakePage(images[:2]), _FakePage(images[2:])])
    calls: list[str] = []

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda _path: reader))
    monkeypatch.setenv("LENGRVIS_MAX_PDF_OCR_IMAGES", "2")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_result",
        lambda image_path, settings=None: calls.append(Path(image_path).name)
        or OCRResult(ok=True, text=f"text-{len(calls)}"),
    )

    text = ocr_service.extract_pdf_text_with_ocr_fallback(tmp_path / "sample.pdf")

    assert text == "text-1\ntext-2"
    assert len(calls) == 2


def test_pdf_ocr_fallback_skips_oversized_embedded_image(monkeypatch, tmp_path: Path):
    reader = SimpleNamespace(pages=[_FakePage([SimpleNamespace(name="large.png", data=b"12345")])])
    calls: list[str] = []

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda _path: reader))
    monkeypatch.setenv("LENGRVIS_MAX_PDF_OCR_IMAGE_BYTES", "4")
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_result",
        lambda image_path, settings=None: calls.append(Path(image_path).name) or OCRResult(ok=True, text="too late"),
    )

    text = ocr_service.extract_pdf_text_with_ocr_fallback(tmp_path / "sample.pdf")

    assert text == ""
    assert calls == []


def test_paddleocr_result_loop_limits_lines(monkeypatch, tmp_path: Path):
    class FakePaddleOCR:
        def ocr(self, image_path: str, cls: bool = True):  # noqa: ARG002, FBT001, FBT002
            return [
                [
                    [None, ("one", 0.9)],
                    [None, ("two", 0.9)],
                    [None, ("three", 0.9)],
                ]
            ]

    monkeypatch.setenv("LENGRVIS_ENABLE_PADDLEOCR", "1")
    monkeypatch.setenv("LENGRVIS_MAX_PADDLE_OCR_LINES", "2")
    monkeypatch.setattr(ocr_service, "_get_paddle_ocr", lambda lang: FakePaddleOCR())

    text = ocr_service._ocr_text_with_paddleocr(tmp_path / "image.png")

    assert text == "one\ntwo"
