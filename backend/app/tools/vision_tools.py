from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.indexer.ocr_service import IMAGE_EXTENSIONS, guess_language, ocr_image_result
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.prompts import load_prompt
from app.llm.registry import get_provider
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS
_GPS_EXIF_TAG = 34853

_DESCRIPTION_METADATA_KEYS = (
    "marvis_description",
    "description",
    "Description",
    "comment",
    "Comment",
    "ImageDescription",
)
_CAPTURED_AT_METADATA_KEYS = (
    "marvis_captured_at",
    "captured_at",
    "date",
    "DateTime",
    "date:create",
    "date:modify",
    "creation_time",
)
_PEOPLE_METADATA_KEYS = ("marvis_people_count", "people_count", "PeopleCount")
_SCENE_METADATA_KEYS = ("marvis_scene_type", "scene_type", "SceneType")
_OBJECT_METADATA_KEYS = ("marvis_visible_objects", "visible_objects", "VisibleObjects", "objects")

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
    from app.core.paths import resolve_authorized

    raw = args.get("path") or args.get("image_path")
    if not raw:
        return None
    allowed = list(context.get("allowed_directories") or [])
    try:
        path = resolve_authorized(raw, allowed)
    except Exception:
        path = Path(raw)
    return path


def _resolve_image_batch(args: dict[str, Any], context: dict[str, Any]) -> list[Path]:
    raw_paths = args.get("paths") or args.get("image_paths") or args.get("images")
    if raw_paths is None and args.get("path"):
        raw_paths = [args["path"]]
    if isinstance(raw_paths, (str, Path)):
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


def _run_vision(prompt: str, image_path: Path, task: str = "vision") -> str:
    try:
        provider = get_provider(task=task)
        return asyncio.run(provider.vision(str(image_path), prompt))
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
    except Exception as exc:  # noqa: BLE001
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
    people_count = _coerce_int(metadata.get("people_count"))
    if people_count is None:
        people_count = _infer_people_count(description)
    scene_type = str(metadata.get("scene_type") or "").strip().lower() or _infer_scene_type(description)
    visible_objects = list(metadata.get("visible_objects") or [])
    if not visible_objects:
        visible_objects = _infer_visible_objects(description)
    label_metadata = {
        key: metadata[key]
        for key in ("captured_at", "gps", "camera", "width", "height", "format")
        if key in metadata
    }
    return {
        "people_count": people_count,
        "scene_type": scene_type,
        "visible_objects": visible_objects,
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
        except Exception:
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
    for item in re.split(r"[,;|]", value):
        normalized = item.strip().lower()
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
    except Exception:
        raw = exif.get(_GPS_EXIF_TAG)
        return dict(raw or {}) if isinstance(raw, dict) else {}


def _gps_from_metadata(info: dict[str, Any]) -> dict[str, float]:
    lat = _coerce_float(_first_text_value(info, ("marvis_gps_latitude", "gps_latitude", "latitude")))
    lon = _coerce_float(_first_text_value(info, ("marvis_gps_longitude", "gps_longitude", "longitude")))
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
    if isinstance(value, (int, float)):
        coordinate = float(value)
    else:
        parts = list(value) if isinstance(value, (tuple, list)) else []
        if len(parts) < 3:
            return None
        coordinate = _rational_float(parts[0]) + (_rational_float(parts[1]) / 60.0) + (_rational_float(parts[2]) / 3600.0)
    ref_text = _decode_exif_text(ref).upper()
    if ref_text in {"S", "W"}:
        coordinate *= -1
    return coordinate


def _rational_float(value: Any) -> float:
    if isinstance(value, (int, float)):
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
        ("vision.compare_images", compare_images),
    ]
    for name, fn in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
                output_schema={},
                risk_level=RiskLevel.R0_READ_ONLY,
                agent_owner="DocumentAgent",
                supports_dry_run=False,
                requires_authorized_path=True,
                execute=fn,
            )
        )
