from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is optional at import time.
    yaml = None

from pydantic import ValidationError

from app.config import AppSettings
from app.core.audit import record
from app.policy.risk import RISK_ORDER, RiskLevel
from app.skills.sandbox import SkillSandbox, SkillSandboxError, is_loopback_http_url
from app.skills.schemas import (
    LEGACY_PERMISSION,
    SkillDefinition,
    SkillExecutionType,
    SkillLoadError,
    SkillSafetyIssue,
    SkillSafetyReport,
    SkillToolSpec,
)
from app.tools.schemas import ToolDefinition


SKILL_MANIFEST_NAMES = ("skill.yaml", "skill.yml")
SENSITIVE_HEADER_HINTS = ("authorization", "cookie", "key", "password", "secret", "token")
HIGH_RISK_PERMISSION_PREFIXES = (
    "credential.",
    "filesystem.delete",
    "messaging.send",
    "network.external",
    "process.kill",
    "process.terminate",
    "shell.execute",
    "system.apply",
    "system.control",
    "system.modify",
    "system.set",
    "system.write",
    "ui.control",
    "ui.input",
)


@dataclass(slots=True)
class LoadedSkillPackage:
    root: Path
    manifest_path: Path
    definition: SkillDefinition
    safety_report: SkillSafetyReport
    tool_definitions: list[ToolDefinition]


def skill_directories_from_settings(settings: AppSettings) -> list[Path]:
    configured = [Path(path) for path in getattr(settings, "skill_directories", []) if str(path).strip()]
    if configured:
        return configured
    return [Path(settings.data_dir) / "skills"]


def scan_skill_directories(
    skill_directories: Iterable[str | Path],
    *,
    allow_unsafe_local_skill_execution: bool | None = None,
) -> list[LoadedSkillPackage]:
    packages: list[LoadedSkillPackage] = []
    for raw_directory in skill_directories:
        directory = Path(raw_directory).expanduser()
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise SkillLoadError("Configured skill path is not a directory", path=directory)
        manifests = _find_manifests(directory)
        for manifest in manifests:
            packages.append(
                load_skill_package(
                    manifest.parent,
                    allow_unsafe_local_skill_execution=allow_unsafe_local_skill_execution,
                )
            )
    return packages


