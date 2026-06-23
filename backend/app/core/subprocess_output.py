from __future__ import annotations

import locale
import os
from typing import Any


def decode_process_output(value: Any, *, fallback_encoding: str = "utf-8") -> str:
    """Decode captured subprocess output without trusting the host locale."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes | bytearray):
        return str(value)

    data = bytes(value)
    if not data:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if utf16_encoding := _detect_utf16_without_bom(data):
        try:
            return data.decode(utf16_encoding)
        except UnicodeDecodeError:
            pass

    # Order matters. Try strict codecs that *raise* on mismatching input before
    # any single-byte code page: UTF-8 first, then the legacy Chinese multi-byte
    # code page that Windows commands commonly emit, and only then the
    # single-byte ANSI/locale code pages. Single-byte code pages (cp1252/latin-1/mbcs and, on an
    # English Windows runner, locale.getpreferredencoding()) decode *any* byte
    # sequence without raising, so consulting them first would mask the correct
    # decoding -- e.g. GBK-encoded CJK output would be returned as cp1252
    # mojibake.
    candidates = ["utf-8-sig", fallback_encoding, "gbk"]
    candidates.append(locale.getpreferredencoding(False))
    if os.name == "nt":
        candidates.extend(["mbcs", "cp65001"])

    seen: set[str] = set()
    for encoding in candidates:
        normalized = str(encoding or "").casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode(fallback_encoding, errors="replace")


def _detect_utf16_without_bom(data: bytes) -> str:
    sample = data[:400]
    if len(sample) < 8:
        return ""

    even_bytes = sample[0::2]
    odd_bytes = sample[1::2]
    even_nuls = even_bytes.count(0)
    odd_nuls = odd_bytes.count(0)
    even_ratio = even_nuls / max(1, len(even_bytes))
    odd_ratio = odd_nuls / max(1, len(odd_bytes))

    if odd_ratio >= 0.35 and even_ratio <= 0.05:
        return "utf-16-le"
    if even_ratio >= 0.35 and odd_ratio <= 0.05:
        return "utf-16-be"
    return ""
