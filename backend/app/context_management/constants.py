from __future__ import annotations

import re

CHARS_PER_TOKEN = 4
JSON_CHARS_PER_TOKEN = 2
# CJK text tokenizes far denser than ASCII (~1-1.5 chars/token vs ~4); a flat
# len/4 underestimates Chinese contexts 3-5x, so auto-compaction would never
# trigger before the provider rejects the prompt.
CJK_CHARS_PER_TOKEN = 1.6
_CJK_CHARS_RE = re.compile(
    "["
    "\u3000-\u303f"  # CJK punctuation
    "\u3040-\u30ff"  # Hiragana / Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
    "\uff00-\uffef"  # Fullwidth forms
    "]"
)
IMAGE_OR_DOCUMENT_TOKENS = 2000
SUMMARY_RESERVED_TOKENS = 20000
ATTACHMENT_BLOCK_TYPES = {"image", "image_url", "document", "input_audio"}
PROMPT_TOO_LONG_MARKERS = (
    "context_length_exceeded",
    "context window",
    "context_window_exceeded",
    "maximum context",
    "model_context_window_exceeded",
    "prompt is too long",
    "prompt too long",
    "prompt-too-long",
    "too many tokens",
    "input is too long",
    "request too large",
    "maximum prompt length",
)
COMPACT_BOUNDARY_TYPES = {"manual_compact", "auto_compact", "reactive_compact"}
