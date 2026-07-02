from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import AppSettings
from app.core.audit import record
from app.core.errors import AppError, SecurityError
from app.core.paths import is_sensitive_path, is_system_path, normalize_path
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.observability.best_effort import log_best_effort_failure
from app.policy.risk import RISK_ORDER
from app.skills.loader import (
    SKILL_MANIFEST_NAMES,
    LoadedSkillPackage,
    load_skill_package,
    skill_directories_from_settings,
)
from app.skills.schemas import SkillLoadError
from app.tools.registry import register_all_tools
from app.tools.registry import registry as tool_registry

logger = logging.getLogger(__name__)


class SkillServiceError(AppError):
    def __init__(self, message: str, *, code: str = "skill_error", status_code: int = 400) -> None:
        super().__init__(code=code, message=message, status_code=status_code)


# Zip-bomb guards for skill package import: bound the decompressed footprint and
# member count so a small archive cannot fill the disk on extraction.
SKILL_ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
SKILL_ZIP_MAX_MEMBERS = 5000
SKILL_ZIP_MAX_COMPRESSION_RATIO = 200


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
            skills.append(_skill_summary(root, effective))
    return {
        "skills": skills,
        "count": len(skills),
        "directories": [str(directory) for directory in directories],
        "install_directory": str(_install_directory(effective)),
    }


async def import_skill(source_path: str, settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    raw_path = str(source_path or "").strip()
    if not raw_path:
        raise SkillServiceError("Skill import path must not be empty.", code="skill_import_path_denied")
    if any(char in raw_path for char in ("\x00", "\n", "\r")):
        raise SkillServiceError(
            "Skill import path must not contain control characters.",
            code="skill_import_path_denied",
        )
    source = Path(raw_path).expanduser().resolve(strict=False)
    _validate_skill_import_source(source, effective)
    if not source.exists():
        raise SkillServiceError(f"Skill source does not exist: {source}")

    install_dir = _install_directory(effective)
    install_dir.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        package = _load_or_service_error(source, effective)
        destination = _destination_for(install_dir, package)
        previous_package = _load_existing_package(destination, effective)
        rollback = _copy_skill_directory(source, destination)
        return await _finalize_import(
            destination,
            package,
            source,
            previous_package=previous_package,
            rollback=rollback,
            trusted_public_keys=effective.skill_trusted_public_keys,
        )

    if source.is_file() and source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory(prefix="lengrvis-skill-") as temp_dir:
            extracted_root = Path(temp_dir) / "extract"
            extracted_root.mkdir()
            _extract_zip_safely(source, extracted_root)
            package_root = _single_skill_root(extracted_root)
            package = _load_or_service_error(package_root, effective)
            destination = _destination_for(install_dir, package)
            previous_package = _load_existing_package(destination, effective)
            rollback = _copy_skill_directory(package_root, destination)
            return await _finalize_import(
                destination,
                package,
                source,
                previous_package=previous_package,
                rollback=rollback,
                trusted_public_keys=effective.skill_trusted_public_keys,
            )

    raise SkillServiceError("Skill source must be a directory or .zip file.")


def _load_or_service_error(path: Path, settings: AppSettings) -> LoadedSkillPackage:
    try:
        return load_skill_package(path, trusted_public_keys=settings.skill_trusted_public_keys)
    except SkillLoadError as exc:
        raise SkillServiceError(str(exc), code="skill_validation_error") from exc


async def refresh_runtime_registry(settings: AppSettings | None = None) -> dict[str, Any]:
    effective = settings or get_effective_settings()
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(effective)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except (KeyError, TypeError, ValueError) as exc:
        mcp_definitions = []
        log_best_effort_failure(logger, "skill.refresh_runtime_registry.mcp_definitions", exc)
        record("mcp.refresh_load_failed", "SkillService", {"error": str(exc)})
    register_all_tools(extra_definitions=mcp_definitions, settings=effective)
    return {
        "ok": True,
        "tool_count": len(tool_registry.list()),
        "skill_count": list_installed_skills(effective)["count"],
    }


def _skill_summary(root: Path, settings: AppSettings) -> dict[str, Any]:
    try:
        package = load_skill_package(root, trusted_public_keys=settings.skill_trusted_public_keys)
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
        "signature": _public_signature_summary(definition, package.signature_report),
        "root": str(package.root),
        "manifest_path": str(package.manifest_path),
        "status": status,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "agent_owner": definition.effective_agent_owner(tool),
                "risk": definition.effective_risk(tool).value,
                "permissions": definition.effective_permissions(tool),
                "input_schema": tool.input_schema,
                "execution_type": tool.execution.type.value,
                "entry": tool.execution.entry,
                "supports_dry_run": tool.supports_dry_run,
                "requires_authorized_path": tool.requires_authorized_path,
                "smoke_tests": [_public_smoke_test_summary(smoke_test) for smoke_test in tool.smoke_tests],
                "rollback_hint": tool.rollback_hint,
            }
            for tool in definition.tools
        ],
        "safety": package.safety_report.model_dump(),
        "error": "",
    }


