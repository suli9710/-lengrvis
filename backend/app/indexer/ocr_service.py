from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.acceleration.onnx_sessions import (
    OnnxAccelerationUnavailable,
    OnnxSessionBackend,
    available_execution_providers,
    create_inference_session,
    detect_session_backend,
    health_payload,
    preprocess_image_for_onnx,
    run_session,
    session_input_names,
)
from app.config import AppSettings, get_env
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.registry import get_effective_settings, get_provider
from app.policy.privacy import can_use_cloud_model

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
_MIN_PDF_TEXT_CHARS = 24
_ACCELERATED_OCR_BACKENDS = ("winml", "directml", "openvino")
DEFAULT_MAX_PDF_OCR_IMAGES = 64
DEFAULT_MAX_PDF_OCR_IMAGE_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_PADDLE_OCR_PAGES = 32
DEFAULT_MAX_PADDLE_OCR_LINES = 512

logger = logging.getLogger(__name__)


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
    local = local_ocr_image(image_path, settings=settings)
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


def local_ocr_image(image_path: Path, *, settings: AppSettings | None = None) -> OCRResult:
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return OCRResult(ok=False, source="local", error=f"Unsupported image extension: {image_path.suffix}")
    if not image_path.exists():
        return OCRResult(ok=False, source="local", error=f"Image not found: {image_path}")

    metadata_text = _ocr_text_from_image_metadata(image_path)
    if metadata_text:
        return OCRResult(ok=True, text=metadata_text, source="local_metadata", language=guess_language(metadata_text))

    accelerated = accelerated_ocr_image(image_path, settings=settings)
    if accelerated.ok and accelerated.text.strip():
        return accelerated

    tesseract = _ocr_text_with_tesseract(image_path)
    if tesseract:
        return OCRResult(ok=True, text=tesseract, source="local_tesseract", language=guess_language(tesseract))

    paddle = _ocr_text_with_paddleocr(image_path)
    if paddle:
        return OCRResult(ok=True, text=paddle, source="local_paddleocr", language=guess_language(paddle))

    return OCRResult(ok=False, source="local", error=accelerated.error or "No local OCR engine produced text.")


def accelerated_ocr_image(image_path: Path, *, settings: AppSettings | None = None) -> OCRResult:
    status = detect_accelerated_ocr(settings=settings)
    if not status["available"]:
        return OCRResult(ok=False, source="local_accelerated", error=str(status.get("error") or "Accelerated OCR unavailable."))
    model = str(status.get("model") or "").strip()
    if not model:
        return OCRResult(
            ok=False,
            source=f"local_{status.get('selected_backend', 'accelerated')}",
            error="Accelerated OCR runtime is available but no OCR model path is configured.",
        )
    if not Path(model).exists():
        return OCRResult(
            ok=False,
            source=f"local_{status.get('selected_backend', 'accelerated')}",
            error=f"Accelerated OCR model not found: {model}",
        )
    backend = _accelerated_ocr_backend(settings=settings)
    if backend is None:
        return OCRResult(
            ok=False,
            source=f"local_{status.get('selected_backend', 'accelerated')}",
            error=status.get("error") or "Accelerated OCR runtime is unavailable.",
        )
    try:
        text = _run_onnx_ocr(image_path, backend)
    except OnnxAccelerationUnavailable as exc:
        return OCRResult(ok=False, source=f"local_{status.get('selected_backend', 'accelerated')}", error=str(exc))
    if not text.strip():
        return OCRResult(
            ok=False,
            source=f"local_{status.get('selected_backend', 'accelerated')}",
            error="Accelerated OCR produced no text.",
        )
    return OCRResult(
        ok=True,
        text=text.strip(),
        source=f"local_{status.get('selected_backend', 'accelerated')}",
        language=guess_language(text),
    )


