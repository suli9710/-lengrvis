from __future__ import annotations

import base64
import sys
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from app.core.schemas import now_iso


DEFAULT_CAPTURE_WIDTH = 1280
DEFAULT_CAPTURE_HEIGHT = 720
DEFAULT_FPS = 2.0
MAX_FPS = 5.0
DEFAULT_JPEG_QUALITY = 50
MIN_JPEG_QUALITY = 10
MAX_JPEG_QUALITY = 95


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    image_base64: str
    timestamp: str
    width: int
    height: int
    original_width: int
    original_height: int
    quality: int


def capture_screen(
    *,
    max_width: int = DEFAULT_CAPTURE_WIDTH,
    max_height: int = DEFAULT_CAPTURE_HEIGHT,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> str:
    """Capture the current desktop as a JPEG and return raw base64."""
    return capture_screen_frame(max_width=max_width, max_height=max_height, quality=quality).image_base64


def capture_screen_frame(
    *,
    max_width: int = DEFAULT_CAPTURE_WIDTH,
    max_height: int = DEFAULT_CAPTURE_HEIGHT,
    quality: int = DEFAULT_JPEG_QUALITY,
) -> ScreenFrame:
    if sys.platform != "win32":
        raise RuntimeError("Remote desktop screen capture is only supported on Windows.")

    image = _grab_screen()
    original_width, original_height = image.size
    resized = _resize_for_stream(image, max_width=max_width, max_height=max_height)
    buffer = BytesIO()
    jpeg_quality = normalize_quality(quality)
    resized.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    return ScreenFrame(
        image_base64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        timestamp=now_iso(),
        width=resized.size[0],
        height=resized.size[1],
        original_width=original_width,
        original_height=original_height,
        quality=jpeg_quality,
    )


def normalize_fps(value: float | int | str | None) -> float:
    try:
        fps = float(value) if value is not None else DEFAULT_FPS
    except (TypeError, ValueError):
        fps = DEFAULT_FPS
    return max(0.1, min(MAX_FPS, fps))


def frame_interval_seconds(value: float | int | str | None) -> float:
    return 1.0 / normalize_fps(value)


def normalize_quality(value: float | int | str | None) -> int:
    try:
        quality = int(value) if value is not None else DEFAULT_JPEG_QUALITY
    except (TypeError, ValueError):
        quality = DEFAULT_JPEG_QUALITY
    return max(MIN_JPEG_QUALITY, min(MAX_JPEG_QUALITY, quality))


def _grab_screen() -> Image.Image:
    from PIL import ImageGrab

    try:
        image = ImageGrab.grab(all_screens=True)
    except TypeError:
        image = ImageGrab.grab()
    return image.convert("RGB")


def _resize_for_stream(image: Image.Image, *, max_width: int, max_height: int) -> Image.Image:
    target_width = max(1, int(max_width or DEFAULT_CAPTURE_WIDTH))
    target_height = max(1, int(max_height or DEFAULT_CAPTURE_HEIGHT))
    resized = image.copy()
    resized.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    return resized
