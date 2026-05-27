from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from app.config import AppSettings, get_base_settings
from app.core import db
from app.core.schemas import new_id, now_iso
from app.perception.schemas import AppContext, ScreenState


DEFAULT_SENSITIVE_WINDOW_PATTERNS = [
    "password",
    "passcode",
    "1password",
    "bitwarden",
    "lastpass",
    "keychain",
    "credential",
    "secret",
    "token",
    "cookie",
    "otp",
    "2fa",
    "api key",
    "bank",
    "card",
    "payment",
    "checkout",
    "wallet",
]

DEFAULT_SENSITIVE_FIELD_NAMES = [
    "password",
    "passcode",
    "pin",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "set-cookie",
    "credential",
    "credit_card",
    "card_number",
    "cvv",
    "ssn",
]

IMAGE_KEYS = {"image", "image_base64", "screenshot", "screenshot_base64", "frame", "frame_base64"}
UI_TREE_KEYS = {"ui_tree", "uitree", "accessibility_tree", "ax_tree", "dom_tree", "elements", "ui_elements"}
OCR_TEXT_KEYS = {
    "ocr",
    "ocr_text",
    "ocr_full_text",
    "full_ocr_text",
    "recognized_text",
    "raw_text",
    "extracted_text",
}
MAX_SUMMARY_CHARS = 500
MAX_PAYLOAD_CHARS = 800
MAX_LIST_ITEMS = 12
REDACTED = "[suppressed]"


def store_observation(event: Any, settings: AppSettings | None = None) -> dict[str, Any] | None:
    effective = settings or get_base_settings()
    if not getattr(effective, "perception_storage_enabled", True):
        return None

    payload = _event_payload(event)
    suppressed = is_sensitive_context(payload=event, settings=effective) or _contains_sensitive_field(payload, effective)
    summary = _summary(event)
    if suppressed:
        summary = "Perception observation suppressed for a sensitive window."

    app_context = _extract_app_context(event)
    body = {
        "id": new_id("pobs"),
        "task_id": str(getattr(event, "task_id", "") or ""),
        "event_id": str(getattr(event, "id", "") or ""),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "environment_type": _enum_value(getattr(event, "environment_type", "")),
        "source_agent": str(getattr(event, "source_agent", "") or ""),
        "summary": _clip(summary),
        "suppressed": suppressed,
        "process_name": "" if suppressed else str(getattr(app_context, "process_name", "") or ""),
        "window_title": "" if suppressed else str(getattr(app_context, "active_window_title", "") or ""),
        "screen_state_id": _screen_state_id(event),
        "payload": _minimal_suppressed_payload(event) if suppressed else _sanitize(payload, effective),
        "created_at": str(getattr(event, "timestamp", "") or now_iso()),
    }
    db.insert_perception_observation(body)
    return body


def store_suggestion(suggestion: Any, source_event: Any | None = None, settings: AppSettings | None = None) -> dict[str, Any] | None:
    effective = settings or get_base_settings()
    if not getattr(effective, "perception_storage_enabled", True):
        return None

    payload = _event_payload(suggestion)
    if source_event is not None:
        payload["source_observation"] = _event_payload(source_event)
    suppressed = (
        is_sensitive_context(payload=source_event, settings=effective)
        or is_sensitive_context(payload=suggestion, settings=effective)
        or _contains_sensitive_field(payload, effective)
    )
    summary = _summary(suggestion)
    if suppressed:
        summary = "Perception suggestion suppressed for a sensitive window."

    body = {
        "id": new_id("psug"),
        "task_id": str(getattr(suggestion, "task_id", "") or getattr(source_event, "task_id", "") or ""),
        "suggestion_id": str(getattr(suggestion, "id", "") or ""),
        "rule_id": str(getattr(suggestion, "rule_id", "") or ""),
        "severity": str(getattr(suggestion, "severity", "") or "info"),
        "title": "" if suppressed else _clip(str(getattr(suggestion, "title", "") or ""), 200),
        "summary": _clip(summary),
        "suppressed": suppressed,
        "payload": _minimal_suppressed_payload(suggestion) if suppressed else _sanitize(payload, effective),
        "created_at": str(getattr(suggestion, "timestamp", "") or now_iso()),
    }
    db.insert_perception_suggestion(body)
    return body


