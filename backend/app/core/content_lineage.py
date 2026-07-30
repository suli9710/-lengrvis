from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

MAX_CONTENT_LINEAGE_ENTRIES = 256
MAX_JSON_POINTER_CHARS = 1024
CONTENT_LINEAGE_SIDECAR_PREFIX = "lengrvis:field-lineage:v1:"

_PARENT_CONTENT_UNSET = object()


class ContentLineageParent(Protocol):
    """Minimum authenticated-parent shape consumed by field derivation rules."""

    source_kind: str
    source_id: str
    content_hash: str


class ContentLineageEdge(BaseModel):
    """One authenticated immediate-parent field derivation."""

    model_config = ConfigDict(extra="forbid")

    output_pointer: str = Field(default="", max_length=MAX_JSON_POINTER_CHARS)
    source_pointer: str = Field(default="", max_length=MAX_JSON_POINTER_CHARS)
    # These identity fields deliberately match ContentEnvelope's unbounded
    # legacy contract. Narrower limits would make an otherwise valid stored
    # envelope impossible to derive from during recovery.
    source_kind: str
    source_id: str = ""
    source_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    operation: Literal["copy", "rename", "extract", "summarize", "rewrite", "merge"]

    @field_validator("output_pointer", "source_pointer")
    @classmethod
    def validate_json_pointer(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("JSON Pointer must be empty or start with '/'")
        index = 0
        while index < len(value):
            if value[index] != "~":
                index += 1
                continue
            if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
                raise ValueError("JSON Pointer contains an invalid escape")
            index += 2
        return value


class ContentEnvelope(BaseModel):
    """Provenance and taint metadata that stays attached to derived content."""

    model_config = ConfigDict(extra="forbid")

    source_kind: str
    source_id: str = ""
    origin: str = ""
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trust_level: Literal["untrusted", "unknown", "internal", "user_confirmed", "trusted"] = "unknown"
    taint_flags: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    task_scope: str = ""
    user_confirmed: bool = False
    sanitizers_applied: list[str] = Field(default_factory=list)
    field_lineage: list[ContentLineageEdge] = Field(
        default_factory=list,
        max_length=MAX_CONTENT_LINEAGE_ENTRIES,
    )
    integrity_hmac: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def decode_legacy_compatible_field_lineage(cls, data: Any) -> Any:
        """Decode lineage carried in an old-ContentEnvelope-compatible field.

        Older binaries reject unknown envelope keys with ``extra='forbid'``.
        The versioned sidecar therefore travels inside the pre-existing
        ``sanitizers_applied`` string list and is covered by the same HMAC.
        """

        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        sanitizers = normalized.get("sanitizers_applied")
        if not isinstance(sanitizers, list):
            return normalized
        markers = [
            item for item in sanitizers if isinstance(item, str) and item.startswith(CONTENT_LINEAGE_SIDECAR_PREFIX)
        ]
        if not markers:
            return normalized
        if len(markers) > 1:
            for marker in markers:
                decode_content_lineage_sidecar(marker)
            # Older binaries treat these as opaque sanitizer strings. A merge
            # can therefore retain one stale marker per parent. Preserve that
            # HMAC-verifiable wire payload, but do not attribute any one
            # parent's mapping to the merged output.
            if normalized.get("field_lineage"):
                raise ValueError("multiple field-lineage sidecars conflict with the explicit field")
            return normalized

        payload = decode_content_lineage_sidecar(markers[0])
        current_hash = str(normalized.get("content_hash") or "")
        if payload["content_hash"] != current_hash:
            # A rolled-back older binary may legitimately rewrite the content
            # while preserving an opaque sanitizer value it does not
            # understand. Keep that value HMAC-verifiable, but do not claim the
            # stale field mapping applies to the rewritten output.
            if normalized.get("field_lineage"):
                raise ValueError("field-lineage sidecar does not match the envelope content hash")
            return normalized

        decoded_edges = [ContentLineageEdge.model_validate(edge).model_dump(mode="json") for edge in payload["edges"]]
        explicit_edges = normalized.get("field_lineage")
        if explicit_edges is not None:
            validated_explicit = [
                ContentLineageEdge.model_validate(edge).model_dump(mode="json") for edge in explicit_edges
            ]
            if validated_explicit != decoded_edges:
                raise ValueError("field-lineage sidecar conflicts with the explicit field")
        normalized["field_lineage"] = decoded_edges
        normalized["sanitizers_applied"] = [item for item in sanitizers if item not in markers]
        return normalized

    @model_serializer(mode="wrap")
    def serialize_legacy_compatible_field_lineage(self, handler, info):  # noqa: ANN001
        """Emit an envelope that both current and pre-lineage binaries accept."""

        serialized = handler(self)
        lineage = serialized.pop("field_lineage", None)
        exclude = info.exclude
        lineage_excluded = (isinstance(exclude, set | frozenset) and "field_lineage" in exclude) or (
            isinstance(exclude, dict) and "field_lineage" in exclude
        )
        sanitizers = list(serialized.get("sanitizers_applied") or [])
        if lineage_excluded:
            serialized["sanitizers_applied"] = [
                item
                for item in sanitizers
                if not (isinstance(item, str) and item.startswith(CONTENT_LINEAGE_SIDECAR_PREFIX))
            ]
            return serialized
        if lineage:
            sanitizers = [
                item
                for item in sanitizers
                if not (isinstance(item, str) and item.startswith(CONTENT_LINEAGE_SIDECAR_PREFIX))
            ]
            sanitizers.append(
                encode_content_lineage_sidecar(
                    content_hash=self.content_hash,
                    edges=lineage,
                )
            )
            serialized["sanitizers_applied"] = sanitizers
        return serialized


def encode_content_lineage_sidecar(*, content_hash: str, edges: list[dict[str, Any]]) -> str:
    raw = json.dumps(
        {"content_hash": content_hash, "edges": edges},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{CONTENT_LINEAGE_SIDECAR_PREFIX}{encoded}"


def decode_content_lineage_sidecar(marker: str) -> dict[str, Any]:
    encoded = marker.removeprefix(CONTENT_LINEAGE_SIDECAR_PREFIX)
    if not encoded or "=" in encoded:
        raise ValueError("content envelope field-lineage sidecar is malformed")
    try:
        raw = base64.b64decode(
            (encoded + ("=" * (-len(encoded) % 4))).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("content envelope field-lineage sidecar is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"content_hash", "edges"}:
        raise ValueError("content envelope field-lineage sidecar is malformed")
    if not isinstance(payload["content_hash"], str) or not isinstance(payload["edges"], list):
        raise ValueError("content envelope field-lineage sidecar is malformed")
    return payload


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


def build_content_lineage(
    mappings: (ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]] | None),
    *,
    parents: Sequence[ContentLineageParent],
    output: Any,
    default_operation: str,
    single_parent_autofill: bool,
    parent_contents: Mapping[tuple[str, str, str], Any],
) -> list[ContentLineageEdge]:
    """Authenticate immediate-parent field mappings and add conservative root edges."""

    raw_mappings = materialize_content_lineage(mappings)
    canonical_output = _canonical_value(output)
    edges: list[ContentLineageEdge] = []
    root_covered_parents: set[tuple[str, str, str]] = set()

    for raw_mapping in raw_mappings:
        if isinstance(raw_mapping, ContentLineageEdge):
            payload = raw_mapping.model_dump(mode="json")
        elif isinstance(raw_mapping, Mapping):
            payload = dict(raw_mapping)
        else:
            raise TypeError("field lineage entry must be a mapping or ContentLineageEdge")

        parent = _resolve_lineage_parent(
            payload,
            parents=parents,
            single_parent_autofill=single_parent_autofill,
        )
        payload.setdefault("source_kind", parent.source_kind)
        payload.setdefault("source_id", parent.source_id)
        payload.setdefault("source_content_hash", parent.content_hash)
        edge = ContentLineageEdge.model_validate(payload)
        parent_key = content_lineage_parent_key(parent)
        if _lineage_edge_parent_key(edge) != parent_key:
            raise ValueError("field lineage source does not match an immediate parent")
        output_exists, output_value = _resolve_json_pointer(canonical_output, edge.output_pointer)
        if not output_exists:
            raise ValueError(f"field lineage output pointer does not exist: {edge.output_pointer!r}")
        source_content = parent_contents.get(parent_key, _PARENT_CONTENT_UNSET)
        if source_content is _PARENT_CONTENT_UNSET:
            raise ValueError("explicit field lineage requires authenticated parent content")
        if not hmac.compare_digest(parent.content_hash, stable_content_hash(source_content)):
            raise ValueError("field lineage parent content does not match its authenticated hash")
        source_exists, source_value = _resolve_json_pointer(
            _canonical_value(source_content),
            edge.source_pointer,
        )
        if not source_exists:
            raise ValueError(f"field lineage source pointer does not exist: {edge.source_pointer!r}")
        if edge.operation in {"copy", "rename", "extract"} and not hmac.compare_digest(
            stable_content_hash(source_value),
            stable_content_hash(output_value),
        ):
            raise ValueError(f"field lineage {edge.operation} values do not match")
        edges.append(edge)
        if edge.output_pointer == "":
            root_covered_parents.add(parent_key)

    for parent in parents:
        parent_key = content_lineage_parent_key(parent)
        if parent_key in root_covered_parents:
            continue
        edges.append(
            ContentLineageEdge(
                output_pointer="",
                source_pointer="",
                source_kind=parent.source_kind,
                source_id=parent.source_id,
                source_content_hash=parent.content_hash,
                operation=default_operation,
            )
        )

    if len(edges) > MAX_CONTENT_LINEAGE_ENTRIES:
        raise ValueError(f"field lineage exceeds the {MAX_CONTENT_LINEAGE_ENTRIES}-entry limit")
    return edges


def normalize_lineage_parent_contents(
    parents: Sequence[ContentLineageParent],
    parent_contents: Mapping[str, Any] | Iterable[Any] | None,
) -> dict[tuple[str, str, str], Any]:
    if parent_contents is None:
        return {}
    if isinstance(parent_contents, Mapping):
        return {
            content_lineage_parent_key(parent): parent_contents[parent.content_hash]
            for parent in parents
            if parent.content_hash in parent_contents
        }
    if isinstance(parent_contents, str | bytes | bytearray):
        raise TypeError("parent_contents must be keyed by content hash or aligned with parents")
    values = list(parent_contents)
    if len(values) != len(parents):
        raise ValueError("parent_contents must contain one value for every parent")
    return {content_lineage_parent_key(parent): value for parent, value in zip(parents, values, strict=True)}


def content_lineage_parent_key(parent: ContentLineageParent) -> tuple[str, str, str]:
    return (parent.source_kind, parent.source_id, parent.content_hash)


def content_lineage_value_hashes(
    output: Any,
    edges: Iterable[ContentLineageEdge],
) -> tuple[str, ...]:
    canonical_output = _canonical_value(output)
    output_value_hashes: list[str] = []
    for edge in edges:
        exists, value = _resolve_json_pointer(canonical_output, edge.output_pointer)
        if not exists:
            raise ValueError(f"field lineage output pointer does not exist: {edge.output_pointer!r}")
        output_value_hashes.append(stable_content_hash(value))
    return tuple(output_value_hashes)


def content_lineage_matches_output(
    output: Any,
    edges: Iterable[ContentLineageEdge],
    expected_hashes: Iterable[str],
) -> bool:
    canonical_output = _canonical_value(output)
    for edge, expected_hash in zip(edges, expected_hashes, strict=True):
        exists, value = _resolve_json_pointer(canonical_output, edge.output_pointer)
        if not exists or not hmac.compare_digest(stable_content_hash(value), expected_hash):
            return False
    return True


def content_envelope_hmac_payload(
    envelope: ContentEnvelope,
    *,
    include_field_lineage: bool = True,
) -> bytes:
    excluded_fields = {"integrity_hmac"}
    if not include_field_lineage:
        excluded_fields.add("field_lineage")
    return json.dumps(
        envelope.model_dump(mode="json", exclude=excluded_fields),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def legacy_direct_field_hmac_payload(envelope: ContentEnvelope) -> bytes:
    """Compatibility verifier for the short-lived direct-field wire format."""

    payload = envelope.model_dump(
        mode="json",
        exclude={"field_lineage", "integrity_hmac"},
    )
    payload["field_lineage"] = [edge.model_dump(mode="json") for edge in envelope.field_lineage]
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def materialize_content_lineage(
    mappings: (ContentLineageEdge | Mapping[str, Any] | Iterable[ContentLineageEdge | Mapping[str, Any]] | None),
) -> list[ContentLineageEdge | Mapping[str, Any]]:
    if mappings is None:
        return []
    if isinstance(mappings, ContentLineageEdge | Mapping):
        materialized = [mappings]
    else:
        if isinstance(mappings, str | bytes | bytearray):
            raise TypeError("field lineage must be an entry or iterable of entries")
        materialized = list(mappings)
    if len(materialized) > MAX_CONTENT_LINEAGE_ENTRIES:
        raise ValueError(f"field lineage exceeds the {MAX_CONTENT_LINEAGE_ENTRIES}-entry limit")
    return materialized


def _resolve_lineage_parent(
    payload: Mapping[str, Any],
    *,
    parents: Sequence[ContentLineageParent],
    single_parent_autofill: bool,
) -> ContentLineageParent:
    if single_parent_autofill and len(parents) == 1:
        return parents[0]

    source_hash = payload.get("source_content_hash")
    if not isinstance(source_hash, str) or not source_hash:
        raise ValueError("multi-parent field lineage requires source_content_hash")
    candidates = [parent for parent in parents if parent.content_hash == source_hash]
    if "source_kind" in payload:
        candidates = [parent for parent in candidates if parent.source_kind == payload["source_kind"]]
    if "source_id" in payload:
        candidates = [parent for parent in candidates if parent.source_id == payload["source_id"]]
    unique = {content_lineage_parent_key(parent): parent for parent in candidates}
    if len(unique) != 1:
        raise ValueError("field lineage references an unknown or ambiguous immediate parent")
    return next(iter(unique.values()))


def _lineage_edge_parent_key(edge: ContentLineageEdge) -> tuple[str, str, str]:
    return (edge.source_kind, edge.source_id, edge.source_content_hash)


def _resolve_json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, document
    current = document
    for encoded_segment in pointer.split("/")[1:]:
        segment = encoded_segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if segment not in current:
                return False, None
            current = current[segment]
            continue
        if isinstance(current, list):
            if segment != "0" and (
                not segment or segment[0] == "0" or any(character < "0" or character > "9" for character in segment)
            ):
                return False, None
            index = int(segment)
            if index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


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
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
        )
    return str(value)
