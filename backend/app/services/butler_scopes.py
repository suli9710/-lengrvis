from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil

from app.core import butler_store as store
from app.core.audit import record
from app.core.butler_models import AutonomyScope, ButlerMatter
from app.core.errors import SecurityError
from app.tools.filesystem_safety import ensure_mutation_path_safe


class ButlerScopeRevoked(ValueError):
    """A stored matter no longer has the exact user-owned authority it began with."""


def normalize_directory(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir() or str(path).startswith(("\\\\", "//")):
        raise ValueError("请选择已存在的本机绝对目录。")
    ensure_mutation_path_safe(path, [str(path)], include_self=True)
    return str(path.resolve())


def create_scope(scope: AutonomyScope, *, expected_version: int | None = None) -> AutonomyScope:
    scope.directories = list(dict.fromkeys(normalize_directory(item) for item in scope.directories))
    applications = []
    for value in scope.applications:
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("应用授权必须使用实际可执行文件的绝对路径。")
        applications.append(str(path.resolve()))
    scope.applications = list(dict.fromkeys(applications))
    if not (scope.directories or scope.applications or scope.origins):
        raise ValueError("至少选择一个目录、应用或网站。")
    if "file_modify" in scope.allowed_actions and not scope.directories:
        raise ValueError("自动文件操作需要目录范围。")
    if "ui_edit" in scope.allowed_actions and not scope.applications:
        raise ValueError("自动应用编辑需要应用范围。")
    if "browser_prepare" in scope.allowed_actions and not scope.origins:
        raise ValueError("自动网页准备需要网站范围。")
    saved = store.save(scope, expected_version=expected_version)
    record(
        "butler.scope_updated" if expected_version else "butler.scope_created",
        "user",
        {"scope_id": saved.id, "version": saved.version, "actions": saved.allowed_actions},
    )
    return saved


def enabled_scopes() -> list[AutonomyScope]:
    return [item for item in store.list_records(AutonomyScope) if item.enabled]


def matter_scopes(matter: ButlerMatter) -> list[AutonomyScope]:
    result = []
    for scope_id, version in matter.scope_versions.items():
        scope = store.get(AutonomyScope, scope_id)
        if scope is None or not scope.enabled or scope.version != version:
            raise ButlerScopeRevoked("事项的授权范围已撤销或改变。")
        result.append(scope)
    return result


def path_in_scope(path: str, scope: AutonomyScope) -> bool:
    target = Path(path).expanduser()
    if not target.is_absolute():
        return False
    target = target.resolve(strict=False)
    for root in scope.directories:
        try:
            target.relative_to(Path(root).resolve())
        except ValueError:
            continue
        try:
            ensure_mutation_path_safe(target, [root], include_self=True)
        except (OSError, ValueError, RuntimeError, SecurityError):
            return False
        return True
    return False


def origin_of(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        return ""
    host = parts.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}" + (
        f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    )


def application_in_scope(process_id: Any, scope: AutonomyScope) -> bool:
    try:
        executable = Path(psutil.Process(int(process_id)).exe()).resolve()
        return any(
            os.path.normcase(str(executable)) == os.path.normcase(str(Path(item).resolve()))
            for item in scope.applications
        )
    except (psutil.Error, OSError, ValueError, TypeError):
        return False


def action_scope(
    tool_name: str, args: dict, scopes: list[AutonomyScope], *, action: str = "observe"
) -> AutonomyScope | None:
    if tool_name.startswith("browser.") and isinstance(args.get("action"), dict):
        args = {**args, **args["action"]}
    if tool_name.startswith("ui_automation.") and isinstance(args.get("selector"), dict):
        args = {**args, **args["selector"]}
    for scope in scopes:
        if action not in scope.allowed_actions:
            continue
        if tool_name.startswith(("file.", "document.")):
            paths = [str(args[key]) for key in ("path", "source", "destination", "output_path") if args.get(key)]
            paths.extend(str(item) for item in args.get("paths", []) if isinstance(item, str))
            if paths and all(path_in_scope(path, scope) for path in paths):
                if tool_name == "file.rename":
                    if not path_in_scope(str(Path(args["source"]).with_name(str(args.get("new_name", "")))), scope):
                        continue
                return scope
            if (
                not paths
                and scope.directories
                and tool_name in {"file.search_by_name", "file.search_full_text", "file.semantic_search"}
            ):
                return scope
        if tool_name.startswith("ui_automation.") and application_in_scope(
            args.get("process_id") or args.get("processId"), scope
        ):
            return scope
        if tool_name.startswith("browser.") and origin_of(str(args.get("url", ""))) in scope.origins:
            account = str(args.get("account_id") or "")
            if scope.account_id and account != scope.account_id:
                continue
            return scope
    return None
