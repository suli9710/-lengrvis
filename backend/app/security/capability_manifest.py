from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from app.config import AppSettings, get_env
from app.config_paths import DEFAULT_DATA_DIR
from app.core.errors import SecurityError

if TYPE_CHECKING:
    from app.tools.schemas import ToolDefinition

CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
CAPABILITY_MANIFEST_FORMAT = "lengrvis.capability-manifest/v1"
DEFAULT_REVOCATION_FILE_NAME = "capability-revocations.json"
MAX_REVOCATION_FILE_BYTES = 1024 * 1024

_KIND_ALIASES = {
    "mcp": "mcp_server",
    "mcp-server": "mcp_server",
    "mcp_server": "mcp_server",
    "permission": "permission_policy",
    "permission-policy": "permission_policy",
    "permission_policy": "permission_policy",
    "policy": "permission_policy",
    "prompt": "prompt",
    "skill": "skill",
    "tool": "tool",
}
_ENFORCED_KINDS = frozenset({"mcp_server", "permission_policy", "prompt", "skill", "tool"})
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "auth",
        "authorization",
        "bearer",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "headers",
        "jwt_secret",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "signature",
        "token",
    }
)
_OBSERVED_LOCK = threading.RLock()
_OBSERVED: dict[tuple[str, str], CapabilityEntry] = {}


class CapabilityManifestError(SecurityError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message=message, code=code)


class CapabilityRevokedError(CapabilityManifestError):
    def __init__(self, kind: str, capability_id: str) -> None:
        self.capability_id_hash = _identifier_digest(capability_id)
        super().__init__(
            f"A {kind} capability has been revoked.",
            code="capability_revoked",
        )


class CapabilityRevocationConfigError(CapabilityManifestError):
    def __init__(self) -> None:
        super().__init__(
            "Capability revocation configuration is invalid; protected capabilities are disabled.",
            code="capability_revocation_config_invalid",
        )


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    kind: str
    capability_id: str
    content_hash: str
    version: str = ""
    origin: str = ""

    def public_dict(self, *, revoked: bool) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": _public_label(self.capability_id, limit=512),
            "content_hash": self.content_hash,
            "version": _public_label(self.version, limit=256),
            "origin": _public_label(self.origin, limit=256),
            "state": "revoked" if revoked else "active",
        }


@dataclass(frozen=True, slots=True)
class RevocationTarget:
    kind: str = ""
    capability_id: str = ""
    content_hash: str = ""
    source: str = ""


@dataclass(frozen=True, slots=True)
class RevocationConfig:
    targets: tuple[RevocationTarget, ...]
    errors: tuple[dict[str, str], ...]
    sources: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def canonical_json_bytes(value: Any) -> bytes:
    sanitized = sanitize_capability_payload(value)
    return json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def prompt_capability_payload(content: str) -> dict[str, str]:
    normalized = str(content).replace("\r\n", "\n").replace("\r", "\n")
    return {"content": normalized}


def skill_manifest_capability_payload(raw_manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in raw_manifest.items()
        if str(key).casefold() not in {"signature", "signatures"}
    }


def permission_policy_capability_payload(policy: Mapping[str, Any] | Any) -> dict[str, Any]:
    data = _mapping_value(policy)
    return _drop_metadata_timestamps(data)


def mcp_server_capability_payload(config: Mapping[str, Any] | Any) -> dict[str, Any]:
    data = _mapping_value(config)
    endpoint = data.get("url") or data.get("endpoint") or ""
    endpoint_details = _sanitize_url_details(str(endpoint))
    command_path = str(data.get("command") or "").replace("\\", "/")
    command = command_path.rsplit("/", 1)[-1]
    return {
        "name": str(data.get("name") or data.get("id") or "mcp"),
        "enabled": bool(data.get("enabled", True)),
        "transport": str(data.get("transport") or "http").casefold(),
        "endpoint": endpoint_details["endpoint"],
        "endpoint_options": endpoint_details["options"],
        "command": command,
        "command_path_hash": canonical_content_hash({"path": command_path}) if command_path else "",
        "args": _sanitize_command_args(data.get("args")),
        "owner": str(data.get("owner") or ""),
        "policy_id": str(data.get("policy_id") or data.get("policyId") or ""),
        "allowed_tools": sorted(_string_list(data.get("allowed_tools") or data.get("allowedTools"))),
    }