def _public_smoke_test_summary(smoke_test: Any) -> dict[str, Any]:
    args = smoke_test.args if isinstance(smoke_test.args, dict) else {}
    expected = smoke_test.expected if isinstance(smoke_test.expected, dict) else {}
    return {
        "name": smoke_test.name,
        "description": smoke_test.description,
        "has_args": bool(args),
        "arg_keys": sorted(str(key) for key in args.keys()),
        "expected_keys": sorted(str(key) for key in expected.keys()),
    }


def _public_signature_summary(definition: Any, signature_report: dict[str, Any] | None = None) -> dict[str, Any]:
    signature = getattr(definition, "signature", None)
    if signature is None:
        return {
            "status": "unsigned",
            "key_id": "",
            "algorithm": "",
            "manifest_digest_present": False,
            "signed_at": "",
            "message": str((signature_report or {}).get("message") or ""),
        }
    status = "signed_metadata_present"
    message = ""
    if isinstance(signature_report, dict):
        status = str(signature_report.get("status") or status)
        message = str(signature_report.get("message") or "")
    return {
        "status": status,
        "key_id": signature.key_id,
        "algorithm": signature.algorithm,
        "manifest_digest_present": bool(signature.manifest_digest),
        "signed_at": signature.signed_at,
        "message": message,
    }


async def _finalize_import(
    destination: Path,
    package: LoadedSkillPackage,
    source: Path,
    *,
    previous_package: LoadedSkillPackage | None,
    rollback: _SkillInstallRollback,
    trusted_public_keys: dict[str, str],
) -> dict[str, Any]:
    try:
        refresh = await refresh_runtime_registry()
    except Exception as exc:
        log_best_effort_failure(
            logger,
            "skill.finalize_import.refresh",
            exc,
            skill=package.definition.name,
            version=package.definition.version,
        )
        await _rollback_failed_import(rollback, package)
        raise SkillServiceError(f"Skill failed registry refresh and was not installed: {exc}") from exc

    try:
        installed = load_skill_package(destination, trusted_public_keys=trusted_public_keys)
    except SkillLoadError as exc:
        await _rollback_failed_import(rollback, package)
        raise SkillServiceError(
            f"Skill failed validation after install and was not installed: {exc}",
            code="skill_validation_error",
        ) from exc
    _discard_install_backup(rollback, package)
    upgrade_diff = _package_upgrade_diff(previous_package, installed)
    record(
        "skills.imported",
        "SkillService",
        {
            "source": str(source),
            "destination": str(destination),
            "skill": installed.definition.name,
            "version": installed.definition.version,
            "signature": _public_signature_summary(installed.definition, installed.signature_report),
            "upgrade_diff": upgrade_diff,
            "tools": [tool.name for tool in installed.tool_definitions],
        },
    )
    return {"skill": _package_summary(installed, status="ready"), "refresh": refresh, "upgrade_diff": upgrade_diff}


