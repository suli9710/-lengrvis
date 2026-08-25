from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any
from urllib.parse import urlparse

from app.policy.policy_rules import (
    BROWSER_CONTENT_PROMPT_INJECTION_WARNING,
    BROWSER_CONTENT_TRUST,
    BROWSER_PROMPT_INJECTION_PATTERNS,
)
from app.policy.redaction import REDACTED, contains_sensitive_key, redact_public_text, redact_text, redact_value
from app.policy.sensitive_values import looks_sensitive_value

SENSITIVE_SELECTOR_TOKENS = {
    "password",
    "pwd",
    "passwd",
    "credit",
    "card",
    "cvv",
    "cvc",
    "ssn",
    "payment",
    "pay",
    "order",
    "delete",
    "token",
    "cookie",
    "otp",
    "2fa",
    "passcode",
    "auth",
    "credential",
}

# WHATWG autocomplete hints for credential, payment, and OTP fields.
SENSITIVE_AUTOCOMPLETE_TOKENS = {
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "cc-name",
}


def field_attributes_are_sensitive(attrs: dict[str, Any]) -> bool:
    """Classify a resolved element from its semantics, not only its selector."""
    if str(attrs.get("type") or "").strip().lower() == "password":
        return True
    autocomplete = str(attrs.get("autocomplete") or "").lower().replace(",", " ")
    if {token.strip() for token in autocomplete.split()} & SENSITIVE_AUTOCOMPLETE_TOKENS:
        return True
    return any(sensitive_selector(str(attrs.get(key) or "")) for key in ("name", "id", "aria-label", "placeholder"))


def sensitive_selector(selector: str) -> bool:
    lowered = selector.lower()
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS)


def sensitive_value(value: Any) -> bool:
    lowered = str(value or "").lower()
    return any(token in lowered for token in SENSITIVE_SELECTOR_TOKENS) or looks_sensitive_value(value)


def sanitize_action(action: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in action.items():
        if key == "url":
            safe[key] = safe_url(str(value or ""))
        elif key in {"selector", "text", "fields", "approval_id"}:
            safe[key] = REDACTED if value not in (None, "", {}) else value
        elif key in {
            "approved",
            "dry_run",
            "kind",
            "max_chars",
            "timeout_ms",
            "width",
            "height",
            "full_page",
            "delta_y",
            "y",
        }:
            safe[key] = value
        else:
            safe[key] = redact_event_value(value)
    return safe


def result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"ok": bool(result.get("ok"))}
    for key in ("url", "title", "adapter", "present", "changed_paths", "rollback_info", "screenshot_url", "path"):
        if key in result:
            metadata[key] = result[key]
    if result.get("error"):
        metadata["error"] = result["error"]
    if result.get("text") is not None:
        metadata["text_chars"] = len(str(result.get("text") or ""))
    if isinstance(result.get("links"), list):
        metadata["link_count"] = len(result.get("links") or [])
    if has_browser_content(result):
        metadata["content_trust"] = BROWSER_CONTENT_TRUST
        warnings = browser_content_warnings(result)
        if warnings:
            metadata["browser_content_warnings"] = warnings
    return metadata


def safe_result(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for item_key, item in value.items():
            text_key = str(item_key)
            if text_key == "url":
                result[text_key] = safe_url(str(item or ""))
            elif text_key in {"path", "screenshot_url"}:
                result[text_key] = artifact_ref(item)
            elif text_key in {"title", "error"}:
                result[text_key] = safe_text(str(item or ""))
            else:
                result[text_key] = safe_result(item, key=text_key)
        if has_browser_content(value):
            result["content_trust"] = BROWSER_CONTENT_TRUST
            warnings = browser_content_warnings(value)
            if warnings:
                result["browser_content_warnings"] = warnings
        return result
    if isinstance(value, list):
        return [safe_result(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [safe_result(item, key=key) for item in value]
    if isinstance(value, str):
        return value if key == "text" else safe_text(value)
    return value


def has_browser_content(result: dict[str, Any]) -> bool:
    return result.get("text") is not None or isinstance(result.get("links"), list)


def browser_content_warnings(result: dict[str, Any]) -> list[str]:
    inspected_parts: list[str] = []
    if result.get("text") is not None:
        inspected_parts.append(str(result.get("text") or ""))
    if result.get("title") is not None:
        inspected_parts.append(str(result.get("title") or ""))
    for link in result.get("links") or []:
        if isinstance(link, dict):
            inspected_parts.append(str(link.get("title") or ""))
            inspected_parts.append(str(link.get("url") or ""))
    inspected = "\n".join(inspected_parts)
    if any(re.search(pattern, inspected, flags=re.IGNORECASE) for pattern in BROWSER_PROMPT_INJECTION_PATTERNS):
        return [BROWSER_CONTENT_PROMPT_INJECTION_WARNING]
    return []


def redact_event_value(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key == "url":
                result[key] = safe_url(str(item or ""))
            elif text_key in {"path", "screenshot_url"}:
                result[key] = artifact_ref(item)
            elif text_key in {"content_trust", "browser_content_warnings"}:
                result[key] = safe_metadata_label_value(item)
            elif text_key in {"text", "selector", "fields"}:
                result[key] = REDACTED if item not in (None, "", {}) else item
            elif contains_sensitive_key(text_key):
                result[key] = redact_event_value(redact_value({text_key: item}).get(text_key))
            else:
                result[key] = redact_event_value(item)
        return result
    if isinstance(value, list):
        return [redact_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_event_value(item) for item in value]
    redacted = redact_value(value)
    return safe_text(redacted) if isinstance(redacted, str) else redacted


def safe_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(redact_text(url))
    except ValueError:
        return redact_text(url)
    if not parsed.query:
        return redact_text(url)
    return parsed._replace(query=REDACTED).geturl()


def artifact_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    candidate = parsed.path if parsed.scheme else text.split("?", 1)[0].split("#", 1)[0]
    return redact_text(PurePath(candidate.replace("\\", "/")).name)


def safe_metadata_label_value(value: Any) -> Any:
    if isinstance(value, list):
        return [safe_metadata_label_value(item) for item in value]
    if isinstance(value, tuple):
        return [safe_metadata_label_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_metadata_label_value(item) for key, item in value.items()}
    if isinstance(value, str):
        return redact_text(value, redact_generic_tokens=False)
    return value


def safe_text(text: str) -> str:
    return redact_public_text(text) if text else ""


def safe_browser_error(value: Any) -> str:
    return safe_text(str(redact_value(str(value or "")) or "")) or value.__class__.__name__
