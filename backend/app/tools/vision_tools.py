from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import json
import logging
import re
from datetime import datetime
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
from app.indexer.ocr_service import IMAGE_EXTENSIONS, guess_language, ocr_image_result
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.prompts import load_prompt
from app.llm.registry import get_effective_settings, get_provider
from app.policy.privacy import can_use_cloud_model
from app.policy.redaction import redact_public_text
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition
from app.tools.tool_catalog import tool_description, tool_search_hint

_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS
_GPS_EXIF_TAG = 34853
DEFAULT_VISION_TIMEOUT_SECONDS = 45.0
logger = logging.getLogger(__name__)

_IMAGE_METADATA_ERRORS = (ImportError, OSError, ValueError, TypeError, AttributeError)
_VECTOR_COERCE_ERRORS = (TypeError, ValueError, OverflowError)
_EXIF_TEXT_ERRORS = (UnicodeError, ValueError)
_GPS_IFD_ERRORS = (AttributeError, KeyError, TypeError, ValueError)
_TEXT_EMBEDDING_FALLBACK_ERRORS = (TimeoutError, OSError, TypeError, ValueError, OverflowError)

_DESCRIPTION_METADATA_KEYS = (
    "lengrvis_description",
    "lengrvis_description",
    "description",
    "Description",
    "comment",
    "Comment",
    "ImageDescription",
)
_CAPTURED_AT_METADATA_KEYS = (
    "lengrvis_captured_at",
    "lengrvis_captured_at",
    "captured_at",
    "date",
    "DateTime",
    "date:create",
    "date:modify",
    "creation_time",
)
_PEOPLE_METADATA_KEYS = ("lengrvis_people_count", "lengrvis_people_count", "people_count", "PeopleCount")
_SCENE_METADATA_KEYS = ("lengrvis_scene_type", "lengrvis_scene_type", "scene_type", "SceneType")
_OBJECT_METADATA_KEYS = (
    "lengrvis_visible_objects",
    "lengrvis_visible_objects",
    "visible_objects",
    "VisibleObjects",
    "objects",
)

_NUMBER_WORDS = {
    "zero": 0,
    "no": 0,
    "one": 1,
    "a": 1,
    "single": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_SCENE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("beach", ("beach", "ocean", "sea", "sand", "surf", "shore")),
    ("office", ("office", "desk", "laptop", "meeting", "workspace", "screen")),
    ("document", ("document", "invoice", "receipt", "contract", "paper", "form")),
    ("screenshot", ("screenshot", "browser", "window", "interface", "ui", "code")),
    ("city", ("city", "street", "building", "skyline", "urban")),
    ("landscape", ("landscape", "mountain", "forest", "river", "sunset", "sunrise")),
    ("food", ("food", "meal", "plate", "restaurant", "kitchen")),
    ("portrait", ("portrait", "selfie", "face", "person", "people")),
    ("vehicle", ("car", "vehicle", "road", "garage", "engine")),
    ("indoor", ("room", "indoor", "home", "house")),
    ("outdoor", ("outdoor", "park", "garden", "field")),
)

_OBJECT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("person", ("person", "people", "man", "woman", "child", "group", "face")),
    ("ocean", ("ocean", "sea", "wave", "water")),
    ("umbrella", ("umbrella", "umbrellas")),
    ("sand", ("sand", "beach")),
    ("desk", ("desk", "table")),
    ("laptop", ("laptop", "computer")),
    ("screen", ("screen", "monitor", "display")),
    ("document", ("document", "paper", "invoice", "receipt", "contract")),
    ("car", ("car", "vehicle", "automobile")),
    ("tree", ("tree", "forest", "garden")),
    ("mountain", ("mountain", "hill")),
    ("food", ("food", "plate", "meal")),
    ("phone", ("phone", "mobile")),
)


