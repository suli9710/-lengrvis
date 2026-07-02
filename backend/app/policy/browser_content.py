from __future__ import annotations

from typing import Any

from app.policy.policy_rules import BROWSER_CONTENT_TRUST


def browser_content_warning_hits(value: Any) -> set[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "browser_content_warnings":
                hits.update(str(warning) for warning in _as_list(item))
            else:
                hits.update(browser_content_warning_hits(item))
    elif isinstance(value, list | tuple | set):
        for item in value:
            hits.update(browser_content_warning_hits(item))
    return hits


def has_browser_content_trust_label(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "content_trust" and str(item) == BROWSER_CONTENT_TRUST:
                return True
            if has_browser_content_trust_label(item):
                return True
    elif isinstance(value, list | tuple | set):
        return any(has_browser_content_trust_label(item) for item in value)
    return False


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]