def tool_capability_payload(tool: ToolDefinition | Any) -> dict[str, Any]:
    risk = getattr(tool, "risk_level", "")
    return {
        "name": str(getattr(tool, "name", "")),
        "description": str(getattr(tool, "description", "")),
        "input_schema": getattr(tool, "input_schema", {}),
        "output_schema": getattr(tool, "output_schema", {}),
        "risk_level": getattr(risk, "value", risk),
        "agent_owner": str(getattr(tool, "agent_owner", "")),
        "supports_dry_run": bool(getattr(tool, "supports_dry_run", False)),
        "requires_authorized_path": bool(getattr(tool, "requires_authorized_path", False)),
        "permission_mode": str(getattr(tool, "permission_mode", "")),
        "search_hint": str(getattr(tool, "search_hint", "")),
        "read_only": getattr(tool, "read_only", None),
        "concurrency_safe": getattr(tool, "concurrency_safe", None),
        "concurrency_key": str(getattr(tool, "concurrency_key", "")),
        "destructive": bool(getattr(tool, "destructive", False)),
        "max_result_size": int(getattr(tool, "max_result_size", 0) or 0),
        "defer_loading": bool(getattr(tool, "defer_loading", False)),
        "progress_schema": getattr(tool, "progress_schema", {}),
        "ui_summary": str(getattr(tool, "ui_summary", "")),
        "hooks": getattr(tool, "hooks", {}),
        "origin": str(getattr(tool, "origin", "")),
        "app_target": getattr(tool, "app_target", None),
        "workflow": getattr(tool, "workflow", None),
        "capabilities": list(getattr(tool, "capabilities", []) or []),
        "effects": list(getattr(tool, "effects", []) or []),
        "resource_kinds": list(getattr(tool, "resource_kinds", []) or []),
        "fast_path_eligible": bool(getattr(tool, "fast_path_eligible", False)),
        "trust_tier": str(getattr(tool, "trust_tier", "")),
        "sensitive_arg_keys": list(getattr(tool, "sensitive_arg_keys", []) or []),
        "external_network": bool(getattr(tool, "external_network", False)),
        "feature_flag": str(getattr(tool, "feature_flag", "")),
        "tool_version": str(getattr(tool, "tool_version", "1") or "1"),
    }


def observe_capability(
    kind: str,
    capability_id: str,
    payload: Any,
    *,
    version: str = "",
    origin: str = "",
) -> CapabilityEntry:
    entry = CapabilityEntry(
        kind=normalize_capability_kind(kind),
        capability_id=_safe_capability_id(capability_id),
        content_hash=canonical_content_hash(payload),
        version=_safe_text(version, limit=256),
        origin=_safe_text(origin, limit=256),
    )
    with _OBSERVED_LOCK:
        _OBSERVED[(entry.kind, entry.capability_id)] = entry
    return entry


def observe_tool(tool: ToolDefinition | Any) -> CapabilityEntry:
    return observe_capability(
        "tool",
        str(getattr(tool, "name", "")),
        tool_capability_payload(tool),
        version=str(getattr(tool, "tool_version", "1") or "1"),
        origin=str(getattr(tool, "origin", "builtin") or "builtin"),
    )


def assert_capability_allowed(
    kind: str,
    capability_id: str,
    *,
    payload: Any | None = None,
    content_hash: str = "",
) -> str:
    normalized_kind = normalize_capability_kind(kind)
    safe_id = _safe_capability_id(capability_id)
    digest = _normalize_hash(content_hash) or (canonical_content_hash(payload) if payload is not None else "")
    revocations = load_revocation_config()
    if not revocations.valid and normalized_kind in _ENFORCED_KINDS:
        _audit_block(normalized_kind, safe_id, digest, reason="invalid_config", sources=revocations.sources)
        raise CapabilityRevocationConfigError
    if _matches_revocation(revocations.targets, normalized_kind, safe_id, digest):
        _audit_block(normalized_kind, safe_id, digest, reason="revoked", sources=revocations.sources)
        raise CapabilityRevokedError(normalized_kind, safe_id)
    return digest