def _resolve_image(args: dict[str, Any], context: dict[str, Any]) -> Path | None:
    from app.core.errors import SecurityError
    from app.core.paths import resolve_authorized

    raw = args.get("path") or args.get("image_path")
    if not raw:
        return None
    allowed = list(context.get("allowed_directories") or [])
    # SecurityError propagates: never fall back to the raw path, that would
    # bypass the path sandbox (previously an authorization bypass).
    try:
        return resolve_authorized(raw, allowed)
    except OSError as exc:
        raise SecurityError(f"image path could not be resolved: {exc}") from exc


def _resolve_image_batch(args: dict[str, Any], context: dict[str, Any]) -> list[Path]:
    raw_paths = args.get("paths") or args.get("image_paths") or args.get("images")
    if raw_paths is None and args.get("path"):
        raw_paths = [args["path"]]
    if isinstance(raw_paths, str | Path):
        raw_paths = [raw_paths]

    paths: list[Path] = []
    for raw in raw_paths or []:
        resolved = _resolve_image({"path": str(raw)}, context)
        if resolved is None:
            continue
        if resolved.is_dir():
            paths.extend(
                path for path in resolved.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS
            )
        elif resolved.suffix.lower() in _IMAGE_EXTENSIONS:
            paths.append(resolved)
    return sorted(dict.fromkeys(paths), key=lambda path: str(path).lower())


async def _with_timeout(value: Any, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0:
        return await value
    return await asyncio.wait_for(value, timeout=timeout_seconds)


def _run_awaitable(value: Any, *, timeout_seconds: float | None = DEFAULT_VISION_TIMEOUT_SECONDS) -> Any:
    if not hasattr(value, "__await__"):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout(value, timeout_seconds))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(asyncio.run, _with_timeout(value, timeout_seconds))
    try:
        guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 1
        return future.result(timeout=guard_timeout)
    finally:
        if not future.done():
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def _run_vision(
    prompt: str,
    image_path: Path,
    task: str = "vision",
    *,
    timeout_seconds: float | None = DEFAULT_VISION_TIMEOUT_SECONDS,
) -> str:
    try:
        provider = get_provider(task=task)
        return str(_run_awaitable(provider.vision(str(image_path), prompt), timeout_seconds=timeout_seconds))
    except TimeoutError:
        return "[vision timed out]"
    except NotImplementedError:
        return f"[{provider.name}] vision not configured"
    except LocalBackendUnavailable as exc:
        return f"[vision unavailable: {exc}]"
    except Exception as exc:  # noqa: BLE001
        return f"[vision unavailable: {exc}]"


