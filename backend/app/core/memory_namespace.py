from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MEMORY_PRINCIPAL_ID = "local-user"
DEFAULT_MEMORY_WORKSPACE_ID = "default"
DEFAULT_MEMORY_DOMAIN_SCOPE = "general"
MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH = 128


@dataclass(frozen=True, slots=True)
class MemoryNamespace:
    principal_id: str
    workspace_id: str
    domain_scope: str


def normalize_memory_namespace(
    *,
    principal_id: str | None = None,
    workspace_id: str | None = None,
    domain_scope: str | None = None,
) -> MemoryNamespace:
    return MemoryNamespace(
        principal_id=_normalize_component(
            principal_id,
            default=DEFAULT_MEMORY_PRINCIPAL_ID,
            field_name="principal_id",
        ),
        workspace_id=_normalize_component(
            workspace_id,
            default=DEFAULT_MEMORY_WORKSPACE_ID,
            field_name="workspace_id",
        ),
        domain_scope=_normalize_component(
            domain_scope,
            default=DEFAULT_MEMORY_DOMAIN_SCOPE,
            field_name="domain_scope",
        ),
    )


def _normalize_component(value: str | None, *, default: str, field_name: str) -> str:
    if value is None:
        return default
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    if len(normalized) > MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH:
        raise ValueError(f"{field_name} cannot exceed {MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH} characters")
    return normalized