def load_skill_package(
    skill_root: str | Path,
    *,
    allow_unsafe_local_skill_execution: bool | None = None,
) -> LoadedSkillPackage:
    root = Path(skill_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SkillLoadError("Skill package root is not a directory", path=root)
    manifest = _manifest_for(root)
    raw = _load_manifest(manifest)
    raw = _hydrate_schema_paths(raw, root, manifest)
    try:
        definition = SkillDefinition.model_validate(raw)
    except ValidationError as exc:
        raise SkillLoadError(f"Invalid skill.yaml: {exc}", path=manifest) from exc

    safety_report = review_skill_definition(definition, root)
    if not safety_report.ok:
        raise SkillLoadError("Unsafe skill definition: " + "; ".join(safety_report.error_messages()), path=manifest)

    tool_definitions = adapt_skill_to_tool_definitions(
        definition,
        root,
        allow_unsafe_local_skill_execution=allow_unsafe_local_skill_execution,
    )
    return LoadedSkillPackage(
        root=root,
        manifest_path=manifest,
        definition=definition,
        safety_report=safety_report,
        tool_definitions=tool_definitions,
    )


def register_skills(
    registry: Any,
    *,
    settings: AppSettings | None = None,
    skill_directories: Iterable[str | Path] | None = None,
) -> list[LoadedSkillPackage]:
    directories = list(skill_directories) if skill_directories is not None else skill_directories_from_settings(settings or AppSettings.from_sources())
    packages = scan_skill_directories(
        directories,
        allow_unsafe_local_skill_execution=(
            getattr(settings, "allow_unsafe_local_skill_execution", None) if settings is not None else None
        ),
    )
    existing_names = {tool.name for tool in registry.list()}
    for package in packages:
        for definition in package.tool_definitions:
            if definition.name in existing_names:
                raise SkillLoadError(f"Skill tool name collides with an existing tool: {definition.name}", path=package.manifest_path)
            registry.register(definition)
            existing_names.add(definition.name)
    if packages:
        record(
            "skills.loaded",
            "SkillLoader",
            {
                "packages": [package.definition.name for package in packages],
                "tools": [tool.name for package in packages for tool in package.tool_definitions],
            },
        )
    return packages


def adapt_skill_to_tool_definitions(
    definition: SkillDefinition,
    root: str | Path,
    *,
    allow_unsafe_local_skill_execution: bool | None = None,
) -> list[ToolDefinition]:
    skill_root = Path(root).resolve(strict=True)
    sandbox = SkillSandbox(
        skill_root,
        allow_unsafe_local_skill_execution=allow_unsafe_local_skill_execution,
    )
    tool_definitions: list[ToolDefinition] = []
    for tool in definition.tools:
        risk = definition.effective_risk(tool)
        permissions = definition.effective_permissions(tool)
        effects = _effects_from_permissions(permissions, risk)
        destructive = _is_destructive_permission_set(permissions) or risk == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        read_only = _is_read_only_skill_tool(permissions, risk)
        tool_definitions.append(
            ToolDefinition(
                name=tool.name,
                description=tool.description or tool.name,
                input_schema=tool.input_schema,
                output_schema=tool.output_schema,
                risk_level=risk,
                agent_owner=definition.effective_agent_owner(tool),
                supports_dry_run=tool.supports_dry_run,
                requires_authorized_path=tool.requires_authorized_path,
                execute=_build_executor(sandbox, tool),
                search_hint=_skill_search_hint(definition, tool),
                read_only=read_only,
                concurrency_safe=read_only and RISK_ORDER[risk] <= RISK_ORDER[RiskLevel.R1_OPEN_ONLY],
                destructive=destructive,
                defer_loading=True,
                trust_tier="skill",
                capabilities=list(permissions),
                effects=effects,
                resource_kinds=_resource_kinds_from_permissions(permissions),
                external_network=any(permission.startswith("network.external") for permission in permissions),
                fast_path_eligible=False,
                app_target=tool.app_target.model_dump(mode="json") if tool.app_target else None,
                workflow=tool.workflow.model_dump(mode="json") if tool.workflow else None,
            )
        )
    return tool_definitions


def review_skill_definition(definition: SkillDefinition, root: str | Path) -> SkillSafetyReport:
    """Pre-install safety hook for local skill packages.

    The hook is deliberately local and deterministic today. A future importer can
    call it before copying a skill into the configured skills directory and show
    the resulting issues to the user.
    """

    skill_root = Path(root).resolve(strict=True)
    sandbox = SkillSandbox(skill_root)
    issues: list[SkillSafetyIssue] = []
    for index, tool in enumerate(definition.tools):
        location = f"tools[{index}] ({tool.name})"
        risk = definition.effective_risk(tool)
        permissions = definition.effective_permissions(tool)
        if _is_legacy_permissions(permissions):
            issues.append(
                SkillSafetyIssue(
                    severity="warning",
                    location=f"{location}.permissions",
                    message="permissions missing; using legacy.unspecified compatibility default.",
                )
            )

        minimum_risk = _minimum_risk_for_permissions(permissions)
        if RISK_ORDER[risk] < RISK_ORDER[minimum_risk]:
            severity = "error" if RISK_ORDER[minimum_risk] >= RISK_ORDER[RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM] else "warning"
            issues.append(
                SkillSafetyIssue(
                    severity=severity,
                    location=f"{location}.risk",
                    message=f"declared permissions imply at least {minimum_risk.value}, but tool risk is {risk.value}.",
                )
            )

        has_high_risk_permissions = _has_high_risk_permission(permissions)
        high_risk_surface = has_high_risk_permissions or risk == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        if high_risk_surface and not tool.smoke_tests:
            issues.append(
                SkillSafetyIssue(
                    severity="error" if has_high_risk_permissions else "warning",
                    location=f"{location}.smoke_tests",
                    message="high-risk skill tools should declare at least one smoke test metadata entry.",
                )
            )
        if high_risk_surface and not tool.rollback_hint:
            issues.append(
                SkillSafetyIssue(
                    severity="error" if has_high_risk_permissions else "warning",
                    location=f"{location}.rollback_hint",
                    message="high-risk skill tools should declare a rollback or handoff hint.",
                )
            )

        if risk == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            issues.append(
                SkillSafetyIssue(
                    severity="error",
                    location=location,
                    message="R4_FORBIDDEN_OR_HANDOFF skill tools cannot be installed for execution.",
                )
            )
        if risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM} and not tool.supports_dry_run:
            issues.append(
                SkillSafetyIssue(
                    severity="error",
                    location=f"{location}.supports_dry_run",
                    message="R2/R3 skill tools must support dry-run previews.",
                )
            )

        execution = tool.execution
        if execution.type in {SkillExecutionType.PYTHON, SkillExecutionType.SHELL}:
            try:
                entry = sandbox.resolve_local_entry(execution)
            except SkillSandboxError as exc:
                issues.append(SkillSafetyIssue(severity="error", location=f"{location}.execution.entry", message=str(exc)))
                continue
            if execution.type == SkillExecutionType.PYTHON and entry.suffix.lower() != ".py":
                issues.append(
                    SkillSafetyIssue(
                        severity="error",
                        location=f"{location}.execution.entry",
                        message="python execution entries must point to .py files.",
                    )
                )
        elif execution.type == SkillExecutionType.HTTP:
            parsed = urlparse(execution.entry)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                issues.append(
                    SkillSafetyIssue(
                        severity="error",
                        location=f"{location}.execution.entry",
                        message="http execution entries must be absolute http(s) URLs.",
                    )
                )
            elif not is_loopback_http_url(execution.entry):
                issues.append(
                    SkillSafetyIssue(
                        severity="error",
                        location=f"{location}.execution.entry",
                        message="http execution entries must use a loopback host.",
                    )
                )
            for key, value in execution.headers.items():
                combined = f"{key} {value}".lower()
                if any(hint in combined for hint in SENSITIVE_HEADER_HINTS):
                    issues.append(
                        SkillSafetyIssue(
                            severity="error",
                            location=f"{location}.execution.headers.{key}",
                            message="secret-like HTTP headers are not allowed in skill manifests.",
                        )
                    )
        else:  # pragma: no cover - guarded by pydantic enum validation.
            issues.append(SkillSafetyIssue(severity="error", location=f"{location}.execution.type", message="unsupported execution type."))
    return SkillSafetyReport(issues=issues)