def describe_image(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    image_path = _resolve_image(args, context)
    if image_path is None:
        return {"ok": False, "error": "missing path"}
    if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return {"ok": False, "error": f"not a supported image extension: {image_path.suffix}"}

    metadata = extract_image_metadata(image_path)
    description = str(metadata.get("description_hint") or "").strip()
    if not description:
        prompt = load_prompt("vision_describe_image.md")
        description = _run_vision(prompt, image_path, task="vision")
    structured_labels = structure_image_labels(description, metadata)
    return {
        "ok": True,
        "path": str(image_path),
        "description": description,
        "tags": _heuristic_tags(description, structured_labels),
        "structured_labels": structured_labels,
        "metadata": metadata,
    }


def describe_images(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    paths = _resolve_image_batch(args, context)
    images = [describe_image({"path": str(path)}, context) for path in paths]
    return {
        "ok": all(image.get("ok") for image in images),
        "images": images,
        "count": len(images),
    }


def ocr_image(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    image_path = _resolve_image(args, context)
    if image_path is None:
        return {"ok": False, "error": "missing path"}
    if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return {"ok": False, "error": f"not a supported image extension: {image_path.suffix}"}
    result = ocr_image_result(image_path, settings=context.get("settings"))
    return result.as_dict(path=image_path)


def embed_image(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    image_path = _resolve_image(args, context)
    if image_path is None:
        return {"ok": False, "error": "missing path"}
    if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
        return {"ok": False, "error": f"not a supported image extension: {image_path.suffix}"}
    profile = args.get("profile") if isinstance(args.get("profile"), dict) else None
    result = image_embedding(image_path, context=context, profile=profile)
    return {
        "ok": bool(result.get("ok")),
        "path": str(image_path),
        "embedding": result.get("embedding") or [],
        "dim": len(result.get("embedding") or []),
        "source": result.get("source", ""),
        "model": result.get("model", ""),
        "error": result.get("error", ""),
        "fallback_used": bool(result.get("fallback_used", False)),
    }


def image_embedding(
    image_path: Path,
    *,
    context: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    settings = context.get("settings") or get_effective_settings()
    accelerated = _local_image_embedding(image_path, settings=settings)
    if accelerated.get("ok"):
        return accelerated

    cloud_allowed = can_use_cloud_model(settings, task="vision").allowed
    if cloud_allowed:
        provider = context.get("image_embedding_provider")
        if provider is not None:
            try:
                vector = _run_maybe_async(provider.embed_image(str(image_path)))
                coerced = _coerce_vector(vector)
                if coerced:
                    return {
                        "ok": True,
                        "embedding": coerced,
                        "source": "vision_provider_image_embedding",
                        "model": getattr(provider, "name", ""),
                        "fallback_used": False,
                    }
            except Exception as exc:  # noqa: BLE001
                logger.debug("image provider profile failed for %s: %s", image_path, exc, exc_info=True)

    fallback_profile = profile or _embedding_fallback_profile(image_path, context, settings=settings)
    label_text = image_label_text(fallback_profile)
    text_embedder = context.get("embedder")
    if text_embedder is None and not can_use_cloud_model(settings, task="embed").allowed:
        from app.indexer.clustering import hashing_vectorize

        vector = hashing_vectorize([label_text], dim=64)[0]
    else:
        from app.indexer.embedding_service import embed_texts_sync

        try:
            vectors = embed_texts_sync([label_text], embedder=text_embedder)
            vector = vectors[0] if vectors else []
        except _TEXT_EMBEDDING_FALLBACK_ERRORS:
            from app.indexer.clustering import hashing_vectorize

            vector = hashing_vectorize([label_text], dim=64)[0]
    return {
        "ok": bool(vector),
        "embedding": _coerce_vector(vector),
        "source": "label_text_embedding",
        "model": getattr(settings, "embedding_model", ""),
        "error": accelerated.get("error", ""),
        "fallback_used": True,
    }


def compare_images(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    path_a_raw = args.get("path_a") or args.get("a")
    path_b_raw = args.get("path_b") or args.get("b")
    if not path_a_raw or not path_b_raw:
        return {"ok": False, "error": "missing path_a or path_b"}
    a = _resolve_image({"path": path_a_raw}, context)
    b = _resolve_image({"path": path_b_raw}, context)
    if not (a and b):
        return {"ok": False, "error": "invalid paths"}
    prompt = load_prompt("vision_compare_image.md")
    desc_a = _run_vision(prompt, a)
    desc_b = _run_vision(prompt, b)
    similarity = _string_similarity(desc_a, desc_b)
    return {
        "ok": True,
        "path_a": str(a),
        "path_b": str(b),
        "description_a": desc_a,
        "description_b": desc_b,
        "similarity": round(similarity, 3),
    }


def extract_image_metadata(image_path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "filename": image_path.name,
        "extension": image_path.suffix.lower(),
    }
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            metadata.update(
                {
                    "width": image.width,
                    "height": image.height,
                    "format": image.format or image_path.suffix.lstrip(".").upper(),
                    "mode": image.mode,
                }
            )
            info = dict(getattr(image, "info", {}) or {})
            exif = image.getexif()
            gps_ifd = _get_gps_ifd(exif)
    except _IMAGE_METADATA_ERRORS as exc:
        metadata["metadata_error"] = str(exc)
        return metadata

    description = _first_text_value(info, _DESCRIPTION_METADATA_KEYS) or _decode_exif_text(exif.get(270))
    if description:
        metadata["description_hint"] = description

    captured_at = (
        _first_text_value(info, _CAPTURED_AT_METADATA_KEYS)
        or _decode_exif_text(exif.get(36867))
        or _decode_exif_text(exif.get(36868))
        or _decode_exif_text(exif.get(306))
    )
    normalized_captured_at = _normalize_capture_time(captured_at)
    if normalized_captured_at:
        metadata["captured_at"] = normalized_captured_at

    people_count = _coerce_int(_first_text_value(info, _PEOPLE_METADATA_KEYS))
    if people_count is not None:
        metadata["people_count"] = people_count

    scene_type = _first_text_value(info, _SCENE_METADATA_KEYS)
    if scene_type:
        metadata["scene_type"] = scene_type.strip().lower()

    visible_objects = _split_metadata_list(_first_text_value(info, _OBJECT_METADATA_KEYS))
    if visible_objects:
        metadata["visible_objects"] = visible_objects

    gps = _gps_from_metadata(info) or _gps_from_ifd(gps_ifd)
    if gps:
        metadata["gps"] = gps

    camera_make = _decode_exif_text(exif.get(271))
    camera_model = _decode_exif_text(exif.get(272))
    if camera_make or camera_model:
        metadata["camera"] = " ".join(part for part in (camera_make, camera_model) if part).strip()

    return metadata


def structure_image_labels(description: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    parsed = _parse_structured_label_text(description)
    people_count = _coerce_int(metadata.get("people_count"))
    if people_count is None:
        people_count = _coerce_int(parsed.get("people_count"))
    if people_count is None:
        people_count = _infer_people_count(description)
    scene_type = str(metadata.get("scene_type") or parsed.get("scene_type") or "").strip().lower() or _infer_scene_type(
        description
    )
    visible_objects = list(metadata.get("visible_objects") or [])
    if not visible_objects:
        visible_objects = list(parsed.get("visible_objects") or [])
    if not visible_objects:
        visible_objects = _infer_visible_objects(description)
    label_metadata = {
        key: metadata[key] for key in ("captured_at", "gps", "camera", "width", "height", "format") if key in metadata
    }
    return {
        "people_count": people_count,
        "scene_type": scene_type,
        "visible_objects": visible_objects,
        "scene": scene_type,
        "objects": visible_objects,
        "metadata": label_metadata,
    }


def image_label_text(profile: dict[str, Any]) -> str:
    labels = profile.get("structured_labels") or {}
    metadata = profile.get("metadata") or labels.get("metadata") or {}
    objects = labels.get("visible_objects") or []
    parts = [
        f"scene {labels.get('scene_type') or 'unknown'}",
        f"people {labels.get('people_count', 0)}",
        "objects " + " ".join(str(obj) for obj in objects),
        str(profile.get("description") or ""),
    ]
    if metadata.get("captured_at"):
        parts.append(f"captured {metadata['captured_at']}")
    gps = metadata.get("gps") or {}
    if isinstance(gps, dict) and gps.get("latitude") is not None and gps.get("longitude") is not None:
        parts.append(f"gps {round(float(gps['latitude']), 2)} {round(float(gps['longitude']), 2)}")
    return " ".join(part for part in parts if part).strip()


def _local_image_embedding(image_path: Path, *, settings: Any) -> dict[str, Any]:
    model = str(getattr(settings, "onnx_image_embedding_model_path", "") or "").strip()
    model_label = _image_embedding_model_label(settings, model)
    if _image_embedding_disabled(settings):
        return {
            "ok": False,
            "source": "local_image_embedding",
            "model": model_label,
            "error": "Image embedding is disabled.",
        }
    if not model:
        return {
            "ok": False,
            "source": "local_image_embedding",
            "model": model_label,
            "error": "No local image embedding model configured.",
        }
    if not Path(model).exists():
        return {
            "ok": False,
            "source": "local_image_embedding",
            "model": model_label,
            "error": f"Local image embedding model not found: {model_label or 'configured model'}",
        }
    backend = _image_embedding_backend(settings)
    if backend is None:
        return {
            "ok": False,
            "source": "local_image_embedding",
            "model": model_label,
            "error": "Local image embedding runtime is unavailable.",
        }
    try:
        vector = _run_local_image_embedding(image_path, backend)
    except OnnxAccelerationUnavailable as exc:
        return {
            "ok": False,
            "source": f"local_image_embedding_{backend.kind.removeprefix('onnx-')}",
            "model": model_label,
            "error": _safe_embedding_error(exc),
        }
    return {
        "ok": bool(vector),
        "embedding": vector,
        "source": f"local_image_embedding_{backend.kind.removeprefix('onnx-')}",
        "model": backend.model_id or model_label,
        "error": "" if vector else "Local image embedding produced no vector.",
        "fallback_used": False,
    }


def _image_embedding_model_label(settings: Any, model: str) -> str:
    model_id = str(getattr(settings, "onnx_image_embedding_model_id", "") or "").strip()
    if model_id:
        return model_id
    if not model:
        return ""
    return Path(model).name or "configured model"


def _safe_embedding_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return redact_public_text(text)


def _embedding_fallback_profile(image_path: Path, context: dict[str, Any], *, settings: Any) -> dict[str, Any]:
    if can_use_cloud_model(settings, task="vision").allowed:
        return describe_image({"path": str(image_path)}, context)
    metadata = extract_image_metadata(image_path)
    return {
        "ok": True,
        "path": str(image_path),
        "description": str(metadata.get("description_hint") or ""),
        "tags": ["image"],
        "structured_labels": structure_image_labels(str(metadata.get("description_hint") or ""), metadata),
        "metadata": metadata,
    }


def _available_image_embedding_runtime(backend: str) -> str:
    candidates = [backend] if backend and backend != "auto" else ["winml", "directml", "openvino", "cpu"]
    for candidate in candidates:
        normalized = candidate.lower()
        providers = available_execution_providers()
        if normalized == "winml" and "WindowsMLExecutionProvider" in providers:
            return "winml"
        if normalized == "directml" and "DmlExecutionProvider" in providers:
            return "directml"
        if normalized == "openvino" and "OpenVINOExecutionProvider" in providers:
            return "openvino"
        if normalized == "cpu" and "CPUExecutionProvider" in providers:
            return "cpu"
    return ""


def image_embedding_health(settings: Any | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    model = str(getattr(effective, "onnx_image_embedding_model_path", "") or "").strip()
    if _image_embedding_disabled(effective):
        return health_payload(
            component="image_embedding",
            backend=None,
            configured_model_path=model,
            configured_provider=str(getattr(effective, "onnx_image_embedding_execution_provider", "") or ""),
            error="Image embedding is disabled.",
        )
    backend = _image_embedding_backend(effective)
    if backend is None:
        error = "No local image embedding model configured."
        if model and not Path(model).exists():
            error = f"Local image embedding model not found: {model}"
        elif model:
            error = "Local image embedding runtime is unavailable."
        return health_payload(
            component="image_embedding",
            backend=None,
            configured_model_path=model,
            configured_provider=str(getattr(effective, "onnx_image_embedding_execution_provider", "") or ""),
            error=error,
        )
    return health_payload(
        component="image_embedding",
        backend=backend,
        configured_model_path=model,
        configured_provider=str(getattr(effective, "onnx_image_embedding_execution_provider", "") or ""),
    )


def test_image_embedding(settings: Any | None = None, image_path: str | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    health = image_embedding_health(effective)
    if not health.get("available"):
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "test_image_embedding",
            "error": health.get("error") or "Local image embedding is unavailable.",
            "image_embedding": health,
        }
    try:
        target = Path(image_path) if image_path else _synthetic_image_for_smoke()
        result = _local_image_embedding(target, settings=effective)
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or exc.__class__.__name__
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "test_image_embedding",
            "error": error,
            "image_embedding": health,
        }
    return {
        "ok": bool(result.get("ok")),
        "available": bool(result.get("ok")),
        "status": "ready" if result.get("ok") else "unavailable",
        "operation": "test_image_embedding",
        "dim": len(result.get("embedding") or []),
        "source": result.get("source", ""),
        "error": result.get("error", ""),
        "image_embedding": image_embedding_health(effective),
    }


def _image_embedding_disabled(settings: Any) -> bool:
    backend = str(getattr(settings, "image_embedding_backend", "auto") or "auto").strip().lower()
    return backend in {"disabled", "off", "none"}


def _image_embedding_backend(settings: Any) -> OnnxSessionBackend | None:
    model = str(getattr(settings, "onnx_image_embedding_model_path", "") or "").strip()
    if not model:
        return None
    return detect_session_backend(
        model_path=model,
        configured_provider=str(getattr(settings, "onnx_image_embedding_execution_provider", "") or ""),
        available_providers=available_execution_providers(),
        settings=settings,
        model_id=str(getattr(settings, "onnx_image_embedding_model_id", "") or ""),
    )


def _run_local_image_embedding(image_path: Path, backend: OnnxSessionBackend) -> list[float]:
    session = create_inference_session(backend)
    input_names = session_input_names(session)
    if not input_names:
        raise OnnxAccelerationUnavailable("Image embedding ONNX model exposes no inputs.")
    image_tensor = preprocess_image_for_onnx(image_path, size=_image_embedding_size(), normalize=True)
    feed: dict[str, np.ndarray] = {}
    for name in input_names:
        lowered = name.lower()
        if any(token in lowered for token in ("pixel", "image", "input", "x")):
            feed[name] = image_tensor
            break
    if not feed:
        feed[input_names[0]] = image_tensor
    outputs = run_session(session, feed)
    if not outputs:
        raise OnnxAccelerationUnavailable("Image embedding ONNX model returned no outputs.")
    vector = _pool_image_embedding_output(outputs)
    return _l2_normalize_vector(vector)


def _pool_image_embedding_output(outputs: list[Any]) -> list[float]:
    best: np.ndarray | None = None
    for output in outputs:
        values = np.asarray(output, dtype=np.float32)
        if values.size == 0:
            continue
        if values.ndim == 1:
            candidate = values
        elif values.ndim == 2:
            candidate = values[0]
        else:
            candidate = values.reshape(values.shape[0], -1)[0]
        if best is None or candidate.size > best.size:
            best = candidate
    if best is None:
        return []
    return [float(value) for value in best.tolist()]


def _l2_normalize_vector(vector: list[float]) -> list[float]:
    if not vector:
        return []
    values = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(values))
    if norm <= 1e-12:
        return [float(value) for value in values.tolist()]
    return [float(value) for value in (values / norm).tolist()]


def _image_embedding_size() -> int:
    raw = str(getattr(get_effective_settings(), "onnx_image_embedding_size", "") or "") or "224"
    try:
        return max(32, int(raw))
    except ValueError:
        return 224


def _synthetic_image_for_smoke() -> Path:
    import tempfile

    from PIL import Image, ImageDraw

    temp_dir = Path(tempfile.mkdtemp(prefix="lengrvis_image_embedding_smoke_"))
    path = temp_dir / "image-embedding-smoke.png"
    image = Image.new("RGB", (224, 224), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 200, 200), outline="black")
    draw.text((44, 104), "Lengrvis", fill="black")
    image.save(path)
    return path


def _run_maybe_async(value: Any) -> Any:
    return _run_awaitable(value)


def _coerce_vector(vector: Any) -> list[float]:
    try:
        return [float(value) for value in vector]
    except _VECTOR_COERCE_ERRORS:
        return []


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _heuristic_tags(description: str, structured_labels: dict[str, Any] | None = None) -> list[str]:
    keywords: list[str] = []
    if structured_labels:
        scene = structured_labels.get("scene_type")
        if scene and scene != "unknown":
            keywords.append(str(scene))
        people_count = _coerce_int(structured_labels.get("people_count"))
        if people_count is not None and people_count > 0:
            keywords.append("people")
        keywords.extend(str(obj) for obj in structured_labels.get("visible_objects") or [])
    for token in ("invoice", "contract", "screenshot", "screen", "person", "landscape", "text", "table", "code"):
        if token in (description or "").lower():
            keywords.append(token)
    deduped = []
    for keyword in keywords:
        if keyword and keyword not in deduped:
            deduped.append(keyword)
    return deduped or ["image"]


def _guess_language(text: str) -> str:
    if not text:
        return "unknown"
    return guess_language(text)


def _string_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a_tokens = set(a.lower().split())
    b_tokens = set(b.lower().split())
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return inter / union if union else 0.0


def _first_text_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    lower_mapping = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        value = mapping.get(key)
        if value is None:
            value = lower_mapping.get(key.lower())
        decoded = _decode_exif_text(value)
        if decoded:
            return decoded
    return ""


def _decode_exif_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for encoding in ("utf-16le", "utf-8", "latin-1"):
            try:
                return value.decode(encoding).strip("\x00 ").strip()
            except UnicodeDecodeError:
                continue
        return ""
    if isinstance(value, tuple) and all(isinstance(item, int) for item in value):
        try:
            return bytes(value).decode("utf-16le").strip("\x00 ").strip()
        except _EXIF_TEXT_ERRORS:
            return ""
    text = str(value).strip()
    return text if text and text.lower() != "none" else ""


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _split_metadata_list(value: str) -> list[str]:
    if not value:
        return []
    result = []
    for item in re.split(r"[,;|，、]", value):
        normalized = item.strip().lower()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _parse_structured_label_text(description: str) -> dict[str, Any]:
    text = (description or "").strip()
    if not text:
        return {}
    parsed = _parse_json_label_text(text) or _parse_key_value_label_text(text)
    if not parsed:
        return {}
    scene = _first_label_value(parsed, ("scene_type", "scene", "场景", "場景"))
    people = _first_label_value(parsed, ("people_count", "person_count", "people", "persons", "人物数", "人数"))
    objects = _first_label_value(
        parsed, ("visible_objects", "objects", "object", "可见物体", "可見物體", "物体", "物件")
    )
    result: dict[str, Any] = {}
    coerced_people = _coerce_int(people)
    if coerced_people is not None:
        result["people_count"] = coerced_people
    if scene:
        result["scene_type"] = str(scene).strip().lower()
    visible_objects = _coerce_label_list(objects)
    if visible_objects:
        result["visible_objects"] = visible_objects
    return result


def _parse_json_label_text(text: str) -> dict[str, Any]:
    candidates = [text]
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_key_value_label_text(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("-*• ")
        if not line:
            continue
        match = re.match(r"([^:：=]+)\s*[:：=]\s*(.+)$", line)
        if not match:
            continue
        key = match.group(1).strip().lower().replace(" ", "_")
        parsed[key] = match.group(2).strip()
    return parsed


def _first_label_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    normalized = {str(key).strip().lower().replace(" ", "_"): value for key, value in mapping.items()}
    for key in keys:
        lookup = key.strip().lower().replace(" ", "_")
        if lookup in normalized:
            return normalized[lookup]
    return None


def _coerce_label_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        raw_items = value
    else:
        raw_items = re.split(r"[,;|，、]", str(value))
    result: list[str] = []
    for item in raw_items:
        normalized = str(item).strip().strip("\"'").lower()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _normalize_capture_time(value: str) -> str:
    if not value:
        return ""
    text = value.strip()
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y:%m:%d",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat(sep=" ")
    except ValueError:
        return text


def _get_gps_ifd(exif: Any) -> dict[int, Any]:
    if not exif:
        return {}
    try:
        gps = exif.get_ifd(_GPS_EXIF_TAG)
        return dict(gps or {})
    except _GPS_IFD_ERRORS:
        raw = exif.get(_GPS_EXIF_TAG)
        return dict(raw or {}) if isinstance(raw, dict) else {}


def _gps_from_metadata(info: dict[str, Any]) -> dict[str, float]:
    lat = _coerce_float(
        _first_text_value(info, ("lengrvis_gps_latitude", "lengrvis_gps_latitude", "gps_latitude", "latitude"))
    )
    lon = _coerce_float(
        _first_text_value(info, ("lengrvis_gps_longitude", "lengrvis_gps_longitude", "gps_longitude", "longitude"))
    )
    if lat is None or lon is None:
        return {}
    return {"latitude": lat, "longitude": lon}


def _gps_from_ifd(gps_ifd: dict[int, Any]) -> dict[str, float]:
    if not gps_ifd:
        return {}
    lat = _gps_coordinate(gps_ifd.get(2), gps_ifd.get(1))
    lon = _gps_coordinate(gps_ifd.get(4), gps_ifd.get(3))
    if lat is None or lon is None:
        return {}
    return {"latitude": lat, "longitude": lon}


def _gps_coordinate(value: Any, ref: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        coordinate = float(value)
    else:
        parts = list(value) if isinstance(value, tuple | list) else []
        if len(parts) < 3:
            return None
        coordinate = (
            _rational_float(parts[0]) + (_rational_float(parts[1]) / 60.0) + (_rational_float(parts[2]) / 3600.0)
        )
    ref_text = _decode_exif_text(ref).upper()
    if ref_text in {"S", "W"}:
        coordinate *= -1
    return coordinate


def _rational_float(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    numerator = getattr(value, "numerator", None)
    denominator = getattr(value, "denominator", None)
    if numerator is not None and denominator:
        return float(numerator) / float(denominator)
    if isinstance(value, tuple) and len(value) == 2 and value[1]:
        return float(value[0]) / float(value[1])
    return float(value)


def _coerce_float(value: str) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _infer_people_count(description: str) -> int:
    text = (description or "").lower()
    if not text:
        return 0
    word_pattern = "|".join(re.escape(word) for word in _NUMBER_WORDS)
    match = re.search(rf"\b({word_pattern}|\d+)\s+(?:people|persons|person|men|women|children|faces?)\b", text)
    if match:
        token = match.group(1)
        return int(token) if token.isdigit() else _NUMBER_WORDS[token]
    if re.search(r"\b(group|crowd|team|family)\b", text):
        return 3
    if re.search(r"\b(person|people|man|woman|child|face|portrait|selfie)\b", text):
        return 1
    return 0


def _infer_scene_type(description: str) -> str:
    text = (description or "").lower()
    for scene, terms in _SCENE_KEYWORDS:
        if any(term in text for term in terms):
            return scene
    return "unknown"


def _infer_visible_objects(description: str) -> list[str]:
    text = (description or "").lower()
    objects = []
    for label, terms in _OBJECT_KEYWORDS:
        if any(term in text for term in terms):
            objects.append(label)
    return objects


def register(registry) -> None:
    defs = [
        ("vision.describe_image", describe_image),
        ("vision.describe_images", describe_images),
        ("vision.ocr_image", ocr_image),
        ("vision.embed_image", embed_image),
        ("vision.compare_images", compare_images),
    ]
    for name, fn in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema={},
                output_schema={},
                risk_level=RiskLevel.R0_READ_ONLY,
                agent_owner="DocumentAgent",
                supports_dry_run=False,
                requires_authorized_path=True,
                execute=fn,
            )
        )
