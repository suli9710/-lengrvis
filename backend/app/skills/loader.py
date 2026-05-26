from __future__ import annotations

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
from app.policy.risk import RiskLevel
from app.skills.sandbox import SkillSandbox, SkillSandboxError, is_loopback_http_url
from app.skills.schemas import (
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


def scan_skill_directories(skill_directories: Iterable[str | Path]) -> list[LoadedSkillPackage]:
    packages: list[LoadedSkillPackage] = []
    for raw_directory in skill_directories:
        directory = Path(raw_directory).expanduser()
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise SkillLoadError("Configured skill path is not a directory", path=directory)
        manifests = _find_manifests(directory)
        for manifest in manifests:
            packages.append(load_skill_package(manifest.parent))
    return packages


def load_skill_package(skill_root: str | Path) -> LoadedSkillPackage:
    root = Path(skill_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise SkillLoadError("Skill package root is not a directory", path=root)
    manifest = _manifest_for(root)
    raw = _load_manifest(manifest)
    try:
        definition = SkillDefinition.model_validate(raw)
    except ValidationError as exc:
        raise SkillLoadError(f"Invalid skill.yaml: {exc}", path=manifest) from exc

    safety_report = review_skill_definition(definition, root)
    if not safety_report.ok:
        raise SkillLoadError("Unsafe skill definition: " + "; ".join(safety_report.error_messages()), path=manifest)

    tool_definitions = adapt_skill_to_tool_definitions(definition, root)
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
    packages = scan_skill_directories(directories)
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


def adapt_skill_to_tool_definitions(definition: SkillDefinition, root: str | Path) -> list[ToolDefinition]:
    skill_root = Path(root).resolve(strict=True)
    sandbox = SkillSandbox(skill_root)
    tool_definitions: list[ToolDefinition] = []
    for tool in definition.tools:
        risk = definition.effective_risk(tool)
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
                defer_loading=True,
                trust_tier="skill",
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