def sanitize_for_storage(value: Any, settings: AppSettings | None = None) -> Any:
    return _sanitize(_to_plain(value), settings or get_base_settings())


def is_sensitive_context(
    *,
    screen_state: ScreenState | dict[str, Any] | None = None,
    app_context: AppContext | dict[str, Any] | None = None,
    payload: Any | None = None,
    settings: AppSettings | None = None,
) -> bool:
    effective = settings or get_base_settings()
    if screen_state is not None:
        context = app_context or _context_from_screen_state(screen_state)
        event = {
            "screen_state": screen_state,
            "app_context": context,
            "subject": _screen_description(screen_state),
        }
        if _is_sensitive_event(event, effective) or _contains_sensitive_field(screen_state, effective):
            return True
        if _contains_sensitive_control(screen_state, effective):
            return True
    if app_context is not None:
        if _is_sensitive_event({"app_context": app_context}, effective):
            return True
        if _contains_sensitive_field(app_context, effective):
            return True
        if _contains_sensitive_control(app_context, effective):
            return True
    if payload is not None:
        if _is_sensitive_event(payload, effective):
            return True
        if _contains_sensitive_field(payload, effective):
            return True
        if _contains_sensitive_control(payload, effective):
            return True
    return False


def screen_state_summary(state: ScreenState | dict[str, Any] | None, settings: AppSettings | None = None) -> dict[str, Any] | None:
    if state is None:
        return None
    effective = settings or get_base_settings()
    if is_sensitive_context(screen_state=state, settings=effective):
        return {
            "available": True,
            "sensitive_context_suppressed": True,
        }
    plain = sanitize_for_storage(state, effective)
    if not isinstance(plain, dict):
        return None
    return {
        "id": str(plain.get("id") or ""),
        "captured_at": str(plain.get("captured_at") or ""),
        "description": _clip(str(plain.get("description") or ""), 300),
        "width": int(plain.get("width") or 0),
        "height": int(plain.get("height") or 0),
        "original_width": int(plain.get("original_width") or 0),
        "original_height": int(plain.get("original_height") or 0),
        "tags": list(plain.get("tags") or [])[:MAX_LIST_ITEMS],
        "structured_labels": _sanitize(plain.get("structured_labels") or {}, effective),
        "metadata": _sanitize(plain.get("metadata") or {}, effective),
        "app_context": app_context_summary(_context_from_screen_state(state), effective),
    }


def app_context_summary(context: AppContext | dict[str, Any] | None, settings: AppSettings | None = None) -> dict[str, Any] | None:
    if context is None:
        return None
    effective = settings or get_base_settings()
    if is_sensitive_context(app_context=context, settings=effective):
        return {
            "available": True,
            "sensitive_context_suppressed": True,
        }
    plain = sanitize_for_storage(context, effective)
    if not isinstance(plain, dict):
        return None
    focus = plain.get("focus_control")
    focus_summary = {}
    if isinstance(focus, dict):
        focus_summary = {
            "role": _clip(str(focus.get("role") or ""), 80),
            "name": _clip(str(focus.get("name") or ""), 120),
        }
    return {
        "platform": str(plain.get("platform") or ""),
        "available": bool(plain.get("available", False)),
        "active_window_title": _clip(str(plain.get("active_window_title") or ""), 200),
        "process_name": _clip(str(plain.get("process_name") or ""), 120),
        "process_id": plain.get("process_id"),
        "focus_control": focus_summary or None,
    }


def perception_context_summary(context: dict[str, Any] | None, settings: AppSettings | None = None) -> dict[str, Any]:
    if not context:
        return {}
    screen_state = context.get("screen_state")
    app_context = context.get("app_context")
    if app_context is None:
        app_context = _context_from_screen_state(screen_state)
    effective = settings or get_base_settings()
    if is_sensitive_context(screen_state=screen_state, app_context=app_context, settings=effective):
        return {"sensitive_context_suppressed": True}
    result: dict[str, Any] = {}
    screen_summary = screen_state_summary(screen_state, effective)
    app_summary = app_context_summary(app_context, effective)
    if screen_summary is not None:
        result["screen_state"] = screen_summary
    if app_summary is not None:
        result["app_context"] = app_summary
    return result


