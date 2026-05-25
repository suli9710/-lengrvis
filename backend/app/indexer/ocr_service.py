from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import AppSettings
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.registry import get_effective_settings, get_provider
from app.policy.privacy import can_use_cloud_model


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
_MIN_PDF_TEXT_CHARS = 24


@dataclass(slots=True)
class OCRResult:
    ok: bool
    text: str = ""
    source: str = "local"
    language: str = "unknown"
    error: str = ""
    fallback_used: bool = False

    def as_dict(self, *, path: Path | None = None) -> dict[str, Any]:
        payload = {
            "ok": self.ok,
            "text": self.text,
            "language": self.language,
            "source": self.source,
            "fallback_used": self.fallback_used,
        }
        if path is not None:
            payload["path"] = str(path)
        if self.error:
            payload["error"] = self.error
        return payload


def ocr_image(image_path: str, allowed_directories: list[str] | None = None) -> str:
    result = ocr_image_result(Path(image_path))
    return result.text if result.ok else ""


def ocr_image_result(
    image_path: Path,
    *,
    settings: AppSettings | None = None,
    allow_cloud_fallback: bool | None = None,
) -> OCRResult:
    local = local_ocr_image(image_path)
    if local.ok and local.text.strip():
        return local

    effective = settings or get_effective_settings()
    cloud_allowed = allow_cloud_fallback
    if cloud_allowed is None:
        cloud_allowed = can_use_cloud_model(effective, task="ocr").allowed
    if not cloud_allowed:
        return OCRResult(
            ok=False,
            text=local.text,
            source=local.source,
            language=local.language,
            error=local.error or "Local OCR produced no text and cloud OCR is not allowed.",
        )

    fallback = provider_ocr_image(image_path, settings=effective)
    fallback.fallback_used = True
    if fallback.ok and fallback.text.strip():
        return fallback
    return OCRResult(
        ok=False,
        text=local.text,
        source=local.source,
        language=local.language,
        error=fallback.error or local.error or "OCR produced no text.",
        fallback_used=True,
    )


def local_ocr_image(image_path: Path) -> OCRResult:
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return OCRResult(ok=False, source="local", error=f"Unsupported image extension: {image_path.suffix}")
    if not image_path.exists():
        return OCRResult(ok=False, source="local", error=f"Image not found: {image_path}")

    metadata_text = _ocr_text_from_image_metadata(image_path)
    if metadata_text:
        return OCRResult(ok=True, text=metadata_text, source="local_metadata", language=guess_language(metadata_text))

    tesseract = _ocr_text_with_tesseract(image_path)
    if tesseract:
        return OCRResult(ok=True, text=tesseract, source="local_tesseract", language=guess_language(tesseract))

    return OCRResult(ok=False, source="local", error="No local OCR engine produced text.")


def provider_ocr_image(image_path: Path, *, settings: AppSettings | None = None) -> OCRResult:
    try:
        provider = get_provider(settings=settings, task="ocr")
        text = str(_run_async(provider.ocr(str(image_path))) or "").strip()
    except NotImplementedError:
        return OCRResult(ok=False, source="vision_provider", error="Provider OCR is not configured.")
    except LocalBackendUnavailable as exc:
        return OCRResult(ok=False, source="vision_provider", error=f"OCR unavailable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return OCRResult(ok=False, source="vision_provider", error=f"OCR failed: {exc}")
    if not text:
        return OCRResult(ok=False, source="vision_provider", error="Provider OCR returned no text.")
    return OCRResult(ok=True, text=text, source="vision_provider", language=guess_language(text))


def extract_pdf_text_with_ocr_fallback(
    path: Path,
    *,
    settings: AppSettings | None = None,
    min_text_chars: int = _MIN_PDF_TEXT_CHARS,
) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        page_texts = [page.extract_text() or "" for page in reader.pages]
        extracted = "\n".join(page_texts).strip()
        if len(extracted) >= min_text_chars:
            return extracted

        ocr_texts = []
        for page_index, page in enumerate(reader.pages, start=1):
            for image_index, image_file in enumerate(getattr(page, "images", []) or [], start=1):
                text = _ocr_pdf_image(image_file, path, page_index, image_index, settings=settings)
                if text:
                    ocr_texts.append(text)
        if ocr_texts:
            return "\n".join(ocr_texts)
        return extracted
    except Exception as exc:
        return f"[PDF extraction unavailable: {exc}]"


def _ocr_pdf_image(
    image_file: Any,
    pdf_path: Path,
    page_index: int,
    image_index: int,
    *,
    settings: AppSettings | None = None,
) -> str:
    embedded_text = _pdf_image_ocr_hint(image_file)
    if embedded_text:
        return embedded_text

    suffix = Path(getattr(image_file, "name", "") or "").suffix.lower() or ".png"
    if suffix not in IMAGE_EXTENSIONS:
        suffix = ".png"
    with tempfile.TemporaryDirectory(prefix="marvis_pdf_ocr_") as tmp_dir:
        temp_image = Path(tmp_dir) / f"{pdf_path.stem}-p{page_index}-i{image_index}{suffix}"
        pil_image = getattr(image_file, "image", None)
        if pil_image is not None:
            pil_image.save(temp_image)
        else:
            temp_image.write_bytes(bytes(getattr(image_file, "data", b"")))
        result = ocr_image_result(temp_image, settings=settings)
        return result.text.strip() if result.ok else ""


def _pdf_image_ocr_hint(image_file: Any) -> str:
    try:
        obj = image_file.indirect_reference.get_object()
    except Exception:
        return ""
    for key in ("/MarvisOCRText", "/OCRText"):
        value = obj.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _ocr_text_from_image_metadata(image_path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            info = dict(getattr(image, "info", {}) or {})
    except Exception:
        return ""
    for key in ("marvis_ocr_text", "ocr_text", "Description", "Comment"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _ocr_text_with_tesseract(image_path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image

        with Image.open(image_path) as image:
            return str(pytesseract.image_to_string(image) or "").strip()
    except Exception:
        return ""


def _run_async(coro) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def guess_language(text: str) -> str:
    if not text:
        return "unknown"
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return "zh" if chinese_chars > len(text) * 0.1 else "en"