def assert_tool_allowed(tool: ToolDefinition | Any) -> CapabilityEntry:
    entry = observe_tool(tool)
    assert_capability_allowed(
        entry.kind,
        entry.capability_id,
        content_hash=entry.content_hash,
    )
    return entry


def is_capability_allowed(
    kind: str,
    capability_id: str,
    *,
    payload: Any | None = None,
    content_hash: str = "",
) -> bool:
    normalized_kind = normalize_capability_kind(kind)
    safe_id = _safe_capability_id(capability_id)
    digest = _normalize_hash(content_hash) or (canonical_content_hash(payload) if payload is not None else "")
    revocations = load_revocation_config()
    if not revocations.valid and normalized_kind in _ENFORCED_KINDS:
        return False
    return not _matches_revocation(revocations.targets, normalized_kind, safe_id, digest)


def is_tool_allowed(tool: ToolDefinition | Any) -> bool:
    entry = observe_tool(tool)
    return is_capability_allowed(entry.kind, entry.capability_id, content_hash=entry.content_hash)


def build_capability_manifest(
    *,
    settings: AppSettings | None = None,
    tools: list[ToolDefinition] | None = None,
) -> dict[str, Any]:
    effective_settings = settings or _effective_settings()
    entries = _observed_entries()
    if tools is not None:
        for tool in tools:
            entry = observe_tool(tool)
            entries[(entry.kind, entry.capability_id)] = entry
    _collect_prompt_entries(entries)
    _collect_permission_policy_entry(entries)
    _collect_skill_entries(entries, effective_settings)
    _collect_mcp_entries(entries, effective_settings)

    revocations = load_revocation_config(settings=effective_settings)
    public_entries: list[dict[str, Any]] = []
    for entry in sorted(entries.values(), key=lambda item: (item.kind, item.capability_id)):
        revoked = (not revocations.valid and entry.kind in _ENFORCED_KINDS) or _matches_revocation(
            revocations.targets,
            entry.kind,
            entry.capability_id,
            entry.content_hash,
        )
        public_entries.append(entry.public_dict(revoked=revoked))

    revocation_hash = canonical_content_hash(
        [
            {
                "kind": target.kind,
                "id_hash": _identifier_digest(target.capability_id) if target.capability_id else "",
                "content_hash": target.content_hash,
            }
            for target in sorted(
                revocations.targets,
                key=lambda item: (item.kind, item.capability_id, item.content_hash),
            )
        ]
    )
    manifest_body = {
        "format": CAPABILITY_MANIFEST_FORMAT,
        "schema_version": CAPABILITY_MANIFEST_SCHEMA_VERSION,
        "revocation_state": "valid" if revocations.valid else "invalid",
        "revocation_hash": revocation_hash,
        "entries": public_entries,
    }
    manifest_hash = canonical_content_hash(manifest_body)
    counts: dict[str, int] = {}
    for entry in public_entries:
        counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
    return {
        **manifest_body,
        "manifest_id": f"cap-v1-{manifest_hash.removeprefix('sha256:')[:16]}",
        "manifest_hash": manifest_hash,
        "generated_at": datetime.now(UTC).isoformat(),
        "state": (
            "invalid"
            if not revocations.valid
            else "revoked"
            if any(entry["state"] == "revoked" for entry in public_entries)
            else "active"
        ),
        "counts": counts,
        "revocations": _public_revocations(revocations),
    }


