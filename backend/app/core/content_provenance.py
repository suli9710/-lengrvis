from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.schemas import ContentEnvelope

_TRUST_RANK = {
    "untrusted": 0,
    "unknown": 1,
    "internal": 2,
    "user_confirmed": 3,
    "trusted": 4,
}
_EMBEDDED_ENVELOPE_KEYS = {"content_envelope", "_content_envelope"}
_ENVELOPE_CONTAINER_KEYS = {
    "content_envelope",
    "_content_envelope",
    "content_envelopes",
    "input_envelopes",
    "upstream_content_envelopes",
    "automation_input_envelopes",
}
_CONTENT_ENVELOPE_SECRET_FILE = "content_envelope.secret"  # noqa: S105 - local HMAC key filename.
_RUNTIME_ONLY_TOOL_ARG_KEYS = {
    "approval_id",
    "approved",
    "auto_approved",
    "dry_run",
    "policy_decision",
}


class ContentRevalidationRequired(ValueError):
    pass


def stable_content_hash(content: Any) -> str:
    """Return a deterministic SHA-256 digest for structured or scalar content."""

    canonical = json.dumps(
        _canonical_value(content),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def content_binding_payload(args: Mapping[str, Any]) -> dict[str, Any]:
    """Return the executable argument payload covered by provenance revalidation."""

    return _content_binding_value(dict(args), root=True)


def create_content_envelope(
    content: Any,
    *,
    source_kind: str,
    source_id: str = "",
    origin: str = "",
    trust_level: str = "unknown",
    taint_flags: Iterable[str] | None = None,
    task_scope: str = "",
    user_confirmed: bool = False,
    sanitizers_applied: Iterable[str] | None = None,
) -> ContentEnvelope:
    envelope = ContentEnvelope(
        source_kind=str(source_kind or "unknown"),
        source_id=str(source_id or ""),
        origin=str(origin or ""),
        content_hash=stable_content_hash(content),
        trust_level=_normalize_trust_level(trust_level),
        taint_flags=_unique_strings(taint_flags),
        task_scope=str(task_scope or ""),
        user_confirmed=bool(user_confirmed),
        sanitizers_applied=_unique_strings(sanitizers_applied),
    )
    return _sign_content_envelope(envelope)


def create_content_envelope_from_hash(
    *,
    content_hash: str,
    source_kind: str,
    source_id: str = "",
    origin: str = "",
    trust_level: str = "unknown",
    taint_flags: Iterable[str] | None = None,
    task_scope: str = "",
    user_confirmed: bool = False,
    sanitizers_applied: Iterable[str] | None = None,
) -> ContentEnvelope:
    """Create an authenticated envelope when a streaming boundary already hashed the content."""

    envelope = ContentEnvelope(
        source_kind=str(source_kind or "unknown"),
        source_id=str(source_id or ""),
        origin=str(origin or ""),
        content_hash=str(content_hash or ""),
        trust_level=_normalize_trust_level(trust_level),
        taint_flags=_unique_strings(taint_flags),
        task_scope=str(task_scope or ""),
        user_confirmed=bool(user_confirmed),
        sanitizers_applied=_unique_strings(sanitizers_applied),
    )
    return _sign_content_envelope(envelope)


def propagate_content_envelope(
    envelope: ContentEnvelope | Mapping[str, Any],
    rewritten_content: Any,
    *,
    sanitizer: str | Iterable[str] | None = None,
    user_confirmed: bool | None = None,
    taint_flags: Iterable[str] | None = None,
) -> ContentEnvelope:
    """Update the content hash without allowing a rewrite to erase provenance or taint."""

    parent = _verified_or_downgraded_envelope(coerce_content_envelope(envelope))
    sanitizers = [*parent.sanitizers_applied, *_string_items(sanitizer)]
    confirmed = parent.user_confirmed
    if user_confirmed is False:
        confirmed = False
    updated = parent.model_copy(
        update={
            "content_hash": stable_content_hash(rewritten_content),
            "taint_flags": _unique_strings([*parent.taint_flags, *_string_items(taint_flags)]),
            "sanitizers_applied": _unique_strings(sanitizers),
            # Trust promotion is deliberately unavailable at a generic rewrite
            # boundary. Only revalidate_content_envelope may set this to true.
            "user_confirmed": confirmed,
            "integrity_hmac": "",
        }
    )
    return _sign_content_envelope(updated)


def revalidate_content_envelope(
    envelope: ContentEnvelope | Mapping[str, Any],
    content: Any,
    *,
    task_scope: str,
    sanitizer: str = "user_revalidated",
) -> ContentEnvelope:
    """Promote one envelope after an explicit, trusted user-review boundary."""

    parent = coerce_content_envelope(envelope)
    if not content_envelope_integrity_valid(parent):
        raise ValueError("content revalidation requires authenticated provenance")
    normalized_scope = str(task_scope or "").strip()
    if not normalized_scope:
        raise ValueError("content revalidation requires a task scope")
    if parent.task_scope and parent.task_scope != normalized_scope:
        raise ValueError("content revalidation task scope does not match")
    updated = parent.model_copy(
        update={
            "content_hash": stable_content_hash(content),
            "trust_level": "user_confirmed",
            "user_confirmed": True,
            "task_scope": normalized_scope,
            "sanitizers_applied": _unique_strings([*parent.sanitizers_applied, sanitizer]),
            "integrity_hmac": "",
        }
    )
    return _sign_content_envelope(updated)


def model_rewrite_envelope(
    envelope: ContentEnvelope | Mapping[str, Any],
    rewritten_content: Any,
) -> ContentEnvelope:
    """Explicit model-boundary helper: a paraphrase changes only the hash."""

    return propagate_content_envelope(envelope, rewritten_content)


def merge_content_envelopes(
    envelopes: Iterable[ContentEnvelope | Mapping[str, Any]],
    content: Any,
    *,
    source_kind: str = "derived",
    source_id: str = "",
    origin: str = "",
    task_scope: str = "",
) -> ContentEnvelope:
    parents = [_verified_or_downgraded_envelope(coerce_content_envelope(item)) for item in envelopes]
    if not parents:
        return create_content_envelope(
            content,
            source_kind=source_kind,
            source_id=source_id,
            origin=origin,
            task_scope=task_scope,
        )

    origins = _unique_strings([origin, *(item.origin for item in parents)])
    scopes = _unique_strings([task_scope, *(item.task_scope for item in parents)])
    parent_hash = stable_content_hash([item.content_hash for item in parents]).split(":", 1)[1]
    merged_source_id = source_id or f"merge:{parent_hash}"
    trust_level = min(parents, key=lambda item: _TRUST_RANK.get(item.trust_level, 1)).trust_level
    merged = ContentEnvelope(
        source_kind=str(source_kind or "derived"),
        source_id=merged_source_id,
        origin=" | ".join(origins),
        content_hash=stable_content_hash(content),
        trust_level=trust_level,
        taint_flags=_unique_strings(flag for item in parents for flag in item.taint_flags),
        observed_at=min(item.observed_at for item in parents),
        task_scope=",".join(scopes),
        user_confirmed=all(item.user_confirmed for item in parents),
        sanitizers_applied=_unique_strings(
            sanitizer for item in parents for sanitizer in item.sanitizers_applied
        ),
    )
    return _sign_content_envelope(merged)


def content_envelope_for_tool_output(
    tool_name: str,
    output: Mapping[str, Any] | None,
    *,
    tool_call_id: str,
    task_scope: str,
    trust_tier: str = "unknown",
    external_network: bool = False,
    resource_kinds: Iterable[str] | None = None,
    upstream: Iterable[ContentEnvelope | Mapping[str, Any] | None] | None = None,
) -> ContentEnvelope:
    """Reusable Browser/Document/MCP/generic tool-result provenance boundary."""

    payload = dict(output or {})
    embedded = [payload.pop(key) for key in list(payload) if key in _EMBEDDED_ENVELOPE_KEYS]
    source_kind, trust_level, taint_flags = _tool_source_policy(
        tool_name,
        trust_tier=trust_tier,
        external_network=external_network,
        resource_kinds=resource_kinds,
    )
    base = create_content_envelope(
        payload,
        source_kind=source_kind,
        source_id=tool_call_id,
        origin=tool_name,
        trust_level=trust_level,
        taint_flags=taint_flags,
        task_scope=task_scope,
    )
    parents: list[ContentEnvelope] = [base]
    for candidate in [*embedded, *(upstream or [])]:
        if candidate is None:
            continue
        try:
            parents.append(coerce_content_envelope(candidate))
        except (TypeError, ValidationError, ValueError):
            continue
    if len(parents) == 1:
        return base
    return merge_content_envelopes(
        parents,
        payload,
        source_kind=source_kind,
        source_id=tool_call_id,
        origin=tool_name,
        task_scope=task_scope,
    )


def coerce_content_envelope(value: ContentEnvelope | Mapping[str, Any]) -> ContentEnvelope:
    if isinstance(value, ContentEnvelope):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("content envelope must be a mapping or ContentEnvelope")
    return ContentEnvelope.model_validate(dict(value))


def content_envelope_integrity_valid(envelope: ContentEnvelope | Mapping[str, Any]) -> bool:
    try:
        candidate = coerce_content_envelope(envelope)
    except (TypeError, ValueError):
        return False
    supplied = str(candidate.integrity_hmac or "")
    if not supplied:
        return False
    expected = hmac.new(
        _content_envelope_secret().encode("utf-8"),
        _content_envelope_payload(candidate),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


def content_envelope_requires_revalidation(envelope: ContentEnvelope | Mapping[str, Any]) -> bool:
    candidate = coerce_content_envelope(envelope)
    return (
        not content_envelope_integrity_valid(candidate)
        or bool(candidate.taint_flags)
        or candidate.trust_level in {"untrusted", "unknown"}
    )


def assert_content_revalidated(
    envelopes: Iterable[ContentEnvelope | Mapping[str, Any]],
    *,
    task_scopes: Iterable[str],
    boundary: str,
    content: Any | None = None,
) -> None:
    allowed_scopes = {str(item).strip() for item in task_scopes if str(item).strip()}
    for raw in envelopes:
        try:
            envelope = coerce_content_envelope(raw)
        except (TypeError, ValueError) as exc:
            raise ContentRevalidationRequired(
                f"{boundary} is blocked because content provenance is invalid"
            ) from exc
        if not content_envelope_requires_revalidation(envelope):
            continue
        if not content_envelope_integrity_valid(envelope):
            raise ContentRevalidationRequired(
                f"{boundary} is blocked because content provenance is not authenticated"
            )
        if not envelope.user_confirmed:
            raise ContentRevalidationRequired(
                f"{boundary} requires explicit user revalidation of tainted content"
            )
        if allowed_scopes and envelope.task_scope not in allowed_scopes:
            raise ContentRevalidationRequired(
                f"{boundary} is blocked because content revalidation belongs to another task"
            )
        if content is not None and not hmac.compare_digest(envelope.content_hash, stable_content_hash(content)):
            raise ContentRevalidationRequired(
                f"{boundary} is blocked because revalidated content does not match the executable payload"
            )


def collect_content_envelopes(*values: Any) -> list[ContentEnvelope]:
    collected: list[ContentEnvelope] = []

    def visit(value: Any, *, key_hint: str = "") -> None:
        if isinstance(value, ContentEnvelope):
            collected.append(value)
            return
        if isinstance(value, Mapping):
            keys = {str(key) for key in value}
            looks_like_envelope = "content_hash" in keys and (
                "source_kind" in keys or "taint_flags" in keys or "trust_level" in keys
            )
            if looks_like_envelope or key_hint in _EMBEDDED_ENVELOPE_KEYS:
                try:
                    collected.append(ContentEnvelope.model_validate(dict(value)))
                except ValueError as exc:
                    raise ContentRevalidationRequired("content provenance is invalid") from exc
                return
            for raw_key, item in value.items():
                key = str(raw_key).strip().casefold()
                if key in _ENVELOPE_CONTAINER_KEYS:
                    visit(item, key_hint=key)
                elif isinstance(item, Mapping | list | tuple):
                    visit(item, key_hint=key)
            return
        if isinstance(value, list | tuple):
            for item in value:
                visit(item, key_hint=key_hint)

    for value in values:
        visit(value)
    unique: dict[tuple[str, str, str, str], ContentEnvelope] = {}
    for envelope in collected:
        key = (envelope.source_kind, envelope.source_id, envelope.content_hash, envelope.task_scope)
        unique[key] = envelope
    return list(unique.values())


def _tool_source_policy(
    tool_name: str,
    *,
    trust_tier: str,
    external_network: bool,
    resource_kinds: Iterable[str] | None,
) -> tuple[str, str, list[str]]:
    name = str(tool_name or "")
    resources = {str(item).strip().casefold() for item in (resource_kinds or []) if str(item).strip()}
    if name.startswith("browser."):
        return "browser", "untrusted", ["external_content", "web_content"]
    if name.startswith("document."):
        return "document", "untrusted", ["external_content", "document_content"]
    if name.startswith("mcp."):
        return "mcp", "untrusted", ["external_content", "mcp_content", "third_party_tool"]
    if external_network or str(trust_tier or "").casefold() == "third_party":
        return "tool_result", "untrusted", ["external_content", "third_party_tool"]
    if resources.intersection({"document", "web_page", "url"}):
        return "tool_result", "unknown", ["external_content"]
    trust = "internal" if str(trust_tier or "").casefold() == "builtin" else "unknown"
    return "tool_result", trust, []


def _content_binding_value(value: Any, *, root: bool = False) -> Any:
    if isinstance(value, Mapping):
        bound: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.strip().casefold()
            if normalized in _ENVELOPE_CONTAINER_KEYS:
                continue
            if root and normalized in _RUNTIME_ONLY_TOOL_ARG_KEYS:
                continue
            bound[key] = _content_binding_value(item)
        return bound
    if isinstance(value, list | tuple):
        return [_content_binding_value(item) for item in value]
    return value


def _verified_or_downgraded_envelope(envelope: ContentEnvelope) -> ContentEnvelope:
    if content_envelope_integrity_valid(envelope):
        return envelope
    return envelope.model_copy(
        update={
            "trust_level": "untrusted",
            "user_confirmed": False,
            "taint_flags": _unique_strings([*envelope.taint_flags, "unverified_provenance"]),
            "integrity_hmac": "",
        }
    )


def _sign_content_envelope(envelope: ContentEnvelope) -> ContentEnvelope:
    signature = hmac.new(
        _content_envelope_secret().encode("utf-8"),
        _content_envelope_payload(envelope),
        hashlib.sha256,
    ).hexdigest()
    return envelope.model_copy(update={"integrity_hmac": signature})


def _content_envelope_payload(envelope: ContentEnvelope) -> bytes:
    return json.dumps(
        envelope.model_dump(mode="json", exclude={"integrity_hmac"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_envelope_secret() -> str:
    from app.core import db
    from app.security.local_secret import load_or_create_local_secret

    path = db.db_path().parent / "secrets" / _CONTENT_ENVELOPE_SECRET_FILE
    return load_or_create_local_secret(path, unavailable_message="Content provenance secret is unavailable.")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="json"))
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, list | tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set | frozenset):
        normalized = [_canonical_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    return str(value)


def _normalize_trust_level(value: Any) -> str:
    candidate = str(value or "unknown").strip().casefold()
    return candidate if candidate in _TRUST_RANK else "unknown"


def _unique_strings(values: Iterable[Any] | None) -> list[str]:
    if values is None:
        return []
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _string_items(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value if str(item).strip()]