def _is_legacy_permissions(permissions: list[str]) -> bool:
    return permissions == [LEGACY_PERMISSION]


def _has_high_risk_permission(permissions: list[str]) -> bool:
    if _is_legacy_permissions(permissions):
        return False
    return any(permission.startswith(HIGH_RISK_PERMISSION_PREFIXES) for permission in permissions)


def _minimum_risk_for_permissions(permissions: list[str]) -> RiskLevel:
    if _is_legacy_permissions(permissions):
        return RiskLevel.R0_READ_ONLY
    minimum = RiskLevel.R0_READ_ONLY
    for permission in permissions:
        segments = set(permission.split("."))
        namespace = permission.split(".", 1)[0]
        if _permission_requires_r3(permission, namespace, segments):
            minimum = max(minimum, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM, key=lambda risk: RISK_ORDER[risk])
        elif segments & {"create", "input", "modify", "send", "write"}:
            minimum = max(minimum, RiskLevel.R2_REVERSIBLE_MODIFY, key=lambda risk: RISK_ORDER[risk])
        elif "open" in segments:
            minimum = max(minimum, RiskLevel.R1_OPEN_ONLY, key=lambda risk: RISK_ORDER[risk])
    return minimum


def _permission_requires_r3(permission: str, namespace: str, segments: set[str]) -> bool:
    if permission.startswith(("credential.", "filesystem.delete", "network.external", "ui.control", "ui.input")):
        return True
    if segments & {"delete", "destructive"}:
        return True
    if namespace == "shell" and segments & {"execute", "run", "write"}:
        return True
    if namespace == "system" and segments & {"apply", "control", "modify", "set", "write"}:
        return True
    if namespace == "process" and segments & {"kill", "terminate", "write"}:
        return True
    return False


def _effects_from_permissions(permissions: list[str], risk: RiskLevel) -> list[str]:
    if _is_legacy_permissions(permissions):
        if risk == RiskLevel.R0_READ_ONLY:
            return ["read"]
        if risk == RiskLevel.R1_OPEN_ONLY:
            return ["open"]
        if risk == RiskLevel.R2_REVERSIBLE_MODIFY:
            return ["write"]
        if risk == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM:
            return ["system"]
        return ["handoff"]

    effects: list[str] = []
    for permission in permissions:
        for segment in permission.split("."):
            if segment in {"inspect", "list", "read", "search"}:
                _append_unique(effects, segment)
            elif segment in {"create", "modify", "write"}:
                _append_unique(effects, "write")
            elif segment in {"control", "input"}:
                _append_unique(effects, "control")
            elif segment in {"delete", "send", "open"}:
                _append_unique(effects, segment)
    return effects or _effects_from_permissions([LEGACY_PERMISSION], risk)


def _resource_kinds_from_permissions(permissions: list[str]) -> list[str]:
    if _is_legacy_permissions(permissions):
        return ["skill"]
    kinds: list[str] = []
    for permission in permissions:
        namespace = permission.split(".", 1)[0]
        for kind in _resource_kinds_for_namespace(namespace):
            _append_unique(kinds, kind)
    return kinds or ["skill"]


