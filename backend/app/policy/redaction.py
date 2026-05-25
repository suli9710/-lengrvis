from __future__ import annotations

import re


PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,})"), r"\1=[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b\d{13,19}\b"), "[REDACTED_CARD_OR_ID]"),
    (re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
