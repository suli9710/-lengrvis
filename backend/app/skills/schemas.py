from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.policy.risk import RiskLevel


TOOL_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"
SKILL_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
AGENT_OWNER_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*$"
PERMISSION_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_-]*)+$"
LEGACY_PERMISSION = "legacy.unspecified"

RISK_ALIASES = {
    "r0": RiskLevel.R0_READ_ONLY,
    "r0_read_only": RiskLevel.R0_READ_ONLY,
    "read_only": RiskLevel.R0_READ_ONLY,
    "r1": RiskLevel.R1_OPEN_ONLY,
    "r1_open_only": RiskLevel.R1_OPEN_ONLY,
    "open_only": RiskLevel.R1_OPEN_ONLY,
    "r2": RiskLevel.R2_REVERSIBLE_MODIFY,
    "r2_reversible_modify": RiskLevel.R2_REVERSIBLE_MODIFY,
    "reversible_modify": RiskLevel.R2_REVERSIBLE_MODIFY,
    "r3": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    "r3_destructive_or_system": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    "destructive_or_system": RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    "r4": RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
    "r4_forbidden_or_handoff": RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
    "forbidden_or_handoff": RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
}

_PERMISSION_RE = re.compile(PERMISSION_NAME_PATTERN)


class SkillLoadError(ValueError):
    """Raised when a skill package cannot be loaded safely."""

    def __init__(self, message: str, *, path: str | Path | None = None) -> None:
        self.path = str(path) if path is not None else ""
        prefix = f"{self.path}: " if self.path else ""
        super().__init__(f"{prefix}{message}")


class SkillExecutionType(StrEnum):
    PYTHON = "python"
    SHELL = "shell"
    HTTP = "http"


def coerce_risk_level(value: Any) -> RiskLevel:
    if isinstance(value, RiskLevel):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("risk must not be empty")
    if raw in RiskLevel._value2member_map_:
        return RiskLevel(raw)
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in RISK_ALIASES:
        return RISK_ALIASES[normalized]
    raise ValueError(f"unsupported risk level: {raw}")


def extract_risk_default(value: Any, fallback: RiskLevel | None) -> Any:
    """Accept legacy string risks and product-manifest risk.default objects."""

    if not isinstance(value, dict):
        return value
    unknown = sorted(set(value) - {"default"})
    if unknown:
        raise ValueError(f"risk object supports only default; unknown keys: {', '.join(unknown)}")
    return value.get("default", fallback)


def normalize_permissions(value: Any, *, default: list[str] | None) -> list[str] | None:
    if value is None:
        return default
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("permissions must be a non-empty list of permission strings")
    if not value:
        raise ValueError("permissions must not be empty when declared")

    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("permissions must contain only strings")
        permission = item.strip()
        if not permission:
            raise ValueError("permission entries must not be empty")
        if not _PERMISSION_RE.fullmatch(permission):
            raise ValueError(f"permission has invalid format: {item}")
        if permission not in normalized:
            normalized.append(permission)
    return normalized


def strip_optional_manifest_path(value: str) -> str:
    text = str(value or "").strip()
    if any(char in text for char in ("\x00", "\n", "\r")):
        raise ValueError("manifest paths must not contain control characters")
    return text


class SkillExecution(BaseModel):
    """Declarative execution entry for one skill tool."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: SkillExecutionType
    entry: str = Field(min_length=1)
    method: str = "POST"
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("method must be one of GET, POST, PUT, PATCH, DELETE")
        return method

    @field_validator("entry")
    @classmethod
    def entry_has_no_control_chars(cls, value: str) -> str:
        if any(char in value for char in ("\x00", "\n", "\r")):
            raise ValueError("entry must not contain control characters")
        return value.strip()


class AppTargetSpec(BaseModel):
    """Optional target metadata for app/UIAutomation skill tools."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    app_id: str = Field(default="", validation_alias=AliasChoices("app_id", "appId"))
    display_name: str = Field(default="", validation_alias=AliasChoices("display_name", "displayName"))
    platform: str = "windows"
    interface: Literal["ui_automation", "com", "settings_uri", "shell", "http"] | str = "ui_automation"
    automation_id: str = Field(default="", validation_alias=AliasChoices("automation_id", "automationId"))
    window_title: str = Field(default="", validation_alias=AliasChoices("window_title", "windowTitle"))
    class_name: str = Field(default="", validation_alias=AliasChoices("class_name", "className"))
    capabilities: list[str] = Field(default_factory=list)


class WorkflowIntentSpec(BaseModel):
    """Declarative workflow hint embedded in a skill manifest."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    target_app: str = Field(default="", validation_alias=AliasChoices("target_app", "targetApp"))
    action: str = ""
    data_transfer: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("data_transfer", "dataTransfer"))
    interface: str = "ui_automation"


class SkillSmokeTestSpec(BaseModel):
    """Declarative smoke metadata for validating one skill tool manually or in CI."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(default="default", min_length=1, max_length=80)
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = value.strip()
        if any(char in text for char in ("\x00", "\n", "\r")):
            raise ValueError("smoke test text must not contain control characters")
        return text