def accelerated_ocr_smoke(settings: AppSettings | None = None) -> dict[str, Any]:
    """Return OCR accelerator health using a synthetic image or a no-op probe."""
    status = detect_accelerated_ocr(settings=settings)
    payload = {
        "ok": bool(status.get("available")),
        "selected_backend": status.get("selected_backend", ""),
        "runtime": status.get("runtime", ""),
        "model": status.get("model", ""),
        "error": status.get("error", ""),
        "smoke": "noop",
    }
    if not status.get("available"):
        return payload

    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # noqa: BLE001
        payload["ok"] = False
        payload["error"] = f"Synthetic OCR smoke image unavailable: {exc}"
        return payload

    with tempfile.TemporaryDirectory(prefix="lengrvis_ocr_smoke_") as tmp_dir:
        image_path = Path(tmp_dir) / "ocr-smoke.png"
        image = Image.new("RGB", (220, 64), "white")
        draw = ImageDraw.Draw(image)
        draw.text((12, 22), "LENGRVIS OCR SMOKE", fill="black")
        image.save(image_path)
        result = accelerated_ocr_image(image_path, settings=settings)
    payload["ok"] = result.ok
    payload["runtime"] = payload["runtime"] or result.source
    payload["error"] = result.error
    payload["smoke"] = "synthetic_image"
    return payload


