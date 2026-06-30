from __future__ import annotations

import re
from typing import Any

CARDLIKE_DIGITS = re.compile(r"\d[\d\s-]{11,}\d")
BLOCKED_REDACTION_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|token|password|secret|authorization|cookie)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.=:/+]{8,})"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=:/+]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
GENERIC_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")


def looks_sensitive_value(value: Any) -> bool:
    """Return true when a browser write value should be filled manually."""
    if value is None:
        return False
    text = str(value)
    if not text:
        return False
    if any(pattern.search(text) for pattern in BLOCKED_REDACTION_PATTERNS):
        return True
    if GENERIC_TOKEN_PATTERN.search(text):
        return True
    return any(_luhn_valid(_digits(candidate.group(0))) for candidate in CARDLIKE_DIGITS.finditer(text))


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _luhn_valid(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        number = int(char)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0
