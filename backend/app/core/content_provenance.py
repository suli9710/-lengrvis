from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.core.content_lineage import (
    CONTENT_LINEAGE_SIDECAR_PREFIX as CONTENT_LINEAGE_SIDECAR_PREFIX,
)
from app.core.content_lineage import (
    MAX_CONTENT_LINEAGE_ENTRIES as MAX_CONTENT_LINEAGE_ENTRIES,
)
from app.core.content_lineage import (
    ContentEnvelope as ContentEnvelope,
)
from app.core.content_lineage import (
    ContentLineageEdge as ContentLineageEdge,
)
from app.core.content_lineage import (
    build_content_lineage as _build_field_lineage,
)
from app.core.content_lineage import (
    content_envelope_hmac_payload as _content_envelope_payload,
)
from app.core.content_lineage import (
    content_lineage_matches_output,
    content_lineage_value_hashes,
)
from app.core.content_lineage import (
    content_lineage_parent_key as _lineage_parent_key,
)
from app.core.content_lineage import (
    legacy_direct_field_hmac_payload as _content_envelope_direct_field_payload,
)
from app.core.content_lineage import (
    materialize_content_lineage as _materialize_field_lineage,
)
from app.core.content_lineage import (
    normalize_lineage_parent_contents as _normalize_parent_contents,
)
from app.core.content_lineage import (
    stable_content_hash as stable_content_hash,
)

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
_PARENT_CONTENT_UNSET = object()
_TOOL_OUTPUT_PROVENANCE_CONTEXT_KEY = "_tool_output_provenance"


class ContentRevalidationRequired(ValueError):
    pass


@dataclass(frozen=True)
class ToolOutputProvenance:
    """Ephemeral authenticated inputs for one derived tool output."""

    parents: tuple[ContentEnvelope, ...]
    parent_contents: dict[str, Any]
    field_lineage: tuple[ContentLineageEdge, ...]
    output_value_hashes: tuple[str, ...]


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
    field_lineage: (
        ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]] | None
    ) = None,
    parent_content: Any = _PARENT_CONTENT_UNSET,
) -> ContentEnvelope:
    """Update the content hash without allowing a rewrite to erase provenance or taint."""

    supplied_parent = coerce_content_envelope(envelope)
    if field_lineage is not None and not content_envelope_integrity_valid(supplied_parent):
        raise ValueError("explicit field lineage requires authenticated parent provenance")
    parent = _verified_or_downgraded_envelope(supplied_parent)
    parent_contents = {}
    if parent_content is not _PARENT_CONTENT_UNSET:
        parent_contents[_lineage_parent_key(parent)] = parent_content
    lineage = _build_field_lineage(
        field_lineage,
        parents=[parent],
        output=rewritten_content,
        default_operation="rewrite",
        single_parent_autofill=True,
        parent_contents=parent_contents,
    )
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
            "field_lineage": lineage,
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
            "field_lineage": _build_field_lineage(
                None,
                parents=[parent],
                output=content,
                default_operation="rewrite",
                single_parent_autofill=True,
                parent_contents={},
            ),
            "integrity_hmac": "",
        }
    )
    return _sign_content_envelope(updated)


def model_rewrite_envelope(
    envelope: ContentEnvelope | Mapping[str, Any],
    rewritten_content: Any,
    *,
    field_lineage: (
        ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]] | None
    ) = None,
    parent_content: Any = _PARENT_CONTENT_UNSET,
) -> ContentEnvelope:
    """Record a model rewrite and its authenticated immediate-parent field mapping."""

    return propagate_content_envelope(
        envelope,
        rewritten_content,
        field_lineage=field_lineage,
        parent_content=parent_content,
    )


def record_tool_output_provenance(
    context: dict[str, Any],
    output: Any,
    *,
    source_content: Any,
    source_kind: str,
    source_id: str = "",
    origin: str = "",
    trust_level: str = "untrusted",
    taint_flags: Iterable[str] | None = None,
    task_scope: str = "",
    field_lineage: (ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]]),
) -> None:
    """Attach verified derivation metadata to an in-process tool context.

    The context value is deliberately ephemeral: it never becomes part of the
    public tool output. The lifecycle consumes it after execution and binds it
    to the final, budgeted ``ContentEnvelope``.
    """

    parent = create_content_envelope(
        source_content,
        source_kind=source_kind,
        source_id=source_id,
        origin=origin,
        trust_level=trust_level,
        taint_flags=taint_flags,
        task_scope=task_scope,
    )
    raw_mappings = _materialize_field_lineage(field_lineage)
    validated = _build_field_lineage(
        raw_mappings,
        parents=[parent],
        output=output,
        default_operation="rewrite",
        single_parent_autofill=True,
        parent_contents={_lineage_parent_key(parent): source_content},
    )
    explicit_edges = tuple(validated[: len(raw_mappings)])
    context[_TOOL_OUTPUT_PROVENANCE_CONTEXT_KEY] = ToolOutputProvenance(
        parents=(parent,),
        parent_contents={parent.content_hash: source_content},
        field_lineage=explicit_edges,
        output_value_hashes=content_lineage_value_hashes(output, explicit_edges),
    )


