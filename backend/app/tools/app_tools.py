from __future__ import annotations

import os
import platform
import re
import subprocess
from fnmatch import fnmatchcase
from typing import Any

from app.core.audit import record
from app.core.paths import resolve_authorized
from app.llm.registry import get_effective_settings
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


ALLOWLIST = {"notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe"}

APP_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "browser": (
        "chrome",
        "edge",
        "firefox",
        "brave",
        "opera",
        "vivaldi",
        "browser",
    ),
    "developer": (
        "code",
        "visual studio",
        "powershell",
        "terminal",
        "git",
        "python",
        "node",
        "docker",
    ),
    "office": (
        "excel",
        "word",
        "powerpoint",
        "onenote",
        "outlook",
        "office",
        "libreoffice",
        "wps",
    ),
    "productivity": (
        "notepad",
        "calculator",
        "calc",
        "excel",
        "word",
        "powerpoint",
        "onenote",
        "outlook",
        "todo",
    ),
    "system": (
        "settings",
        "control panel",
        "explorer",
        "task manager",
        "powershell",
        "terminal",
    ),
    "utility": (
        "notepad",
        "calculator",
        "calc",
        "paint",
        "snipping",
        "7-zip",
        "winrar",
        "powertoys",
    ),
}


def _settings(context: dict[str, Any]):
    return context.get("settings") or get_effective_settings()


def _configured_allowlist(context: dict[str, Any]) -> set[str]:
    return set(_configured_allowlist_entries(context))


def _configured_allowlist_entries(context: dict[str, Any]) -> list[str]:
    settings = _settings(context)
    values = set(getattr(settings, "app_allowlist", []) or [])
    return sorted({str(value).strip().lower() for value in values if str(value).strip()} | set(ALLOWLIST))


def _app_fields(app: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("id", "name", "command", "path", "publisher", "source"):
        value = str(app.get(key) or "").strip().lower()
        if value:
            fields.append(value)
            if key in {"command", "path"}:
                fields.append(os.path.basename(value))
                fields.append(os.path.splitext(os.path.basename(value))[0])
    return list(dict.fromkeys(fields))


def _app_categories(app: dict[str, Any]) -> list[str]:
    haystack = " ".join(_app_fields(app))
    categories = [
        category
        for category, hints in APP_CATEGORY_HINTS.items()
        if any(hint in haystack for hint in hints)
    ]
    return sorted(set(categories))


def _allowlist_entry_matches(entry: str, app: dict[str, Any]) -> bool:
    normalized = entry.strip().lower()
    if not normalized:
        return False
    fields = _app_fields(app)
    categories = _app_categories(app)
    if normalized.startswith(("category:", "cat:", "group:")):
        wanted = normalized.split(":", 1)[1].strip()
        return wanted in categories
    if normalized.startswith(("publisher:", "pub:")):
        pattern = normalized.split(":", 1)[1].strip()
        publisher = str(app.get("publisher") or "").strip().lower()
        return bool(pattern and fnmatchcase(publisher, pattern))
    if normalized.startswith("source:"):
        pattern = normalized.split(":", 1)[1].strip()
        source = str(app.get("source") or "").strip().lower()
        return bool(pattern and fnmatchcase(source, pattern))
    if any(char in normalized for char in "*?[]"):
        return any(fnmatchcase(field, normalized) for field in fields)
    return normalized in fields


def _allowlist_match(app: dict[str, Any], context: dict[str, Any]) -> str:
    for entry in _configured_allowlist_entries(context):
        if _allowlist_entry_matches(entry, app):
            return entry
    return ""


def _find_installed_app(app_name: str, context: dict[str, Any]) -> dict[str, Any] | None:
    normalized = app_name.lower().strip()
    for app in installed_apps(context):
        if normalized in _app_fields(app):
            return app
    return None


def _shortcut_dirs() -> list[str]:
    return [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]


def _scan_shortcuts() -> list[dict[str, Any]]:
    apps = []
    for root in _shortcut_dirs():
        if not root or "%" in root:
            continue
        for current, _dirs, files in os.walk(root):
            for file_name in files:
                if not file_name.lower().endswith((".lnk", ".url")):
                    continue
                path = os.path.join(current, file_name)
                name = os.path.splitext(file_name)[0]
                apps.append({"id": name.lower(), "name": name, "path": path, "source": "start_menu"})
    return apps


def _scan_registry_apps() -> list[dict[str, Any]]:
    apps = []
    try:
        import winreg

        locations = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, key_path in locations:
            try:
                with winreg.OpenKey(hive, key_path) as root:
                    index = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, subkey_name) as subkey:
                                name = str(winreg.QueryValueEx(subkey, "DisplayName")[0])
                                install_location = ""
                                try:
                                    install_location = str(winreg.QueryValueEx(subkey, "InstallLocation")[0])
                                except OSError:
                                    pass
                                uninstall_string = ""
                                quiet_uninstall_string = ""
                                publisher = ""
                                version = ""
                                try:
                                    uninstall_string = str(winreg.QueryValueEx(subkey, "UninstallString")[0])
                                except OSError:
                                    pass
                                try:
                                    quiet_uninstall_string = str(winreg.QueryValueEx(subkey, "QuietUninstallString")[0])
                                except OSError:
                                    pass
                                try:
                                    publisher = str(winreg.QueryValueEx(subkey, "Publisher")[0])
                                except OSError:
                                    pass
                                try:
                                    version = str(winreg.QueryValueEx(subkey, "DisplayVersion")[0])
                                except OSError:
                                    pass
                                apps.append(
                                    {
                                        "id": name.lower(),
                                        "name": name,
                                        "path": install_location,
                                        "publisher": publisher,
                                        "version": version,
                                        "uninstall_string": uninstall_string,
                                        "quiet_uninstall_string": quiet_uninstall_string,
                                        "source": "registry",
                                    }
                                )
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:
        pass
    return apps