def detect_accelerated_ocr(settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    enabled = bool(getattr(effective, "ocr_acceleration_enabled", True))
    configured_backend = str(
        getattr(effective, "ocr_acceleration_backend", None)
        or getattr(effective, "ocr_backend", "auto")
        or "auto"
    ).strip().lower()
    execution_provider = str(getattr(effective, "ocr_execution_provider", "") or "").strip().lower()
    if configured_backend == "auto" and execution_provider:
        configured_backend = execution_provider
    model = str(
        getattr(effective, "ocr_acceleration_model_path", None)
        or getattr(effective, "ocr_openvino_model_dir", "")
        or ""
    ).strip()
    if not enabled:
        return {
            "available": False,
            "selected_backend": configured_backend,
            "runtime": "",
            "model": model,
            "error": "Accelerated OCR is disabled.",
        }

    backend = _accelerated_ocr_backend(settings=effective)
    if backend is not None:
        payload = health_payload(
            component="ocr",
            backend=backend,
            configured_model_path=model,
            configured_provider=execution_provider,
        )
        payload["selected_backend"] = backend.kind.removeprefix("onnx-")
        payload["runtime"] = backend.runtime_package or backend.execution_provider
        payload["model"] = model
        return payload

    candidates = _ocr_backend_candidates(configured_backend)
    errors: list[str] = []
    for backend in candidates:
        runtime = _available_accelerated_runtime(backend)
        if runtime:
            return {
                "available": True,
                "selected_backend": backend,
                "runtime": runtime,
                "model": model,
                "error": "",
            }
        errors.append(f"{backend}: optional runtime not installed")
    return {
        "available": False,
        "selected_backend": configured_backend,
        "runtime": "",
        "model": model,
        "error": "; ".join(errors) or "No accelerated OCR backend selected.",
    }


def accelerated_ocr_health(settings: AppSettings | None = None) -> dict[str, Any]:
    return detect_accelerated_ocr(settings=settings)


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
        image_count = 0
        image_limit_reached = False
        max_images = _env_int_limit("LENGRVIS_MAX_PDF_OCR_IMAGES", DEFAULT_MAX_PDF_OCR_IMAGES)
        for page_index, page in enumerate(reader.pages, start=1):
            for image_index, image_file in enumerate(getattr(page, "images", []) or [], start=1):
                if image_count >= max_images:
                    image_limit_reached = True
                    break
                image_count += 1
                text = _ocr_pdf_image(image_file, path, page_index, image_index, settings=settings)
                if text:
                    ocr_texts.append(text)
            if image_limit_reached:
                logger.warning("PDF OCR image limit reached for %s (max_images=%s)", path, max_images)
                break
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
    with tempfile.TemporaryDirectory(prefix="lengrvis_pdf_ocr_") as tmp_dir:
        temp_image = Path(tmp_dir) / f"{pdf_path.stem}-p{page_index}-i{image_index}{suffix}"
        pil_image = getattr(image_file, "image", None)
        if pil_image is not None:
            if _pil_image_exceeds_ocr_limit(pil_image):
                logger.warning(
                    "Skipping oversized PDF OCR image in %s page=%s image=%s",
                    pdf_path,
                    page_index,
                    image_index,
                )
                return ""
            pil_image.save(temp_image)
        else:
            raw_data = getattr(image_file, "data", b"") or b""
            if _raw_image_data_exceeds_ocr_limit(raw_data):
                logger.warning(
                    "Skipping oversized PDF OCR image data in %s page=%s image=%s",
                    pdf_path,
                    page_index,
                    image_index,
                )
                return ""
            temp_image.write_bytes(bytes(raw_data))
        result = ocr_image_result(temp_image, settings=settings)
        return result.text.strip() if result.ok else ""


def _pdf_image_ocr_hint(image_file: Any) -> str:
    try:
        obj = image_file.indirect_reference.get_object()
    except Exception:
        return ""
    for key in ("/LengrvisOCRText", "/LengrvisOCRText", "/OCRText"):
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
    for key in ("lengrvis_ocr_text", "lengrvis_ocr_text", "ocr_text", "Description", "Comment"):
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


# PaddleOCR loads its detection/recognition models at construction time
# (seconds of latency); reuse one engine per language across calls.
_PADDLE_OCR_CACHE: dict[str, Any] = {}
_PADDLE_OCR_LOCK = threading.Lock()


def _get_paddle_ocr(lang: str) -> Any:
    with _PADDLE_OCR_LOCK:
        engine = _PADDLE_OCR_CACHE.get(lang)
        if engine is None:
            from paddleocr import PaddleOCR

            engine = PaddleOCR(use_angle_cls=True, lang=lang)
            _PADDLE_OCR_CACHE[lang] = engine
        return engine


def _ocr_text_with_paddleocr(image_path: Path) -> str:
    if (get_env("LENGRVIS_ENABLE_PADDLEOCR") or "").strip().lower() not in {"1", "true", "yes"}:
        return ""
    try:
        ocr = _get_paddle_ocr(get_env("LENGRVIS_PADDLEOCR_LANG", "en") or "en")
        result = ocr.ocr(str(image_path), cls=True)
    except Exception:
        return ""

    lines: list[str] = []
    max_pages = _env_int_limit("LENGRVIS_MAX_PADDLE_OCR_PAGES", DEFAULT_MAX_PADDLE_OCR_PAGES)
    max_lines = _env_int_limit("LENGRVIS_MAX_PADDLE_OCR_LINES", DEFAULT_MAX_PADDLE_OCR_LINES)
    for page_index, page in enumerate(result or []):
        if page_index >= max_pages:
            logger.warning("PaddleOCR page result limit reached for %s (max_pages=%s)", image_path, max_pages)
            break
        for item in page or []:
            if len(lines) >= max_lines:
                logger.warning("PaddleOCR line result limit reached for %s (max_lines=%s)", image_path, max_lines)
                return "\n".join(lines)
            try:
                text = item[1][0]
            except Exception:
                continue
            if str(text).strip():
                lines.append(str(text).strip())
    return "\n".join(lines)


def _raw_image_data_exceeds_ocr_limit(raw_data: Any) -> bool:
    max_bytes = _env_int_limit("LENGRVIS_MAX_PDF_OCR_IMAGE_BYTES", DEFAULT_MAX_PDF_OCR_IMAGE_BYTES)
    try:
        if len(raw_data) > max_bytes:
            return True
    except TypeError:
        return False
    return False


def _pil_image_exceeds_ocr_limit(image: Any) -> bool:
    max_bytes = _env_int_limit("LENGRVIS_MAX_PDF_OCR_IMAGE_BYTES", DEFAULT_MAX_PDF_OCR_IMAGE_BYTES)
    try:
        width = int(getattr(image, "width", 0) or 0)
        height = int(getattr(image, "height", 0) or 0)
        bands = getattr(image, "getbands", lambda: ())()
        channels = max(1, len(tuple(bands or ())))
    except Exception:
        return False
    return width > 0 and height > 0 and width * height * channels > max_bytes


def _env_int_limit(name: str, default: int) -> int:
    try:
        return max(1, int(get_env(name, str(default)) or default))
    except ValueError:
        return default


def _ocr_backend_candidates(configured_backend: str) -> list[str]:
    if configured_backend in {"", "auto"}:
        return list(_ACCELERATED_OCR_BACKENDS)
    if configured_backend in _ACCELERATED_OCR_BACKENDS:
        return [configured_backend]
    return []


def _available_accelerated_runtime(backend: str) -> str:
    if backend == "winml":
        if "WindowsMLExecutionProvider" in available_execution_providers() or _has_module("onnxruntime_windowsml"):
            return "onnxruntime-windowsml"
        return ""
    if backend == "directml":
        if "DmlExecutionProvider" in available_execution_providers() or _has_module("onnxruntime_directml"):
            return "onnxruntime-directml"
        return ""
    if backend == "openvino":
        if "OpenVINOExecutionProvider" in available_execution_providers():
            return "onnxruntime-openvino"
        if _has_module("openvino"):
            return "openvino"
        return ""
    return ""


def _accelerated_ocr_backend(settings: AppSettings | None = None) -> OnnxSessionBackend | None:
    effective = settings or get_effective_settings()
    model = str(
        getattr(effective, "ocr_acceleration_model_path", None)
        or getattr(effective, "ocr_openvino_model_dir", "")
        or ""
    ).strip()
    if not model:
        return None
    return detect_session_backend(
        model_path=model,
        configured_provider=str(getattr(effective, "ocr_execution_provider", "") or ""),
        settings=effective,
        model_id=f"ocr:{getattr(effective, 'ocr_lang', 'multi')}",
    )


def _run_onnx_ocr(image_path: Path, backend: OnnxSessionBackend) -> str:
    session = create_inference_session(backend)
    inputs = session_input_names(session)
    if not inputs:
        raise OnnxAccelerationUnavailable("OCR ONNX model exposes no inputs.")
    feed: dict[str, np.ndarray] = {}
    image_tensor = preprocess_image_for_onnx(image_path, size=_ocr_input_size(), normalize=False)
    for name in inputs:
        lowered = name.lower()
        if any(token in lowered for token in ("image", "pixel", "input", "x")):
            feed[name] = image_tensor
            break
    if not feed:
        feed[inputs[0]] = image_tensor
    outputs = run_session(session, feed)
    return _decode_ocr_outputs(outputs, Path(backend.model_path).parent)


def _decode_ocr_outputs(outputs: list[Any], model_dir: Path) -> str:
    for output in outputs:
        text = _decode_string_output(output)
        if text:
            return text
    vocab = _load_ocr_vocab(model_dir)
    if vocab:
        for output in outputs:
            text = _decode_token_output(np.asarray(output), vocab)
            if text:
                return text
    return ""


def _decode_string_output(output: Any) -> str:
    array = np.asarray(output)
    if array.dtype.kind not in {"U", "S", "O"}:
        return ""
    values: list[str] = []
    for item in array.reshape(-1).tolist():
        if isinstance(item, bytes):
            values.append(item.decode("utf-8", errors="ignore"))
        else:
            values.append(str(item))
    return " ".join(value.strip() for value in values if value and value.strip()).strip()


def _decode_token_output(output: np.ndarray, vocab: dict[int, str]) -> str:
    if output.size == 0:
        return ""
    values = output
    if values.ndim >= 3:
        values = values.argmax(axis=-1)
    elif values.ndim == 2 and values.dtype.kind == "f":
        values = values.argmax(axis=-1)
    token_ids = [int(token) for token in np.asarray(values).reshape(-1).tolist()]
    pieces: list[str] = []
    last_token: int | None = None
    for token in token_ids:
        if token == last_token:
            continue
        last_token = token
        piece = vocab.get(token, "")
        if not piece or piece in {"<pad>", "<s>", "</s>", "[PAD]", "[CLS]", "[SEP]", "<blank>"}:
            continue
        pieces.append(piece.replace("##", ""))
    return "".join(pieces).strip()


def _load_ocr_vocab(model_dir: Path) -> dict[int, str]:
    candidates = [
        model_dir / "vocab.txt",
        model_dir / "tokens.txt",
        model_dir / "charset.txt",
        model_dir / "vocab.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            if path.suffix.lower() == ".json":
                import json

                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {int(value): str(key) for key, value in raw.items()}
            lines = path.read_text(encoding="utf-8").splitlines()
            return {index: line.strip() for index, line in enumerate(lines)}
        except Exception:
            continue
    return {}


def _ocr_input_size() -> int:
    raw = get_env("LENGRVIS_OCR_IMAGE_SIZE", "224")
    try:
        return max(32, int(raw))
    except ValueError:
        return 224


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


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
