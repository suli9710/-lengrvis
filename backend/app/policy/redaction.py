from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.policy.policy_rules import BROWSER_CONTENT_PROMPT_INJECTION_WARNING, BROWSER_CONTENT_TRUST

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
    "field_name",
    "locator",
    "otp",
    "passcode",
    "password",
    "passwd",
    "private_key",
    "pwd",
    "secret",
    "selector",
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
    (
        re.compile(
            r"(?i)(api[_-]?key|token|password|secret|authorization|cookie)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.=:/+]{8,})"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=:/+]{8,}\b"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED_CARD_OR_ID]"),
    (re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
]
GENERIC_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")
PUBLIC_MACHINE_LABELS = {
    BROWSER_CONTENT_PROMPT_INJECTION_WARNING,
    BROWSER_CONTENT_TRUST,
}
QUOTED_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?P<quote>['\"])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s,;'\"<>\r\n]+[\\/]|"
    r"(?:/Users|/home|/tmp|/var|/private)/|~[\\/])"
    r"(?:(?!(?P=quote))[^\r\n])+(?P=quote)"
)
LOCAL_FILE_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s,;'\"<>\r\n]+[\\/]|"
    r"(?:/Users|/home|/tmp|/var|/private)/|~[\\/])"
    # Do not scan through another absolute-path prefix. Without this boundary,
    # repeated extensionless paths make every prefix rescan the remaining text.
    r"(?:(?!(?<![A-Za-z0-9])"
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s,;'\"<>\r\n]+[\\/]|"
    r"(?:/Users|/home|/tmp|/var|/private)/|~[\\/]))"
    r"[^,;'‘’\"<>\r\n])*?\.[A-Za-z0-9][A-Za-z0-9_-]{0,15}"
    r"(?=$|[\s,;:'‘’\"<>\])}.。；：，、!?！？?=&/#])"
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s,;'\"<>\r\n]+[\\/]|"
    r"(?:/Users|/home|/tmp|/var|/private)/|~[\\/])"
    r"(?:(?!\s+(?:with\s+)?(?:api[_-]?key|token|password|secret|authorization|cookie)\s*[:=])"
    r"[^,;'\"<>\r\n])+"
)
PUBLIC_STACK_LOCATION_PATTERN = re.compile(r"(?is)file\s+\"[^\"\r\n]+\",\s+line\s+\d+,\s+in\s+[^\r\n]+")
PUBLIC_FILE_NAME_PATTERN = re.compile(
    r"(?i)(?<![\w.-])(?:(?:\.(?:env|npmrc|pypirc|netrc)(?:\.[A-Za-z0-9_-]+)*)|(?:[A-Za-z0-9][A-Za-z0-9_.()-]{0,96}\."
    r"(?:csv|doc|docx|env|ini|json|key|log|md|pdf|pem|png|jpe?g|pptx?|py|sqlite|sqlite3|ts|tsx|txt|xls|xlsx|zip))"
    r")(?=$|[\s,:'\";\])}>.。；：;，、!?！？?=&/#])"
)
PUBLIC_PROMPT_TEXT_PATTERN = re.compile(
    r"(?i)(?:\b(?:(?:hidden\s+)?(?:system|developer|internal)\s+(?:prompt|instructions?|message)|"
    r"hidden\s+(?:prompt|instructions?|message)|"
    r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?|chain[-\s]?of[-\s]?thought)\b|"
    r"\b(?:system|developer|internal)\s*[:=-])"
    r"\s*[:=-]?\s*[^.;\n\r]*"
)


def redact_text(text: str, *, redact_generic_tokens: bool = True) -> str:
    redacted = _redact_url_secrets(text)
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    if redact_generic_tokens:
        redacted = GENERIC_TOKEN_PATTERN.sub(_redact_generic_token, redacted)
    return redacted


def redact_public_text(text: str, *, redact_generic_tokens: bool = True) -> str:
    redacted = redact_text(text, redact_generic_tokens=redact_generic_tokens)
    redacted = PUBLIC_STACK_LOCATION_PATTERN.sub("[REDACTED_STACK]", redacted)
    redacted = QUOTED_LOCAL_PATH_PATTERN.sub("[REDACTED_LOCAL_PATH]", redacted)
    redacted = LOCAL_FILE_PATH_PATTERN.sub("[REDACTED_LOCAL_PATH]", redacted)
    redacted = LOCAL_PATH_PATTERN.sub("[REDACTED_LOCAL_PATH]", redacted)
    redacted = PUBLIC_FILE_NAME_PATTERN.sub("[REDACTED_FILE_NAME]", redacted)
    return PUBLIC_PROMPT_TEXT_PATTERN.sub("[REDACTED_PROMPT]", redacted)


def _redact_generic_token(match: re.Match[str]) -> str:
    token = match.group(0)
    if token in PUBLIC_MACHINE_LABELS:
        return token
    return "[REDACTED_TOKEN]"


def redact_value(value: Any) -> Any:
    return _redact_value(value)


def redact_payload(value: Any) -> Any:
    return _redact_value(value)


def redact_audit_payload(value: Any) -> Any:
    """Public-read redaction for audit payloads.

    Same as :func:`redact_value` but additionally scrubs local absolute paths,
    punctuated public file names, and prompt-injection text from string leaves
    (via :func:`redact_public_text`) so audit export surfaces cannot leak them.
    """
    return _redact_value(value, scrub_local_paths=True)


def redact_audit_storage_payload(value: Any) -> Any:
    """Storage redaction for hash-chained audit payloads.

    Audit rows are an internal evidence source, so path values remain useful for
    diagnostics. Sensitive keys and inline secret patterns are still redacted
    before the row is hashed and persisted.
    """
    return _redact_audit_storage_value(value)


