from __future__ import annotations

import base64
import ctypes
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
    screen_origin_x: int = 0
    screen_origin_y: int = 0


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
    # Platform capability is enforced by _grab_screen() (PIL ImageGrab), which
    # raises on environments without a usable display. Callers such as the
    # remote-screen WebSocket loop catch that and surface a graceful
    # capture-failed error, so we must not hard-block non-Windows here: doing so
    # broke cross-platform CI once the screen WebSocket could actually connect.
    image = _grab_screen()
    screen_origin_x, screen_origin_y = _screen_origin_from_grabbed_image(image)
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
        screen_origin_x=screen_origin_x,
        screen_origin_y=screen_origin_y,
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
        screen_origin = _virtual_screen_origin()
    except TypeError:
        image = ImageGrab.grab()
        screen_origin = (0, 0)
    converted = image.convert("RGB")
    # Attaching the origin is an optional optimization; readers fall back to
    # _virtual_screen_origin() when the attribute is missing.
    try:
        converted._lengrvis_screen_origin = screen_origin
    except Exception:  # noqa: S110, BLE001
        pass
    return converted


def _screen_origin_from_grabbed_image(image: Image.Image) -> tuple[int, int]:
    origin = getattr(image, "_lengrvis_screen_origin", None)
    if isinstance(origin, tuple | list) and len(origin) >= 2:
        try:
            return int(origin[0]), int(origin[1])
        except (TypeError, ValueError):
            return 0, 0
    return _virtual_screen_origin()


def _virtual_screen_origin() -> tuple[int, int]:
    if sys.platform != "win32":
        return 0, 0
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77))
    except Exception:  # noqa: BLE001
        return 0, 0


def _resize_for_stream(image: Image.Image, *, max_width: int, max_height: int) -> Image.Image:
    target_width = max(1, int(max_width or DEFAULT_CAPTURE_WIDTH))
    target_height = max(1, int(max_height or DEFAULT_CAPTURE_HEIGHT))
    resized = image.copy()
    resized.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    return resized