def _resource_kinds_for_namespace(namespace: str) -> list[str]:
    return {
        "browser": ["web_page", "url"],
        "credential": ["secret"],
        "filesystem": ["file", "directory"],
        "memory": ["memory"],
        "messaging": ["message"],
        "network": ["url"],
        "process": ["process"],
        "shell": ["process"],
        "system": ["system"],
        "ui": ["application"],
    }.get(namespace, [namespace])


def _is_destructive_permission_set(permissions: list[str]) -> bool:
    for permission in permissions:
        segments = set(permission.split("."))
        namespace = permission.split(".", 1)[0]
        if segments & {"delete", "destructive"}:
            return True
        if namespace in {"process", "shell", "system"} and _permission_requires_r3(permission, namespace, segments):
            return True
    return False


def _is_read_only_skill_tool(permissions: list[str], risk: RiskLevel) -> bool:
    if risk != RiskLevel.R0_READ_ONLY:
        return False
    effects = set(_effects_from_permissions(permissions, risk))
    return effects.issubset({"inspect", "list", "read", "search"})


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _build_executor(sandbox: SkillSandbox, tool: SkillToolSpec):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return sandbox.execute(tool.execution, args, context)

    return execute


def _skill_search_hint(definition: SkillDefinition, tool: SkillToolSpec) -> str:
    parts = [
        definition.name,
        tool.name,
        tool.description,
        definition.effective_agent_owner(tool),
        tool.execution.type.value,
        " ".join(definition.effective_permissions(tool)),
    ]
    if tool.app_target:
        parts.extend(
            [
                tool.app_target.display_name,
                tool.app_target.app_id,
                tool.app_target.interface,
                " ".join(tool.app_target.capabilities),
            ]
        )
    if tool.workflow:
        parts.extend([tool.workflow.target_app, tool.workflow.action, tool.workflow.interface])
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def _hydrate_schema_paths(raw: dict[str, Any], root: Path, manifest: Path) -> dict[str, Any]:
    tools = raw.get("tools")
    if not isinstance(tools, list):
        return raw

    sandbox = SkillSandbox(root)
    hydrated = dict(raw)
    hydrated_tools: list[Any] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            hydrated_tools.append(tool)
            continue
        copied = dict(tool)
        for schema_field, path_names in (
            ("input_schema", ("input_schema_path", "inputSchemaPath")),
            ("output_schema", ("output_schema_path", "outputSchemaPath")),
        ):
            schema_path = next((copied.get(name) for name in path_names if copied.get(name)), "")
            if not schema_path:
                continue
            if not isinstance(schema_path, str):
                raise SkillLoadError(f"Invalid skill.yaml: tools[{index}].{schema_field}_path must be a string.", path=manifest)
            try:
                resolved = sandbox.resolve_package_file(schema_path, label=f"tools[{index}].{schema_field}_path")
            except SkillSandboxError as exc:
                raise SkillLoadError(f"Invalid skill.yaml: {exc}", path=manifest) from exc
            copied[schema_field] = _load_schema_file(resolved, schema_field)
        hydrated_tools.append(copied)
    hydrated["tools"] = hydrated_tools
    return hydrated


def _load_schema_file(path: Path, field_name: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
    except OSError as exc:
        raise SkillLoadError(f"Could not read {field_name} file: {exc}", path=path) from exc
    except json.JSONDecodeError as exc:
        raise SkillLoadError(f"Invalid JSON in {field_name} file: {exc}", path=path) from exc
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Invalid YAML in {field_name} file: {exc}", path=path) from exc
    if not isinstance(data, dict):
        raise SkillLoadError(f"{field_name}_path must point to a JSON schema object.", path=path)
    return data


def _find_manifests(directory: Path) -> list[Path]:
    direct = [directory / name for name in SKILL_MANIFEST_NAMES if (directory / name).exists()]
    if direct:
        return [direct[0]]
    manifests: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir():
            continue
        manifest = _manifest_for(child, required=False)
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def _manifest_for(root: Path, *, required: bool = True) -> Path | None:
    for name in SKILL_MANIFEST_NAMES:
        manifest = root / name
        if manifest.exists():
            return manifest
    if required:
        raise SkillLoadError("Skill package does not contain skill.yaml", path=root)
    return None


def _load_manifest(manifest: Path) -> dict[str, Any]:
    if yaml is None:
        raise SkillLoadError("PyYAML is required to load skill manifests.", path=manifest)
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise SkillLoadError(f"Could not read skill manifest: {exc}", path=manifest) from exc
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"Invalid YAML: {exc}", path=manifest) from exc
    if not isinstance(raw, dict):
        raise SkillLoadError("skill.yaml must contain a mapping/object.", path=manifest)
    return raw
