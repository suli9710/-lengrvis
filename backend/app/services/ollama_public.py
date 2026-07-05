"""Public-safe Ollama status text helpers."""

from __future__ import annotations

import re
from typing import Any

from app.policy.redaction import redact_public_text

PUBLIC_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
MAX_PUBLIC_ERROR_CHARS = 600


def public_text(value: Any, fallback: str = "Local AI action failed.") -> str:
    text = str(value or fallback)
    without_urls = PUBLIC_URL_RE.sub("[REDACTED_URL]", text)
    redacted = redact_public_text(without_urls)
    redacted = " ".join(redacted.split())
    if len(redacted) > MAX_PUBLIC_ERROR_CHARS:
        return f"{redacted[: MAX_PUBLIC_ERROR_CHARS - 1].rstrip()}..."
    return redacted


def public_model_name(model: str, *, fallback: str) -> str:
    return public_text(model, fallback=fallback)


def public_model_names(models: list[str], *, fallback: str) -> list[str]:
    return [public_model_name(model, fallback=fallback) for model in models if str(model or "").strip()]


def public_manifest_string(value: Any) -> str:
    return public_text(value, fallback="")


def public_bundle_manifest_value(key: str, value: Any) -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key == "path" or normalized_key.endswith("_path"):
        return ""
    if isinstance(value, str):
        return public_manifest_string(value)
    if isinstance(value, dict):
        return {str(item_key): public_bundle_manifest_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list):
        return [public_bundle_manifest_value("", item) for item in value]
    return value


def public_bundle_manifest_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {str(key): public_bundle_manifest_value(str(key), value) for key, value in summary.items()}