async def _rollback_failed_import(rollback: _SkillInstallRollback, package: LoadedSkillPackage) -> None:
    try:
        rollback.restore()
    except Exception as restore_exc:  # noqa: BLE001
        log_best_effort_failure(
            logger,
            "skill.finalize_import.rollback_restore",
            restore_exc,
            skill=package.definition.name,
            version=package.definition.version,
        )
    try:
        await refresh_runtime_registry()
    except Exception as rollback_exc:  # noqa: BLE001
        log_best_effort_failure(
            logger,
            "skill.finalize_import.rollback_refresh",
            rollback_exc,
            skill=package.definition.name,
            version=package.definition.version,
        )


def _discard_install_backup(rollback: _SkillInstallRollback, package: LoadedSkillPackage) -> None:
    try:
        rollback.discard()
    except Exception as cleanup_exc:  # noqa: BLE001
        log_best_effort_failure(
            logger,
            "skill.finalize_import.backup_cleanup",
            cleanup_exc,
            skill=package.definition.name,
            version=package.definition.version,
        )


def _load_existing_package(destination: Path, settings: AppSettings) -> LoadedSkillPackage | None:
    if not destination.exists():
        return None
    try:
        return load_skill_package(destination, trusted_public_keys=settings.skill_trusted_public_keys)
    except SkillLoadError:
        return None


def _package_upgrade_diff(previous: LoadedSkillPackage | None, current: LoadedSkillPackage) -> dict[str, Any]:
    if previous is None:
        return {
            "kind": "new_install",
            "previous_version": "",
            "current_version": current.definition.version,
            "added_tools": sorted(tool.name for tool in current.definition.tools),
            "removed_tools": [],
            "changed_tools": [],
            "risk_increases": [],
            "permission_changes": [],
            "signature_status": _public_signature_summary(current.definition, current.signature_report)["status"],
        }

    previous_tools = {tool.name: tool for tool in previous.definition.tools}
    current_tools = {tool.name: tool for tool in current.definition.tools}
    changed_tools: list[str] = []
    risk_increases: list[dict[str, str]] = []
    permission_changes: list[dict[str, Any]] = []
    for name, tool in current_tools.items():
        old_tool = previous_tools.get(name)
        if old_tool is None:
            continue
        old_risk = previous.definition.effective_risk(old_tool)
        new_risk = current.definition.effective_risk(tool)
        old_permissions = previous.definition.effective_permissions(old_tool)
        new_permissions = current.definition.effective_permissions(tool)
        if old_risk != new_risk or old_permissions != new_permissions:
            changed_tools.append(name)
        if RISK_ORDER[new_risk] > RISK_ORDER[old_risk]:
            risk_increases.append({"tool": name, "from": old_risk.value, "to": new_risk.value})
        if old_permissions != new_permissions:
            permission_changes.append(
                {
                    "tool": name,
                    "from": old_permissions,
                    "to": new_permissions,
                }
            )

    return {
        "kind": "upgrade_or_replace",
        "previous_version": previous.definition.version,
        "current_version": current.definition.version,
        "added_tools": sorted(set(current_tools) - set(previous_tools)),
        "removed_tools": sorted(set(previous_tools) - set(current_tools)),
        "changed_tools": sorted(set(changed_tools)),
        "risk_increases": risk_increases,
        "permission_changes": permission_changes,
        "signature_status": _public_signature_summary(current.definition, current.signature_report)["status"],
    }


def _install_directory(settings: AppSettings) -> Path:
    directories = skill_directories_from_settings(settings)
    return directories[0]


def _validate_skill_import_source(source: Path, settings: AppSettings) -> None:
    if is_system_path(source) or is_sensitive_path(source):
        raise SkillServiceError(
            "Skill import source must not be a system or sensitive path.",
            code="skill_import_path_denied",
        )
    if _is_downloads_skill_import_source(source):
        return
    whitelist_roots = _skill_import_whitelist_roots(settings)
    if not whitelist_roots:
        raise SkillServiceError(
            "Skill import requires configured allowed_directories or skill_directories.",
            code="skill_import_path_denied",
        )
    for base in whitelist_roots:
        try:
            if source == base or source.is_relative_to(base):
                return
        except ValueError:
            continue
    raise SkillServiceError(
        "Skill import source is outside authorized directories.",
        code="skill_import_path_denied",
    )