def clear_tool_output_provenance(context: dict[str, Any]) -> None:
    """Clear an inherited/stale internal provenance directive before a tool runs."""

    context.pop(_TOOL_OUTPUT_PROVENANCE_CONTEXT_KEY, None)


def take_tool_output_provenance(context: dict[str, Any]) -> ToolOutputProvenance | None:
    """Consume the one-shot internal provenance directive emitted by a tool."""

    value = context.pop(_TOOL_OUTPUT_PROVENANCE_CONTEXT_KEY, None)
    if value is None:
        return None
    if not isinstance(value, ToolOutputProvenance):
        raise ContentRevalidationRequired("tool output provenance directive is invalid")
    return value


def merge_content_envelopes(
    envelopes: Iterable[ContentEnvelope | Mapping[str, Any]],
    content: Any,
    *,
    source_kind: str = "derived",
    source_id: str = "",
    origin: str = "",
    task_scope: str = "",
    field_lineage: (
        ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]] | None
    ) = None,
    parent_contents: Mapping[str, Any] | Iterable[Any] | None = None,
) -> ContentEnvelope:
    supplied_parents = [coerce_content_envelope(item) for item in envelopes]
    if field_lineage is not None and any(not content_envelope_integrity_valid(parent) for parent in supplied_parents):
        raise ValueError("explicit field lineage requires authenticated parent provenance")
    parents = [_verified_or_downgraded_envelope(parent) for parent in supplied_parents]
    if not parents:
        if _materialize_field_lineage(field_lineage):
            raise ValueError("field lineage references an unknown parent")
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
    lineage = _build_field_lineage(
        field_lineage,
        parents=parents,
        output=content,
        default_operation="merge",
        single_parent_autofill=len(parents) == 1,
        parent_contents=_normalize_parent_contents(parents, parent_contents),
    )
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
        sanitizers_applied=_unique_strings(sanitizer for item in parents for sanitizer in item.sanitizers_applied),
        field_lineage=lineage,
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
    output_provenance: ToolOutputProvenance | None = None,
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
    parent_contents: dict[str, Any] = {}
    explicit_lineage: tuple[ContentLineageEdge, ...] | None = None
    if output_provenance is not None:
        parents.extend(output_provenance.parents)
        parent_contents.update(output_provenance.parent_contents)
        if content_lineage_matches_output(
            payload,
            output_provenance.field_lineage,
            output_provenance.output_value_hashes,
        ):
            explicit_lineage = output_provenance.field_lineage
    for candidate in [*embedded, *(upstream or [])]:
        if candidate is None:
            continue
        try:
            parents.append(coerce_content_envelope(candidate))
        except (TypeError, ValidationError, ValueError) as exc:
            raise ContentRevalidationRequired("tool output provenance is invalid") from exc
    if len(parents) == 1:
        return base
    return merge_content_envelopes(
        parents,
        payload,
        source_kind=source_kind,
        source_id=tool_call_id,
        origin=tool_name,
        task_scope=task_scope,
        field_lineage=explicit_lineage,
        parent_contents=parent_contents,
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
    if hmac.compare_digest(supplied, expected):
        return True
    if candidate.field_lineage:
        direct_field_expected = hmac.new(
            _content_envelope_secret().encode("utf-8"),
            _content_envelope_direct_field_payload(candidate),
            hashlib.sha256,
        ).hexdigest()
        # A pre-lineage HMAC authenticates only the legacy envelope fields. It
        # must never be used to bless subsequently injected field mappings.
        # Current sidecar and short-lived direct-field formats were both
        # signed with lineage included and are handled above.
        return hmac.compare_digest(supplied, direct_field_expected)
    legacy_expected = hmac.new(
        _content_envelope_secret().encode("utf-8"),
        _content_envelope_payload(candidate, include_field_lineage=False),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, legacy_expected)


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
            raise ContentRevalidationRequired(f"{boundary} is blocked because content provenance is invalid") from exc
        if not content_envelope_requires_revalidation(envelope):
            continue
        if not content_envelope_integrity_valid(envelope):
            raise ContentRevalidationRequired(f"{boundary} is blocked because content provenance is not authenticated")
        if not envelope.user_confirmed:
            raise ContentRevalidationRequired(f"{boundary} requires explicit user revalidation of tainted content")
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
    if envelope.field_lineage:
        envelope = envelope.model_copy(
            update={
                "sanitizers_applied": [
                    item for item in envelope.sanitizers_applied if not item.startswith(CONTENT_LINEAGE_SIDECAR_PREFIX)
                ]
            }
        )
    signature = hmac.new(
        _content_envelope_secret().encode("utf-8"),
        _content_envelope_payload(envelope),
        hashlib.sha256,
    ).hexdigest()
    return envelope.model_copy(update={"integrity_hmac": signature})


def _content_envelope_secret() -> str:
    from app.core import db
    from app.security.local_secret import load_or_create_local_secret

    path = db.db_path().parent / "secrets" / _CONTENT_ENVELOPE_SECRET_FILE
    return load_or_create_local_secret(path, unavailable_message="Content provenance secret is unavailable.")


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
