from __future__ import annotations

from pathlib import Path

from app.indexer.ocr_service import ocr_image
from app.tools.document_tools import extract_text_from_path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def parse_file(path: Path) -> str:
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        try:
            text = ocr_image(str(path), allowed_directories=[str(path.parent)])
        except Exception:
            text = ""
        return text
    return extract_text_from_path(path)
