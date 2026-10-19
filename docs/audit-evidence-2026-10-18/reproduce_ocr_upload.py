"""Reproduce OCR cloud fallback despite allow_file_content_upload=false."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-ocr-upload-")

from PIL import Image  # noqa: E402

from app.config import AppSettings  # noqa: E402
from app.indexer import ocr_service  # noqa: E402

image_path = Path(tempfile.mkdtemp(prefix="lengrvis-ocr-image-")) / "scan.png"
Image.new("RGB", (2, 2), "white").save(image_path)
calls = []


class FakeCloudProvider:
    name = "fake-cloud"

    async def ocr(self, path: str) -> str:
        calls.append(("ocr", path))
        return "fake cloud OCR text"


settings = AppSettings(
    mode="efficiency",
    allow_file_content_upload=False,
    provider_name="openai_compatible",
    base_url="https://example.com/v1",
    api_key="inert",  # noqa: S106
)
ocr_service.get_provider = lambda settings=None, task="default": FakeCloudProvider()  # type: ignore[assignment]
result = ocr_service.ocr_image_result(image_path, settings=settings)
print("allow_file_content_upload=", settings.allow_file_content_upload)
print("provider_calls=", calls)
print("ok=", result.ok, "source=", result.source)
if calls:
    print("CLOUD_OCR_UPLOAD_CONFIRMED")
