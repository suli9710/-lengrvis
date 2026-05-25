from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.config import AppSettings
from app.core.audit import record
from app.core.errors import AppError, SecurityError
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.skills.loader import (
    LoadedSkillPackage,
    SKILL_MANIFEST_NAMES,
    load_skill_package,
    skill_directories_from_settings,
)
from app.skills.schemas import SkillLoadError
from app.tools.registry import register_all_tools, registry as tool_registry


class SkillServiceError(AppError):
    def __init__(self, message: str, *, code: str = "skill_error", status_code: int = 400) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


def list_installed_skills(settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    directories = skill_directories_from_settings(effective)
    skills: list[dict[str, Any]] = []
    for directory in directories:
        if not directory.exists():
            continue
        if not directory.is_dir():
            skills.append(
                {
                    "name": directory.name,
                    "version": "",
                    "agent_owner": "",
                    "risk": "",
                    "root": str(directory),
                    "manifest_path": "",
                    "status": "error",
                    "tools": [],
                    "safety": {"ok": False, "issues": []},
                    "error": "Configured skill path is not a directory.",
                }
            )
            continue
        for root in _iter_skill_roots(directory):
            skills.append(_skill_summary(root))
    return {
        "skills": skills,
        "count": len(skills),
        "directories": [str(directory) for directory in directories],
        "install_directory": str(_install_directory(effective)),
    }


async def import_skill(source_path: str, settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    source = Path(source_path).expanduser().resolve(strict=False)
    if not source.exists():
        raise SkillServiceError(f"Skill source does not exist: {source}")

    install_dir = _install_directory(effective)
    install_dir.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        package = _load_or_service_error(source)
        destination = _destination_for(install_dir, package)
        _copy_skill_directory(source, destination)
        return await _finalize_import(destination, package, source)

    if source.is_file() and source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="mavris-skill-") as temp_dir:
            extracted_root = Path(temp_dir) / "extract"
            extracted_root.mkdir()
            _extract_zip_safely(source, extracted_root)
            package_root = _single_skill_root(extracted_root)
            package = _load_or_service_error(package_root)
            destination = _destination_for(install_dir, package)
            _copy_skill_directory(package_root, destination)
            return await _finalize_import(destination, package, source)

    raise SkillServiceError("Skill source must be a directory or .zip file.")


def _load_or_service_error(path: Path) -> LoadedSkillPackage:
    try:
        return load_skill_package(path)
    except SkillLoadError as exc:
        raise SkillServiceError(str(exc), code="skill_validation_error") from exc


async def refresh_runtime_registry(settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(effective)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except Exception as exc:  # noqa: BLE001
        mcp_definitions = []
        record("mcp.refresh_load_failed", "SkillService", {"error": str(exc)})
    register_all_tools(extra_definitions=mcp_definitions, settings=effective)
    return {
        "ok": True,
        "tool_count": len(tool_registry.list()),
        "skill_count": list_installed_skills(effective)["count"],
    }


def _skill_summary(root: Path) -> dict[str, Any]:
    try:
        package = load_skill_package(root)
    except SkillLoadError as exc:
        manifest = _manifest_path(root)
        return {
            "name": root.name,
            "version": "",
            "agent_owner": "",
            "risk": "",
            "root": str(root),
            "manifest_path": str(manifest) if manifest else "",
            "status": "error",
            "tools": [],
            "safety": {"ok": False, "issues": []},
            "error": str(exc),
        }
    return _package_summary(package, status="ready")


def _package_summary(package: LoadedSkillPackage, *, status: str) -> dict[str, Any]:
    definition = package.definition
    return {
        "name": definition.name,
        "version": definition.version,
        "agent_owner": definition.agent_owner,
        "risk": definition.risk.value,
        "root": str(package.root),
        "manifest_path": str(package.manifest_path),
        "status": status,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "agent_owner": definition.effective_agent_owner(tool),
                "risk": definition.effective_risk(tool).value,
                "input_schema": tool.input_schema,
                "execution_type": tool.execution.type.value,
                "entry": tool.execution.entry,
            }
            for tool in definition.tools
        ],
        "safety": package.safety_report.model_dump(),
        "error": "",
    }


async def _finalize_import(destination: Path, package: LoadedSkillPackage, source: Path) -> dict[str, Any]:
    try:
        refresh = await refresh_runtime_registry()
    except Exception as exc:
        _remove_installed_copy(destination)
        try:
            await refresh_runtime_registry()
        except Exception:  # noqa: BLE001
            pass
        raise SkillServiceError(f"Skill failed registry refresh and was not installed: {exc}") from exc

    installed = load_skill_package(destination)
    record(
        "skills.imported",
        "SkillService",
        {
            "source": str(source),
            "destination": str(destination),
            "skill": installed.definition.name,
            "tools": [tool.name for tool in installed.tool_definitions],
        },
    )
    return {"skill": _package_summary(installed, status="ready"), "refresh": refresh}


def _install_directory(settings: AppSettings) -> Path:
    directories = skill_directories_from_settings(settings)
    return directories[0]


def _iter_skill_roots(directory: Path) -> list[Path]:
    if _manifest_path(directory):
        return [directory]
    roots: list[Path] = []
    for child in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
        if child.is_dir() and _manifest_path(child):
            roots.append(child)
    return roots


def _single_skill_root(extracted_root: Path) -> Path:
    roots = _iter_skill_roots(extracted_root)
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise SkillServiceError("Zip package does not contain a skill.yaml manifest.")
    raise SkillServiceError("Zip package must contain exactly one skill package.")


def _manifest_path(root: Path) -> Path | None:
    for name in SKILL_MANIFEST_NAMES:
        path = root / name
        if path.exists():
            return path
    return None


def _destination_for(install_dir: Path, package: LoadedSkillPackage) -> Path:
    folder_name = _safe_folder_name(f"{package.definition.name}-{package.definition.version}")
    return (install_dir / folder_name).resolve(strict=False)


def _safe_folder_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    cleaned = cleaned.strip(".-")
    return cleaned or "skill"


def _copy_skill_directory(source: Path, destination: Path) -> None:
    destination_parent = destination.parent.resolve(strict=False)
    destination_resolved = destination.resolve(strict=False)
    try:
        destination_resolved.relative_to(destination_parent)
    except ValueError as exc:  # pragma: no cover - defensive guard.
        raise SecurityError("Skill install destination escapes the skills directory.") from exc
    if destination_resolved.exists():
        _remove_installed_copy(destination_resolved)
    shutil.copytree(
        source,
        destination_resolved,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv", "node_modules"),
    )


def _remove_installed_copy(destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)


def _extract_zip_safely(source: Path, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise SkillServiceError("Skill zip file is invalid.") from exc
    with archive:
        destination_root = destination.resolve(strict=False)
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise SkillServiceError("Skill zip contains an unsafe path.")
            target = (destination_root / member.filename).resolve(strict=False)
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise SkillServiceError("Skill zip contains an unsafe path.") from exc
        archive.extractall(destination_root)