class SkillToolSpec(BaseModel):
    """Tool declaration inside skill.yaml."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = ""
    agent_owner: str | None = Field(default=None, pattern=AGENT_OWNER_PATTERN, validation_alias=AliasChoices("agent_owner", "agentOwner"))
    risk: RiskLevel | None = None
    permissions: list[str] | None = None
    entrypoint: str = Field(default="", validation_alias=AliasChoices("entrypoint", "entryPoint"))
    input_schema_path: str = Field(default="", validation_alias=AliasChoices("input_schema_path", "inputSchemaPath"))
    output_schema_path: str = Field(default="", validation_alias=AliasChoices("output_schema_path", "outputSchemaPath"))
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": True},
        validation_alias=AliasChoices("input_schema", "inputSchema"),
    )
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object"},
        validation_alias=AliasChoices("output_schema", "outputSchema"),
    )
    execution: SkillExecution
    supports_dry_run: bool = Field(default=False, validation_alias=AliasChoices("supports_dry_run", "supportsDryRun"))
    requires_authorized_path: bool = Field(
        default=False,
        validation_alias=AliasChoices("requires_authorized_path", "requiresAuthorizedPath"),
    )
    app_target: AppTargetSpec | None = Field(
        default=None,
        validation_alias=AliasChoices("app_target", "appTarget"),
    )
    workflow: WorkflowIntentSpec | None = None
    smoke_tests: list[SkillSmokeTestSpec] = Field(
        default_factory=list,
        validation_alias=AliasChoices("smoke_tests", "smokeTests", "smoke_test", "smokeTest"),
    )
    rollback_hint: str = Field(default="", validation_alias=AliasChoices("rollback_hint", "rollbackHint"))

    @model_validator(mode="before")
    @classmethod
    def populate_execution_entry_from_entrypoint(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        entrypoint = data.get("entrypoint") or data.get("entryPoint")
        execution = data.get("execution")
        if entrypoint and isinstance(execution, dict) and not execution.get("entry"):
            copied = dict(data)
            copied["execution"] = {**execution, "entry": entrypoint}
            return copied
        return data

    @field_validator("risk", mode="before")
    @classmethod
    def normalize_optional_risk(cls, value: Any) -> RiskLevel | None:
        value = extract_risk_default(value, None)
        if value is None or value == "":
            return None
        return coerce_risk_level(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def normalize_tool_permissions(cls, value: Any) -> list[str] | None:
        return normalize_permissions(value, default=None)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("entrypoint", "input_schema_path", "output_schema_path")
    @classmethod
    def strip_manifest_paths(cls, value: str) -> str:
        return strip_optional_manifest_path(value)

    @field_validator("rollback_hint")
    @classmethod
    def strip_rollback_hint(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def manifest_contract_must_be_consistent(self) -> "SkillToolSpec":
        for field_name in ("input_schema", "output_schema"):
            schema = getattr(self, field_name)
            if not isinstance(schema, dict):
                raise ValueError(f"{field_name} must be a JSON schema object")
        if self.entrypoint and self.entrypoint != self.execution.entry:
            raise ValueError("entrypoint must match execution.entry when both are declared")
        self.entrypoint = self.execution.entry
        return self


class SkillDefinition(BaseModel):
    """Top-level skill.yaml schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=SKILL_NAME_PATTERN)
    version: str = Field(min_length=1)
    agent_owner: str = Field(pattern=AGENT_OWNER_PATTERN, validation_alias=AliasChoices("agent_owner", "agentOwner"))
    risk: RiskLevel = RiskLevel.R0_READ_ONLY
    permissions: list[str] = Field(default_factory=lambda: [LEGACY_PERMISSION])
    tools: list[SkillToolSpec] = Field(min_length=1)

    @field_validator("version", mode="before")
    @classmethod
    def stringify_version(cls, value: Any) -> str:
        return str(value).strip()

    @field_validator("risk", mode="before")
    @classmethod
    def normalize_risk(cls, value: Any) -> RiskLevel:
        value = extract_risk_default(value, RiskLevel.R0_READ_ONLY)
        if value is None or value == "":
            return RiskLevel.R0_READ_ONLY
        return coerce_risk_level(value)

    @field_validator("permissions", mode="before")
    @classmethod
    def normalize_manifest_permissions(cls, value: Any) -> list[str]:
        permissions = normalize_permissions(value, default=[LEGACY_PERMISSION])
        return permissions or [LEGACY_PERMISSION]

    @model_validator(mode="after")
    def tools_must_have_unique_names(self) -> "SkillDefinition":
        names = [tool.name for tool in self.tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")
        return self

    def effective_agent_owner(self, tool: SkillToolSpec) -> str:
        return tool.agent_owner or self.agent_owner

    def effective_risk(self, tool: SkillToolSpec) -> RiskLevel:
        return tool.risk or self.risk

    def effective_permissions(self, tool: SkillToolSpec) -> list[str]:
        return tool.permissions or self.permissions


class SkillSafetyIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning"]
    location: str
    message: str


class SkillSafetyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[SkillSafetyIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def error_messages(self) -> list[str]:
        return [f"{issue.location}: {issue.message}" for issue in self.issues if issue.severity == "error"]