def installed_apps(context: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    apps = [{"id": key, "name": key, "command": value, "path": value, "source": "builtin"} for key, value in ALLOWLIST.items()]
    apps.extend(_scan_shortcuts())
    apps.extend(_scan_registry_apps())
    unique = []
    for app in apps:
        key = str(app.get("id") or app.get("name")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        categories = _app_categories(app)
        if categories:
            app["categories"] = categories
        match = _allowlist_match(app, context)
        app["allowlisted"] = bool(match)
        if match:
            app["allowlist_match"] = match
        unique.append(app)
    unique.sort(key=lambda item: (not bool(item.get("allowlisted")), str(item.get("name", "")).lower()))
    return unique


def list_installed(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {"apps": installed_apps(context)}


def find_uninstall_entries(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip().lower()
    matches = []
    for app in installed_apps(context):
        uninstall = str(app.get("uninstall_string") or "")
        if not uninstall:
            continue
        haystack = " ".join(
            str(app.get(key) or "").lower()
            for key in ("id", "name", "publisher", "path", "uninstall_string")
        )
        if query and query not in haystack:
            continue
        matches.append(
            {
                "name": app.get("name"),
                "publisher": app.get("publisher", ""),
                "version": app.get("version", ""),
                "source": app.get("source", ""),
                "path": app.get("path", ""),
                "uninstall_string": uninstall,
            }
        )
    return {"query": query, "matches": matches[:20], "count": len(matches)}


def uninstall_app(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    uninstall_string = str(args.get("uninstall_string") or "").strip()

    selected = None
    if uninstall_string:
        selected = {"name": query or "selected app", "uninstall_string": uninstall_string}
    else:
        matches = find_uninstall_entries({"query": query}, context)["matches"]
        if not matches:
            return {"ok": False, "error": f"No uninstall entry found for: {query}"}
        if len(matches) > 1:
            return {
                "ok": False,
                "error": "Multiple uninstall entries matched; refine the app name.",
                "matches": matches[:10],
            }
        selected = matches[0]
        uninstall_string = str(selected.get("uninstall_string") or "")

    preview = {
        "dry_run": True,
        "action": "uninstall_app",
        "app": selected.get("name", query),
        "publisher": selected.get("publisher", ""),
        "version": selected.get("version", ""),
        "uninstall_string": uninstall_string,
        "message": "Approval is required before launching the app uninstaller.",
    }
    if args.get("dry_run", True):
        return preview

    command = _normalize_uninstall_command(uninstall_string)
    subprocess.Popen(command, shell=True)
    record("app.uninstall_app", "AppAgent", {"app": selected.get("name", query), "command": uninstall_string})
    return {
        "ok": True,
        "app": selected.get("name", query),
        "launched": True,
        "message": "Uninstaller launched. Follow the vendor dialog to complete removal.",
    }


def _normalize_uninstall_command(command: str) -> str:
    if re.search(r"\bmsiexec(\.exe)?\b", command, flags=re.IGNORECASE) and re.search(r"\s/I\s*", command, flags=re.IGNORECASE):
        command = re.sub(r"\s/I\s*", " /X ", command, count=1, flags=re.IGNORECASE)
    return command


def launch_allowlisted(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app", "")).lower()
    if app not in ALLOWLIST:
        return {"ok": False, "error": "Application is not allowlisted and requires manual confirmation."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "command": ALLOWLIST[app]}
    subprocess.Popen([ALLOWLIST[app]], shell=False)
    record("app.launch_allowlisted", "AppAgent", {"app": app, "command": ALLOWLIST[app]})
    return {"ok": True, "app": app, "command": ALLOWLIST[app], "launched": True}


def launch_installed(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    app_name = str(args.get("app", "")).lower().strip()
    if not app_name:
        return {"ok": False, "error": "Missing app name."}
    if app_name in ALLOWLIST:
        return launch_allowlisted({"app": app_name, "dry_run": args.get("dry_run", False)}, context)
    match = _find_installed_app(app_name, context)
    if not match:
        if _allowlist_match({"id": app_name, "name": app_name, "path": app_name}, context):
            return {"ok": False, "error": "Allowlisted application was not found."}
        return {"ok": False, "error": "Application is not allowlisted."}
    if not match.get("allowlisted"):
        return {"ok": False, "error": "Application is not allowlisted."}
    path = str(match.get("path") or "")
    if not path:
        return {"ok": False, "error": "Application has no launchable path."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "app": app_name, "path": path, "allowlist_match": match.get("allowlist_match", "")}
    os.startfile(path)  # type: ignore[attr-defined]
    record("app.launch_installed", "AppAgent", {"app": app_name, "path": path})
    return {"ok": True, "app": app_name, "path": path, "launched": True}


def open_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if not path.is_file():
        return {"ok": False, "error": "Path is not a file."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    os.startfile(str(path))  # type: ignore[attr-defined]
    record("app.open_file", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "opened": True}


def open_folder(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if not path.is_dir():
        return {"ok": False, "error": "Path is not a folder."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    os.startfile(str(path))  # type: ignore[attr-defined]
    record("app.open_folder", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "opened": True}


def reveal_in_explorer(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    if platform.system().lower() == "windows":
        subprocess.Popen(["explorer", "/select,", str(path)], shell=False)
    else:
        os.startfile(str(path.parent if path.is_file() else path))  # type: ignore[attr-defined]
    record("app.reveal_in_explorer", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "revealed": True}


def register(registry) -> None:
    defs = [
        ("app.list_installed", list_installed, RiskLevel.R0_READ_ONLY),
        ("app.launch_allowlisted", launch_allowlisted, RiskLevel.R1_OPEN_ONLY),
        ("app.launch_installed", launch_installed, RiskLevel.R1_OPEN_ONLY),
        ("app.find_uninstall_entries", find_uninstall_entries, RiskLevel.R0_READ_ONLY),
        ("app.uninstall_app", uninstall_app, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM),
        ("app.open_file", open_file, RiskLevel.R1_OPEN_ONLY),
        ("app.open_folder", open_folder, RiskLevel.R1_OPEN_ONLY),
        ("app.reveal_in_explorer", reveal_in_explorer, RiskLevel.R1_OPEN_ONLY),
    ]
    for name, fn, risk in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={},
                output_schema={},
                risk_level=risk,
                agent_owner="AppAgent",
                supports_dry_run=True,
                requires_authorized_path=False,
                execute=fn,
            )
        )