def _event_payload(event: Any) -> dict[str, Any]:
    if event is None:
        return {}
    if isinstance(event, BaseModel):
        data = event.model_dump(mode="json")
    elif isinstance(event, dict):
        data = dict(event)
    else:
        data = {
            key: value
            for key, value in vars(event).items()
            if not key.startswith("_")
        } if hasattr(event, "__dict__") else {"value": str(event)}
    data.pop("image_base64", None)
    data.pop("ui_elements", None)
    return data


def _sanitize(value: Any, settings: AppSettings) -> Any:
    return _sanitize_inner(_to_plain(value), settings, parent_key="")


def _sanitize_inner(value: Any, settings: AppSettings, *, parent_key: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = _normalize_key(key)
            if normalized in IMAGE_KEYS or normalized in UI_TREE_KEYS or normalized in OCR_TEXT_KEYS:
                result[key] = REDACTED
                continue
            if _is_sensitive_field_name(key, settings):
                result[key] = REDACTED
                continue
            result[key] = _sanitize_inner(raw_value, settings, parent_key=key)
        return result
    if isinstance(value, list):
        items = value[:MAX_LIST_ITEMS]
        sanitized = [_sanitize_inner(item, settings, parent_key=parent_key) for item in items]
        if len(value) > MAX_LIST_ITEMS:
            sanitized.append({"truncated_count": len(value) - MAX_LIST_ITEMS})
        return sanitized
    if isinstance(value, str):
        if _normalize_key(parent_key) in OCR_TEXT_KEYS:
            return REDACTED
        return _clip(value, MAX_PAYLOAD_CHARS)
    return value


def _to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _extract_app_context(event: Any) -> AppContext | None:
    if event is None:
        return None
    context = getattr(event, "app_context", None)
    if context is not None:
        return context
    state = getattr(event, "screen_state", None)
    if isinstance(state, ScreenState):
        return state.app_context
    if isinstance(event, dict):
        raw = event.get("app_context") or {}
        if isinstance(raw, AppContext):
            return raw
        if isinstance(raw, dict):
            try:
                return AppContext.model_validate(raw)
            except Exception:
                return None
    return None


def _context_from_screen_state(state: ScreenState | dict[str, Any] | None) -> AppContext | dict[str, Any] | None:
    if isinstance(state, ScreenState):
        return state.app_context
    if isinstance(state, dict):
        raw = state.get("app_context")
        return raw if isinstance(raw, dict) or isinstance(raw, AppContext) else None
    return None


def _screen_description(state: ScreenState | dict[str, Any] | None) -> str:
    if isinstance(state, ScreenState):
        return state.description
    if isinstance(state, dict):
        return str(state.get("description") or "")
    return ""


def _screen_state_id(event: Any) -> str:
    state = getattr(event, "screen_state", None)
    if isinstance(state, ScreenState):
        return state.id
    if isinstance(event, dict):
        raw = event.get("screen_state") or {}
        if isinstance(raw, dict):
            return str(raw.get("id") or "")
    return ""


def _summary(event: Any) -> str:
    if event is None:
        return ""
    summary = getattr(event, "summary", None)
    if callable(summary):
        try:
            return str(summary() or "")
        except Exception:
            return ""
    if isinstance(event, dict):
        return str(event.get("summary") or event.get("summary_text") or event.get("title") or "")
    return str(event)


def _minimal_suppressed_payload(event: Any) -> dict[str, Any]:
    return {
        "event_type": str(getattr(event, "event_type", "") or ""),
        "environment_type": _enum_value(getattr(event, "environment_type", "")),
        "suppression_reason": "sensitive_window_or_field",
    }


def _is_sensitive_event(event: Any, settings: AppSettings) -> bool:
    if event is None:
        return False
    context = _extract_app_context(event)
    haystack = []
    if context is not None:
        haystack.extend([context.active_window_title, context.process_name])
    if isinstance(event, dict):
        haystack.extend([event.get("window_title"), event.get("process_name"), event.get("subject")])
        raw_context = event.get("app_context")
        if isinstance(raw_context, dict):
            haystack.extend([raw_context.get("active_window_title"), raw_context.get("process_name")])
    else:
        haystack.append(getattr(event, "subject", ""))
    text = " ".join(str(item or "") for item in haystack).lower()
    return any(_matches_pattern(text, pattern) for pattern in _sensitive_window_patterns(settings))


def _sensitive_window_patterns(settings: AppSettings) -> list[str]:
    configured = list(getattr(settings, "perception_sensitive_window_patterns", []) or [])
    return [str(item).lower() for item in (configured or DEFAULT_SENSITIVE_WINDOW_PATTERNS) if str(item).strip()]


def _sensitive_field_names(settings: AppSettings) -> list[str]:
    configured = list(getattr(settings, "perception_sensitive_field_names", []) or [])
    return [str(item).lower() for item in (configured or DEFAULT_SENSITIVE_FIELD_NAMES) if str(item).strip()]


def _is_sensitive_field_name(key: str, settings: AppSettings) -> bool:
    normalized = _normalize_key(key)
    for field in _sensitive_field_names(settings):
        candidate = _normalize_key(field)
        if candidate and (candidate == normalized or candidate in normalized):
            return True
    return False


def _contains_sensitive_field(value: Any, settings: AppSettings) -> bool:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        for key, item in plain.items():
            normalized = _normalize_key(str(key))
            if normalized in IMAGE_KEYS or normalized in UI_TREE_KEYS or normalized in OCR_TEXT_KEYS:
                continue
            if _is_sensitive_field_name(str(key), settings) or _contains_sensitive_field(item, settings):
                return True
    if isinstance(plain, list):
        return any(_contains_sensitive_field(item, settings) for item in plain)
    return False


def _contains_sensitive_term(value: Any, settings: AppSettings) -> bool:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        for key, item in plain.items():
            normalized = _normalize_key(str(key))
            if normalized in IMAGE_KEYS or normalized in UI_TREE_KEYS or normalized in OCR_TEXT_KEYS:
                continue
            if _contains_sensitive_term(item, settings):
                return True
        return False
    if isinstance(plain, list):
        return any(_contains_sensitive_term(item, settings) for item in plain[:MAX_LIST_ITEMS])
    if isinstance(plain, str):
        text = plain.lower()
        return any(_matches_pattern(text, pattern) for pattern in _sensitive_window_patterns(settings))
    return False


def _contains_sensitive_control(value: Any, settings: AppSettings) -> bool:
    plain = _to_plain(value)
    if isinstance(plain, dict):
        for key, item in plain.items():
            normalized = _normalize_key(str(key))
            if normalized in {"focus_control", "focused_control"}:
                if _control_text_is_sensitive(item, settings):
                    return True
            if normalized in {"ui_elements", "elements", "controls", "fields"}:
                items = item if isinstance(item, list) else [item]
                if any(_control_text_is_sensitive(candidate, settings) for candidate in items):
                    return True
            if _contains_sensitive_control(item, settings):
                return True
        return False
    if isinstance(plain, list):
        return any(_contains_sensitive_control(item, settings) for item in plain)
    return False


def _control_text_is_sensitive(value: Any, settings: AppSettings) -> bool:
    plain = _to_plain(value)
    if not isinstance(plain, dict):
        return False
    parts: list[str] = []
    for key in ("role", "name", "text", "type", "control_type", "automation_id"):
        raw = plain.get(key)
        if raw is not None:
            parts.append(str(raw))
    attributes = plain.get("attributes")
    if isinstance(attributes, dict):
        for key, raw in attributes.items():
            parts.append(str(key))
            if isinstance(raw, (str, int, float, bool)):
                parts.append(str(raw))
    text = " ".join(parts).lower()
    return any(_matches_pattern(text, pattern) for pattern in _sensitive_window_patterns(settings))


def _matches_pattern(text: str, pattern: str) -> bool:
    if not pattern:
        return False
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], text, flags=re.IGNORECASE) is not None
        except re.error:
            return False
    return pattern.lower() in text


def _normalize_key(key: str) -> str:
    return str(key or "").strip().lower().replace("-", "_")


def _clip(value: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")
