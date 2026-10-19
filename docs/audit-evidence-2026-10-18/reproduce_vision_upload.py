"""Reproduce image upload despite allow_file_content_upload=false."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-vision-upload-")

from PIL import Image  # noqa: E402

from app.config import AppSettings  # noqa: E402
from app.tools import vision_tools  # noqa: E402

image_path = Path(tempfile.mkdtemp(prefix="lengrvis-image-")) / "receipt.png"
Image.new("RGB", (2, 2), "white").save(image_path)
calls = []


class FakeCloudProvider:
    name = "fake-cloud"

    async def vision(self, path: str, prompt: str) -> str:
        calls.append(("vision", path, prompt[:20]))
        return "fake cloud description"


settings = AppSettings(
    mode="efficiency",
    allow_file_content_upload=False,
    provider_name="openai_compatible",
    base_url="https://example.com/v1",
    api_key="inert",  # noqa: S106
)
vision_tools.get_provider = lambda settings=None, task="default": FakeCloudProvider()  # type: ignore[assignment]
result = vision_tools.describe_image(
    {"path": str(image_path)},
    {"settings": settings, "allowed_directories": [str(image_path.parent)]},
)
print("allow_file_content_upload=", settings.allow_file_content_upload)
print("provider_calls=", calls)
print("ok=", result.get("ok"))
if calls:
    print("CLOUD_VISION_UPLOAD_CONFIRMED")