def load_revocation_config(*, settings: AppSettings | None = None) -> RevocationConfig:
    targets: list[RevocationTarget] = []
    errors: list[dict[str, str]] = []
    sources: list[str] = []

    raw_env = str(get_env("LENGRVIS_CAPABILITY_REVOCATIONS") or "").strip()
    if raw_env:
        sources.append("environment")
        _parse_revocation_source(raw_env, source="environment", targets=targets, errors=errors)

    file_path, explicit = _revocation_file_path(settings)
    if file_path.exists() or explicit:
        sources.append("file")
        _load_revocation_file(file_path, targets=targets, errors=errors)

    unique: dict[tuple[str, str, str, str], RevocationTarget] = {}
    for target in targets:
        unique[(target.kind, target.capability_id, target.content_hash, target.source)] = target
    return RevocationConfig(
        targets=tuple(unique.values()),
        errors=tuple(errors),
        sources=tuple(dict.fromkeys(sources)),
    )


def sanitize_capability_payload(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, Enum):
        return sanitize_capability_payload(value.value, key_hint=key_hint)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize_capability_payload(asdict(value), key_hint=key_hint)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return sanitize_capability_payload(model_dump(mode="python"), key_hint=key_hint)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        schema_field_names = key_hint.casefold() in {"$defs", "definitions", "patternproperties", "properties"}
        for raw_key, item in value.items():
            key = str(raw_key)
            if not schema_field_names and _is_sensitive_key(key):
                continue
            result[key] = sanitize_capability_payload(item, key_hint=key)
        return result
    if isinstance(value, list | tuple):
        return [sanitize_capability_payload(item, key_hint=key_hint) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(sanitize_capability_payload(item, key_hint=key_hint) for item in value)
    if isinstance(value, str):
        if "://" in value or key_hint.casefold() in {"endpoint", "url", "uri"}:
            return _sanitize_url(value)
        return value
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


def normalize_capability_kind(kind: str) -> str:
    normalized = str(kind or "").strip().casefold().replace(" ", "_")
    return _KIND_ALIASES.get(normalized, normalized)


def _effective_settings() -> AppSettings:
    from app.llm.registry import get_effective_settings

    return get_effective_settings()


def _observed_entries() -> dict[tuple[str, str], CapabilityEntry]:
    with _OBSERVED_LOCK:
        return dict(_OBSERVED)


def _collect_prompt_entries(entries: dict[tuple[str, str], CapabilityEntry]) -> None:
    try:
        from app.llm import prompts

        prompt_paths = sorted(prompts._prompt_dir().glob("*.md"))
    except (ImportError, OSError):
        return
    for path in prompt_paths:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        entry = CapabilityEntry(
            kind="prompt",
            capability_id=path.name,
            content_hash=canonical_content_hash(prompt_capability_payload(content)),
            version="1",
            origin="builtin_prompt",
        )
        entries[(entry.kind, entry.capability_id)] = entry


def _collect_permission_policy_entry(entries: dict[tuple[str, str], CapabilityEntry]) -> None:
    try:
        from app.policy.permissions import PermissionStore

        store = PermissionStore()
        policy = store.get_policy()
        persisted_version = store.updated_at()
        payload = permission_policy_capability_payload(policy)
    except Exception:  # noqa: BLE001 - broad-exception-boundary; status remains available when local policy storage is unavailable.
        return
    entry = CapabilityEntry(
        kind="permission_policy",
        capability_id=str(policy.id),
        content_hash=canonical_content_hash(payload),
        version=persisted_version,
        origin="local_policy_store",
    )
    entries[(entry.kind, entry.capability_id)] = entry


def _collect_skill_entries(
    entries: dict[tuple[str, str], CapabilityEntry],
    settings: AppSettings,
) -> None:
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is included in supported builds.
        return
    configured = [Path(item).expanduser() for item in settings.skill_directories if str(item).strip()]
    directories = configured or [Path(settings.data_dir) / "skills"]
    for directory in directories:
        if not directory.is_dir():
            continue
        manifests = sorted({*directory.rglob("skill.yaml"), *directory.rglob("skill.yml")})
        for manifest in manifests:
            try:
                raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(raw, Mapping):
                continue
            capability_id = _safe_capability_id(str(raw.get("name") or manifest.parent.name))
            entry = CapabilityEntry(
                kind="skill",
                capability_id=capability_id,
                content_hash=canonical_content_hash(skill_manifest_capability_payload(raw)),
                version=_safe_text(raw.get("version"), limit=256),
                origin="installed_skill",
            )
            entries[(entry.kind, entry.capability_id)] = entry


def _collect_mcp_entries(
    entries: dict[tuple[str, str], CapabilityEntry],
    settings: AppSettings,
) -> None:
    for raw in settings.mcp_servers:
        if not isinstance(raw, Mapping):
            continue
        payload = mcp_server_capability_payload(raw)
        capability_id = _safe_capability_id(str(payload["name"]))
        entry = CapabilityEntry(
            kind="mcp_server",
            capability_id=capability_id,
            content_hash=canonical_content_hash(payload),
            version="1",
            origin="runtime_config",
        )
        entries[(entry.kind, entry.capability_id)] = entry


def _revocation_file_path(settings: AppSettings | None) -> tuple[Path, bool]:
    configured = str(get_env("LENGRVIS_CAPABILITY_REVOCATION_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser(), True
    if settings is not None:
        data_dir = Path(settings.data_dir)
    else:
        data_dir = Path(str(get_env("LENGRVIS_DATA_DIR") or DEFAULT_DATA_DIR))
    return data_dir / DEFAULT_REVOCATION_FILE_NAME, False


def _load_revocation_file(
    path: Path,
    *,
    targets: list[RevocationTarget],
    errors: list[dict[str, str]],
) -> None:
    try:
        stat = path.stat()
    except OSError:
        errors.append({"source": "file", "code": "unreadable"})
        return
    if not path.is_file():
        errors.append({"source": "file", "code": "not_a_file"})
        return
    if stat.st_size > MAX_REVOCATION_FILE_BYTES:
        errors.append({"source": "file", "code": "too_large"})
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        errors.append({"source": "file", "code": "unreadable"})
        return
    _parse_revocation_source(raw, source="file", targets=targets, errors=errors)


def _parse_revocation_source(
    raw: str,
    *,
    source: str,
    targets: list[RevocationTarget],
    errors: list[dict[str, str]],
) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if source == "environment" and not raw.lstrip().startswith(("{", "[")):
            parsed = [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
        else:
            errors.append({"source": source, "code": "invalid_json"})
            return
    if isinstance(parsed, Mapping):
        parsed = parsed.get("revocations", parsed.get("items", [parsed]))
    if not isinstance(parsed, list):
        errors.append({"source": source, "code": "invalid_shape"})
        return
    for item in parsed:
        target = _parse_revocation_item(item, source=source)
        if target is None:
            errors.append({"source": source, "code": "invalid_entry"})
            continue
        targets.append(target)


def _parse_revocation_item(item: Any, *, source: str) -> RevocationTarget | None:
    if isinstance(item, str):
        text = item.strip()
        digest = _normalize_hash(text)
        if digest:
            return RevocationTarget(content_hash=digest, source=source)
        if ":" not in text:
            return None
        kind, capability_id = text.split(":", 1)
        normalized_kind = normalize_capability_kind(kind)
        safe_id = _safe_capability_id(capability_id)
        if not normalized_kind or not safe_id:
            return None
        return RevocationTarget(kind=normalized_kind, capability_id=safe_id, source=source)
    if not isinstance(item, Mapping):
        return None
    digest = _normalize_hash(str(item.get("content_hash") or item.get("contentHash") or item.get("hash") or ""))
    kind = normalize_capability_kind(str(item.get("kind") or item.get("type") or ""))
    capability_id = _safe_capability_id(str(item.get("id") or item.get("capability_id") or ""))
    if not digest and not (kind and capability_id):
        return None
    return RevocationTarget(kind=kind, capability_id=capability_id, content_hash=digest, source=source)


def _matches_revocation(
    targets: tuple[RevocationTarget, ...],
    kind: str,
    capability_id: str,
    content_hash: str,
) -> bool:
    normalized_hash = _normalize_hash(content_hash)
    for target in targets:
        if target.content_hash and normalized_hash and target.content_hash == normalized_hash:
            return True
        if target.kind == kind and target.capability_id in {capability_id, "*"}:
            return True
    return False


def _public_revocations(config: RevocationConfig) -> dict[str, Any]:
    targets = [
        {
            "kind": target.kind,
            "id_hash": _identifier_digest(target.capability_id) if target.capability_id else "",
            "content_hash": target.content_hash,
            "source": target.source,
        }
        for target in config.targets
    ]
    return {
        "state": "valid" if config.valid else "invalid",
        "sources": list(config.sources),
        "targets": targets,
        "errors": list(config.errors),
    }


def _audit_block(kind: str, capability_id: str, content_hash: str, *, reason: str, sources: tuple[str, ...]) -> None:
    try:
        from app.core.audit import record

        record(
            "security.capability_blocked",
            "CapabilityManifest",
            {
                "kind": kind,
                "capability_id_hash": _identifier_digest(capability_id),
                "content_hash": _normalize_hash(content_hash),
                "reason": reason,
                "revocation_sources": list(sources),
            },
        )
    except Exception:  # noqa: BLE001 - broad-exception-boundary; a failed audit write must not reopen a revoked capability.
        return


def _identifier_digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _normalize_hash(value: str) -> str:
    text = str(value or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return "sha256:" + text
    if re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        return text
    return ""


def _safe_capability_id(value: Any) -> str:
    return _safe_text(value, limit=512)


def _safe_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip().replace("\x00", "")
    text = " ".join(text.splitlines())
    return text[:limit]


def _public_label(value: Any, *, limit: int) -> str:
    text = _safe_text(value, limit=limit)
    if not text or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]*", text):
        return text
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mapping_value(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    return {
        key: getattr(value, key)
        for key in (
            "allowed_tools",
            "command",
            "enabled",
            "name",
            "owner",
            "policy_id",
            "transport",
            "url",
        )
        if hasattr(value, key)
    }


def _drop_metadata_timestamps(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_metadata_timestamps(item)
            for key, item in value.items()
            if str(key) not in {"created_at", "updated_at"}
        }
    if isinstance(value, list | tuple):
        return [_drop_metadata_timestamps(item) for item in value]
    return value


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple | set | frozenset):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _sanitize_url(value: str) -> str:
    return str(_sanitize_url_details(value)["endpoint"])


def _sanitize_url_details(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError:
        return {"endpoint": "", "options": []}
    if not parsed.scheme or not parsed.netloc:
        return {"endpoint": text.split("?", 1)[0].split("#", 1)[0], "options": []}
    hostname = parsed.hostname or ""
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        port = ""
    netloc = f"{hostname}{port}"
    options = [
        [key, "<redacted>" if _is_sensitive_key(key) else value]
        for key, value in sorted(parse_qsl(parsed.query, keep_blank_values=True))
    ]
    return {
        "endpoint": urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "", "", "")),
        "options": options,
    }


def _sanitize_command_args(value: Any) -> list[Any]:
    args = _string_list(value)
    result: list[Any] = []
    redact_next = False
    for arg in args:
        if redact_next:
            result.append("<redacted>")
            redact_next = False
            continue
        key, separator, _arg_value = arg.partition("=")
        normalized_key = key.lstrip("-/").replace("-", "_")
        if _is_sensitive_key(normalized_key):
            result.append(f"{key}=<redacted>" if separator else key)
            redact_next = not separator
            continue
        if "://" in arg:
            result.append(_sanitize_url_details(arg))
            continue
        result.append(arg)
    return result


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    collapsed = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_KEYS
        or collapsed
        in {
            "accesstoken",
            "apikey",
            "authtoken",
            "clientsecret",
            "jwtsecret",
            "privatekey",
            "refreshtoken",
            "sessionid",
        }
        or normalized.endswith(("_api_key", "_auth_token", "_password", "_private_key", "_secret", "_token"))
    )


def _reset_observed_for_tests() -> None:
    with _OBSERVED_LOCK:
        _OBSERVED.clear()
