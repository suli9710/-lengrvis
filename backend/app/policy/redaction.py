from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "***"

SENSITIVE_KEY_FRAGMENTS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "bearer",
    "card",
    "cookie",
    "credential",
    "credentials",
    "cvv",
    "cvc",
    "form_value",
    "otp",
    "passcode",
    "password",
    "passwd",
    "private_key",
    "pwd",
    "secret",
    "session",
    "ssn",
    "token",
    "value",
}

FORM_CONTAINER_KEYS = {"field", "fields", "form", "form_data", "form_values", "inputs"}
FORM_VALUE_KEYS = {"input", "new_value", "old_value", "text", "value", "values"}
PATH_VALUE_KEYS = {
    "destination",
    "destination_path",
    "from",
    "path",
    "source",
    "source_path",
    "target",
    "target_folder",
    "target_path",
    "to",
}

PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization|cookie)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.=:/+]{8,})"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=:/+]{8,}\b"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED_CARD_OR_ID]"),
    (re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
]
GENERIC_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")


def redact_text(text: str, *, redact_generic_tokens: bool = True) -> str:
    redacted = _redact_url_secrets(text)
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if redact_generic_tokens:
        redacted = GENERIC_TOKEN_PATTERN.sub("[REDACTED_TOKEN]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    return _redact_value(value)


def redact_payload(value: Any) -> Any:
    return _redact_value(value)


def redact(value: Any) -> Any:
    return _redact_value(value)


def sanitize_text(text: str) -> str:
    return redact_text(text)


def _redact_value(value: Any, *, key: str = "", in_form: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _redact_keyed_value(str(item_key), item, in_form=in_form)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, key=key, in_form=in_form) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, key=key, in_form=in_form) for item in value]
    if isinstance(value, set):
        return [_redact_value(item, key=key, in_form=in_form) for item in sorted(value, key=str)]
    if isinstance(value, str):
        if in_form or _is_form_value_key(key):
            return REDACTED
        return redact_text(value, redact_generic_tokens=not _is_path_value_key(key, value))
    if in_form and value is not None:
        return REDACTED
    return value


def contains_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_keyed_value(key: str, value: Any, *, in_form: bool = False) -> Any:
    if contains_sensitive_key(key):
        if isinstance(value, (dict, list, tuple, set)):
            return _redact_value(value)
        return REDACTED
    child_in_form = in_form or _is_form_container_key(key)
    return _redact_value(value, key=key, in_form=child_in_form)


def _is_form_container_key(key: str) -> bool:
    return key.replace("-", "_").casefold() in FORM_CONTAINER_KEYS


def _is_form_value_key(key: str) -> bool:
    return key.replace("-", "_").casefold() in FORM_VALUE_KEYS


def _is_path_value_key(key: str, value: str) -> bool:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key not in PATH_VALUE_KEYS and not normalized_key.endswith("_path"):
        return False
    return bool(re.search(r"^[A-Za-z]:[\\/]", value) or re.search(r"^[/~]", value) or re.search(r"[\\/]", value))


def _redact_url_secrets(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        try:
            split = urlsplit(raw_url)
        except ValueError:
            return raw_url
        if not split.query:
            return raw_url
        query = []
        changed = False
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            if contains_sensitive_key(key):
                query.append((key, "[REDACTED]"))
                changed = True
            else:
                query.append((key, redact_text(value) if value else value))
        if not changed:
            return raw_url
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))

    return re.sub(r"https?://[^\s'\"<>]+", replace, text)