def redact_run_payload(value: Any) -> Any:
    """Redaction for run-engine read surfaces (timeline / replay / progress / state).

    The run timeline is a desktop-token-gated local surface whose structured
    payloads are rendered by the desktop client, so unlike ``redact_value`` this
    helper preserves the payload shape **and identifiers** (no generic 24+ token
    collapse, which would destroy 32-hex run/task/step ids). It still:

    - drops internal ``_``-prefixed keys (e.g. ``state._runtime.data_dir``, which
      otherwise leaks an absolute local path onto the timeline), and
    - redacts values under sensitive key names plus inline secret patterns
      (api keys, bearer tokens, ``sk-`` keys, PEM blocks, cards, email, phone).
    """
    return _redact_run_value(value)


def _redact_run_value(value: Any, *, key: str = "", in_form: bool = False) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for item_key, item in value.items():
            text_key = str(item_key)
            if text_key.startswith("_"):
                continue
            result[text_key] = _redact_run_keyed_value(text_key, item, in_form=in_form)
        return result
    if isinstance(value, list | tuple):
        return [_redact_run_value(item, key=key, in_form=in_form) for item in value]
    if isinstance(value, set):
        return [_redact_run_value(item, key=key, in_form=in_form) for item in sorted(value, key=str)]
    if isinstance(value, str):
        if in_form or _is_form_value_key(key):
            return REDACTED
        # Keep generic tokens so identifiers (run/task/step ids) survive; inline
        # secret patterns (api_key=, Bearer, sk-, PEM, card/email/phone) still apply.
        return redact_text(value, redact_generic_tokens=False)
    if in_form and value is not None:
        return REDACTED
    return value


def _redact_run_keyed_value(key: str, value: Any, *, in_form: bool = False) -> Any:
    if contains_sensitive_key(key):
        if isinstance(value, dict | list | tuple | set):
            return _redact_run_value(value)
        return REDACTED
    child_in_form = in_form or _is_form_container_key(key)
    return _redact_run_value(value, key=key, in_form=child_in_form)


def _redact_audit_storage_value(value: Any, *, key: str = "", in_form: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _redact_audit_storage_keyed_value(str(item_key), item, in_form=in_form)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_audit_storage_value(item, key=key, in_form=in_form) for item in value]
    if isinstance(value, tuple):
        return [_redact_audit_storage_value(item, key=key, in_form=in_form) for item in value]
    if isinstance(value, set):
        return [_redact_audit_storage_value(item, key=key, in_form=in_form) for item in sorted(value, key=str)]
    if isinstance(value, str):
        if in_form or _is_form_value_key(key):
            return REDACTED
        redact_generic_tokens = not _is_path_value_key(key, value)
        return redact_text(value, redact_generic_tokens=redact_generic_tokens)
    if in_form and value is not None:
        return REDACTED
    return value


def _redact_audit_storage_keyed_value(key: str, value: Any, *, in_form: bool = False) -> Any:
    if contains_sensitive_key(key):
        if isinstance(value, dict | list | tuple | set):
            return _redact_audit_storage_value(value)
        return REDACTED
    child_in_form = in_form or _is_form_container_key(key)
    return _redact_audit_storage_value(value, key=key, in_form=child_in_form)


def redact(value: Any) -> Any:
    return _redact_value(value)


def sanitize_text(text: str) -> str:
    return redact_text(text)


def _redact_value(value: Any, *, key: str = "", in_form: bool = False, scrub_local_paths: bool = False) -> Any:
    if isinstance(value, dict):
        return {
            str(item_key): _redact_keyed_value(
                str(item_key), item, in_form=in_form, scrub_local_paths=scrub_local_paths
            )
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, key=key, in_form=in_form, scrub_local_paths=scrub_local_paths) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, key=key, in_form=in_form, scrub_local_paths=scrub_local_paths) for item in value]
    if isinstance(value, set):
        return [
            _redact_value(item, key=key, in_form=in_form, scrub_local_paths=scrub_local_paths)
            for item in sorted(value, key=str)
        ]
    if isinstance(value, str):
        if in_form or _is_form_value_key(key):
            return REDACTED
        redact_generic_tokens = not _is_path_value_key(key, value)
        if scrub_local_paths:
            return redact_public_text(value, redact_generic_tokens=redact_generic_tokens)
        return redact_text(value, redact_generic_tokens=redact_generic_tokens)
    if in_form and value is not None:
        return REDACTED
    return value


def contains_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_keyed_value(key: str, value: Any, *, in_form: bool = False, scrub_local_paths: bool = False) -> Any:
    if contains_sensitive_key(key):
        if isinstance(value, dict | list | tuple | set):
            return _redact_value(value, scrub_local_paths=scrub_local_paths)
        return REDACTED
    child_in_form = in_form or _is_form_container_key(key)
    return _redact_value(value, key=key, in_form=child_in_form, scrub_local_paths=scrub_local_paths)


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
            # Malformed bracketed hosts and ports can make urllib reject the
            # URL before its query or userinfo can be inspected. Treat the
            # whole token as sensitive instead of returning it verbatim.
            return "[REDACTED_URL]"

        netloc = split.netloc.rsplit("@", 1)[-1]
        changed = netloc != split.netloc
        if not split.query:
            if not changed:
                return raw_url
            return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))

        query = []
        for key, value in parse_qsl(split.query, keep_blank_values=True):
            if contains_sensitive_key(key):
                query.append((key, "[REDACTED]"))
                changed = True
            else:
                safe_value = redact_text(value) if value else value
                query.append((key, safe_value))
                changed = changed or safe_value != value
        if not changed:
            return raw_url
        return urlunsplit((split.scheme, netloc, split.path, urlencode(query), split.fragment))

    return re.sub(r"https?://[^\s'\"<>]+", replace, text)