def _is_downloads_skill_import_source(source: Path) -> bool:
    downloads = (Path.home() / "Downloads").expanduser().resolve(strict=False)
    try:
        if not (source == downloads or source.is_relative_to(downloads)):
            return False
    except ValueError:
        return False
    if source.suffix.lower() == ".zip":
        return True
    return _manifest_path(source) is not None


def _skill_import_whitelist_roots(settings: AppSettings) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in (*settings.allowed_directories, *skill_directories_from_settings(settings)):
        text = str(raw or "").strip()
        if not text:
            continue
        normalized = str(normalize_path(text))
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(Path(normalized))
    return roots


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


@dataclass
class _SkillInstallRollback:
    destination: Path
    backup_parent: Path | None = None
    backup_path: Path | None = None

    def restore(self) -> None:
        if self.destination.exists():
            _remove_installed_copy(self.destination)
        if self.backup_path and self.backup_path.exists():
            shutil.move(str(self.backup_path), str(self.destination))
        self.discard()

    def discard(self) -> None:
        if self.backup_parent and self.backup_parent.exists():
            shutil.rmtree(self.backup_parent)


def _copy_skill_directory(source: Path, destination: Path) -> _SkillInstallRollback:
    source_resolved = source.resolve(strict=False)
    destination_parent = destination.parent.resolve(strict=False)
    destination_resolved = destination.resolve(strict=False)
    try:
        destination_resolved.relative_to(destination_parent)
    except ValueError as exc:  # pragma: no cover - defensive guard.
        raise SecurityError("Skill install destination escapes the skills directory.") from exc
    _raise_if_skill_copy_self_references(source_resolved, destination_resolved)
    rollback = _prepare_skill_install_rollback(destination_resolved)
    try:
        shutil.copytree(
            source_resolved,
            destination_resolved,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv", "node_modules"),
        )
    except Exception:
        try:
            rollback.restore()
        except Exception as restore_exc:  # noqa: BLE001
            log_best_effort_failure(logger, "skill.copy.rollback_restore", restore_exc)
        raise
    return rollback


def _prepare_skill_install_rollback(destination: Path) -> _SkillInstallRollback:
    rollback = _SkillInstallRollback(destination=destination)
    if destination.exists():
        backup_parent = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.backup-", dir=str(destination.parent))
        ).resolve(strict=False)
        backup_path = backup_parent / destination.name
        shutil.move(str(destination), str(backup_path))
        rollback.backup_parent = backup_parent
        rollback.backup_path = backup_path
    return rollback


def _raise_if_skill_copy_self_references(source: Path, destination: Path) -> None:
    try:
        source_contains_destination = destination.is_relative_to(source)
    except ValueError:
        source_contains_destination = False
    try:
        destination_contains_source = source.is_relative_to(destination)
    except ValueError:
        destination_contains_source = False
    if source == destination or source_contains_destination or destination_contains_source:
        raise SkillServiceError(
            "Skill import source overlaps the install destination; choose the original package or zip to reinstall.",
            code="skill_import_path_denied",
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
        members = archive.infolist()
        if len(members) > SKILL_ZIP_MAX_MEMBERS:
            raise SkillServiceError("Skill zip contains too many files.")
        total_uncompressed = 0
        for member in members:
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise SkillServiceError("Skill zip contains an unsafe path.")
            target = (destination_root / member.filename).resolve(strict=False)
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise SkillServiceError("Skill zip contains an unsafe path.") from exc
            # Zip-bomb guard: reject before writing anything to disk.
            total_uncompressed += max(0, int(member.file_size))
            if total_uncompressed > SKILL_ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise SkillServiceError("Skill zip is too large when uncompressed.")
            if (
                member.compress_size > 0
                and member.file_size // max(1, member.compress_size) > SKILL_ZIP_MAX_COMPRESSION_RATIO
            ):
                raise SkillServiceError("Skill zip has a suspicious compression ratio.")
        archive.extractall(destination_root)
