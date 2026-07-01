from __future__ import annotations

import json
import logging
import os
import platform
import re
import shlex
import subprocess
import time
from fnmatch import fnmatchcase
from pathlib import PureWindowsPath
from typing import Any

from app.core.audit import record
from app.core.paths import resolve_authorized
from app.core.subprocess_output import decode_process_output
from app.llm.registry import get_effective_settings
from app.policy.redaction import redact_public_text, redact_value
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import raise_if_tool_aborted
from app.tools.tool_catalog import tool_description, tool_search_hint

ALLOWLIST = {"notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe"}
logger = logging.getLogger(__name__)
BLOCKED_UNINSTALL_EXECUTABLES = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "wscript",
    "wscript.exe",
    "cscript",
    "cscript.exe",
    "mshta",
    "mshta.exe",
}
BLOCKED_UNINSTALL_EXTENSIONS = {".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta"}
UNINSTALL_TIMEOUT_SECONDS = 300
UNINSTALL_SCAN_TIMEOUT_SECONDS = 60
UNINSTALL_VERIFY_ATTEMPTS = 4
UNINSTALL_VERIFY_DELAY_SECONDS = 2.0
WINGET_ID_PATTERN = re.compile(r"^[A-Za-z0-9._\-]+$")
APPX_PACKAGE_PATTERN = re.compile(r"^[A-Za-z0-9._!\-]+$")

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
    categories = [category for category, hints in APP_CATEGORY_HINTS.items() if any(hint in haystack for hint in hints)]
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

        def optional_value(subkey: Any, value_name: str) -> str:
            try:
                return str(winreg.QueryValueEx(subkey, value_name)[0])
            except OSError:
                return ""

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
                                install_location = optional_value(subkey, "InstallLocation")
                                uninstall_string = optional_value(subkey, "UninstallString")
                                quiet_uninstall_string = optional_value(subkey, "QuietUninstallString")
                                publisher = optional_value(subkey, "Publisher")
                                version = optional_value(subkey, "DisplayVersion")
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
    except Exception as exc:  # noqa: BLE001 - registry scanning is best-effort.
        logger.debug("registry app scan failed: %s", exc, exc_info=True)
    return apps


def _scan_appx_packages() -> list[dict[str, Any]]:
    if platform.system().lower() != "windows":
        return []
    try:
        command = [  # noqa: S607 - Windows executable resolved by PATH.
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "Get-AppxPackage | Select-Object "
                "Name,PackageFullName,Publisher,Version,InstallLocation | ConvertTo-Json -Compress"
            ),
        ]
        completed = subprocess.run(  # noqa: S603 - fixed PowerShell command for local package inventory.
            command,
            capture_output=True,
            timeout=UNINSTALL_SCAN_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = decode_process_output(completed.stdout)
        if completed.returncode != 0 or not stdout.strip():
            return []
        payload = json.loads(stdout)
        rows = payload if isinstance(payload, list) else [payload]
        apps: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()
            package_full_name = str(row.get("PackageFullName") or "").strip()
            if not name or not package_full_name:
                continue
            apps.append(
                {
                    "id": name.lower(),
                    "name": name,
                    "path": str(row.get("InstallLocation") or "").strip(),
                    "publisher": str(row.get("Publisher") or "").strip(),
                    "version": str(row.get("Version") or "").strip(),
                    "package_full_name": package_full_name,
                    "uninstall_string": "",
                    "quiet_uninstall_string": "",
                    "source": "appx",
                }
            )
        return apps
    except Exception as exc:  # noqa: BLE001 - appx scanning is best-effort.
        logger.debug("appx package scan failed: %s", exc, exc_info=True)
        return []


def _scan_winget_packages() -> list[dict[str, Any]]:
    try:
        command = [  # noqa: S607 - Windows executable resolved by PATH.
            "winget",
            "list",
            "--output",
            "json",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        completed = subprocess.run(  # noqa: S603 - fixed winget inventory command.
            command,
            capture_output=True,
            timeout=UNINSTALL_SCAN_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = decode_process_output(completed.stdout)
        if completed.returncode != 0 or not stdout.strip():
            return []
        payload = json.loads(stdout)
        rows = payload.get("Packages") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        apps: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            winget_id = str(row.get("Id") or row.get("id") or "").strip()
            name = str(row.get("Name") or row.get("name") or winget_id).strip()
            if not winget_id or winget_id.lower() == "name":
                continue
            apps.append(
                {
                    "id": winget_id.lower(),
                    "name": name,
                    "publisher": str(row.get("Publisher") or row.get("publisher") or "").strip(),
                    "version": str(row.get("Version") or row.get("version") or "").strip(),
                    "winget_id": winget_id,
                    "uninstall_string": "",
                    "quiet_uninstall_string": "",
                    "source": "winget",
                }
            )
        return apps
    except FileNotFoundError:
        return []
    except Exception as exc:  # noqa: BLE001 - winget scanning is best-effort.
        logger.debug("winget package scan failed: %s", exc, exc_info=True)
        return []


def _app_merge_key(app: dict[str, Any]) -> str:
    return str(app.get("id") or app.get("name") or "").strip().lower()


def _app_record_richness(app: dict[str, Any]) -> int:
    score = 0
    if _has_uninstall_capability(app):
        score += 20
    if str(app.get("publisher") or "").strip():
        score += 2
    if str(app.get("version") or "").strip():
        score += 2
    if str(app.get("path") or "").strip():
        score += 1
    if str(app.get("source") or "") == "registry":
        score += 3
    return score


def _has_uninstall_capability(app: dict[str, Any]) -> bool:
    if str(app.get("quiet_uninstall_string") or "").strip():
        return True
    if str(app.get("uninstall_string") or "").strip():
        return True
    if str(app.get("source") or "") == "appx" and str(app.get("package_full_name") or "").strip():
        return True
    if str(app.get("source") or "") == "winget" and str(app.get("winget_id") or "").strip():
        return True
    return False


def installed_apps(context: dict[str, Any]) -> list[dict[str, Any]]:
    apps = [
        {"id": key, "name": key, "command": value, "path": value, "source": "builtin"}
        for key, value in ALLOWLIST.items()
    ]
    apps.extend(_scan_shortcuts())
    apps.extend(_scan_registry_apps())
    apps.extend(_scan_appx_packages())
    apps.extend(_scan_winget_packages())
    merged: dict[str, dict[str, Any]] = {}
    for app in apps:
        key = _app_merge_key(app)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None or _app_record_richness(app) > _app_record_richness(existing):
            merged[key] = app
    unique = []
    for app in merged.values():
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
        if not _has_uninstall_capability(app):
            continue
        haystack = " ".join(
            str(app.get(key) or "").lower()
            for key in (
                "id",
                "name",
                "publisher",
                "path",
                "uninstall_string",
                "quiet_uninstall_string",
                "package_full_name",
                "winget_id",
            )
        )
        if query and query not in haystack:
            continue
        uninstall = str(app.get("uninstall_string") or "")
        quiet_uninstall = str(app.get("quiet_uninstall_string") or "")
        method = _describe_uninstall_method(app)
        matches.append(
            {
                "name": app.get("name"),
                "publisher": app.get("publisher", ""),
                "version": app.get("version", ""),
                "source": app.get("source", ""),
                "path": app.get("path", ""),
                "uninstall_string": uninstall,
                "quiet_uninstall_string": quiet_uninstall,
                "uninstall_method": method,
                "package_full_name": app.get("package_full_name", ""),
                "winget_id": app.get("winget_id", ""),
            }
        )
    return {"query": query, "matches": matches[:20], "count": len(matches)}


def _describe_uninstall_method(app: dict[str, Any]) -> str:
    if str(app.get("quiet_uninstall_string") or "").strip():
        return "quiet_registry"
    source = str(app.get("source") or "")
    if source == "winget" and str(app.get("winget_id") or "").strip():
        return "winget"
    if source == "appx" and str(app.get("package_full_name") or "").strip():
        return "appx"
    if str(app.get("uninstall_string") or "").strip():
        return "registry"
    return "unknown"


def _resolve_uninstall_plan(app: dict[str, Any]) -> tuple[str, list[str] | str]:
    quiet = str(app.get("quiet_uninstall_string") or "").strip()
    regular = str(app.get("uninstall_string") or "").strip()
    source = str(app.get("source") or "")
    if quiet:
        return "quiet_registry", _safe_uninstall_args(quiet)
    if source == "winget" and str(app.get("winget_id") or "").strip():
        winget_id = _validate_winget_id(str(app["winget_id"]).strip())
        return "winget", [
            "winget",
            "uninstall",
            "--id",
            winget_id,
            "-e",
            "-h",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
    if source == "appx" and str(app.get("package_full_name") or "").strip():
        return "appx", _validate_appx_package_name(str(app["package_full_name"]).strip())
    if regular:
        return "registry", _safe_uninstall_args(regular)
    raise ValueError("No uninstall method available for this application.")


def _truncate_process_output(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _safe_process_output(text: str) -> str:
    return redact_public_text(str(redact_value(_truncate_process_output(text)) or ""))


def _run_uninstall_command(
    command_args: list[str],
    *,
    timeout: int = UNINSTALL_TIMEOUT_SECONDS,
    abort_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        raise_if_tool_aborted(abort_context)
        completed = subprocess.run(  # noqa: S603 - command is selected from scanned uninstall entries.
            command_args,
            shell=False,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": _safe_process_output(decode_process_output(completed.stdout)),
            "stderr": _safe_process_output(decode_process_output(completed.stderr)),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Uninstaller timed out.", "timed_out": True}
    except OSError as exc:
        return {"returncode": -1, "stdout": "", "stderr": _safe_process_output(str(exc)), "timed_out": False}


def _validate_winget_id(winget_id: str) -> str:
    if not winget_id or not WINGET_ID_PATTERN.match(winget_id):
        raise ValueError("Winget package id is invalid.")
    return winget_id


def _validate_appx_package_name(package_full_name: str) -> str:
    if not package_full_name or not APPX_PACKAGE_PATTERN.match(package_full_name):
        raise ValueError("Appx package name is invalid.")
    return package_full_name


def _run_appx_uninstall(
    package_full_name: str,
    *,
    timeout: int = UNINSTALL_TIMEOUT_SECONDS,
    abort_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    package_full_name = _validate_appx_package_name(package_full_name)
    escaped = package_full_name.replace("'", "''")
    script = f"Remove-AppxPackage -Package '{escaped}'"
    return _run_uninstall_command(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
        abort_context=abort_context,
    )


def _entry_identity_keys(app: dict[str, Any]) -> dict[str, str]:
    keys: dict[str, str] = {}
    winget_id = str(app.get("winget_id") or "").strip().lower()
    package_full_name = str(app.get("package_full_name") or "").strip()
    uninstall_string = str(app.get("uninstall_string") or "").strip()
    quiet_uninstall = str(app.get("quiet_uninstall_string") or "").strip()
    source = str(app.get("source") or "").strip().lower()
    name = str(app.get("name") or "").strip().lower()
    if winget_id:
        keys["winget_id"] = winget_id
    if package_full_name:
        keys["package_full_name"] = package_full_name
    if uninstall_string:
        keys["uninstall_string"] = uninstall_string.lower()
    if quiet_uninstall:
        keys["quiet_uninstall_string"] = quiet_uninstall.lower()
    if source:
        keys["source"] = source
    if name:
        keys["name"] = name
    return keys


def _entries_match_identity(target: dict[str, str], candidate: dict[str, Any]) -> bool:
    if not _has_uninstall_capability(candidate):
        return False
    candidate_keys = _entry_identity_keys(candidate)
    if target.get("winget_id") and candidate_keys.get("winget_id") == target["winget_id"]:
        return True
    if target.get("package_full_name") and candidate_keys.get("package_full_name") == target["package_full_name"]:
        return True
    if (
        target.get("quiet_uninstall_string")
        and candidate_keys.get("quiet_uninstall_string") == target["quiet_uninstall_string"]
    ):
        return True
    if target.get("uninstall_string") and candidate_keys.get("uninstall_string") == target["uninstall_string"]:
        return True
    if (
        target.get("source") == "registry"
        and target.get("name")
        and candidate_keys.get("source") == "registry"
        and candidate_keys.get("name") == target["name"]
    ):
        return True
    return False


def _entry_still_present(app: dict[str, Any], context: dict[str, Any]) -> bool:
    identity = _entry_identity_keys(app)
    if not identity:
        return False
    for candidate in installed_apps(context):
        if _entries_match_identity(identity, candidate):
            return True
    return False


def _verify_removal(app: dict[str, Any], context: dict[str, Any]) -> bool:
    for attempt in range(UNINSTALL_VERIFY_ATTEMPTS):
        if not _entry_still_present(app, context):
            return True
        if attempt + 1 < UNINSTALL_VERIFY_ATTEMPTS:
            time.sleep(UNINSTALL_VERIFY_DELAY_SECONDS)
    return not _entry_still_present(app, context)


def _find_installed_app_record(selected: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    identity = _entry_identity_keys(selected)
    if not identity:
        return None
    best: dict[str, Any] | None = None
    best_score = -1
    for app in installed_apps(context):
        if not _entries_match_identity(identity, app):
            continue
        score = _app_record_richness(app)
        if score > best_score:
            best = app
            best_score = score
    return best


def uninstall_app(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    uninstall_string = str(args.get("uninstall_string") or "").strip()

    if uninstall_string:
        return {
            "ok": False,
            "error": (
                "Direct uninstall commands are not accepted. Search by app name and use a scanned uninstall entry."
            ),
        }

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
    app_record = _find_installed_app_record(selected, context) or selected
    try:
        method, command = _resolve_uninstall_plan(app_record)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    preview = {
        "dry_run": True,
        "action": "uninstall_app",
        "app": selected.get("name", query),
        "publisher": selected.get("publisher", ""),
        "version": selected.get("version", ""),
        "source": selected.get("source", ""),
        "uninstall_method": method,
        "uninstall_string": str(app_record.get("uninstall_string") or ""),
        "quiet_uninstall_string": str(app_record.get("quiet_uninstall_string") or ""),
        "package_full_name": app_record.get("package_full_name", ""),
        "winget_id": app_record.get("winget_id", ""),
        "message": "Approval is required before running the uninstaller.",
    }
    if args.get("dry_run", True):
        return preview

    raise_if_tool_aborted(context)
    if method == "appx":
        execution = _run_appx_uninstall(str(command), abort_context=context)
        command_audit = f"Remove-AppxPackage -Package {command}"
    else:
        execution = _run_uninstall_command(list(command), abort_context=context)
        command_audit = subprocess.list2cmdline(list(command))

    raise_if_tool_aborted(context)
    verified_removed = _verify_removal(app_record, context)
    still_present = not verified_removed
    ok = execution.get("returncode") == 0 and verified_removed and not execution.get("timed_out")
    record(
        "app.uninstall_app",
        "AppAgent",
        {
            "app": selected.get("name", query),
            "command": command_audit,
            "method": method,
            "returncode": execution.get("returncode"),
            "verified_removed": verified_removed,
        },
    )
    if ok:
        message = "Application uninstall completed and removal was verified."
    elif execution.get("timed_out"):
        message = "Uninstaller timed out before completion."
    elif execution.get("returncode") != 0:
        message = "Uninstaller exited with a non-zero status."
    elif still_present:
        message = "Uninstaller finished but the application is still installed."
    else:
        message = "Uninstall finished with an unexpected outcome."

    return {
        "ok": ok,
        "app": selected.get("name", query),
        "uninstall_method": method,
        "returncode": execution.get("returncode"),
        "verified_removed": verified_removed,
        "still_present": still_present,
        "timed_out": execution.get("timed_out", False),
        "stdout": execution.get("stdout", ""),
        "stderr": execution.get("stderr", ""),
        "message": message,
    }


def _normalize_uninstall_command(command: str) -> str:
    if re.search(r"\bmsiexec(\.exe)?\b", command, flags=re.IGNORECASE) and re.search(
        r"\s/I\s*", command, flags=re.IGNORECASE
    ):
        command = re.sub(r"\s/I\s*", " /X ", command, count=1, flags=re.IGNORECASE)
    return command


def _safe_uninstall_args(command: str) -> list[str]:
    normalized = _normalize_uninstall_command(command.strip())
    if not normalized:
        raise ValueError("Uninstall entry has no command.")
    if re.search(r"[\r\n&|<>]", normalized):
        raise ValueError("Uninstall entry contains shell control characters and cannot be launched safely.")
    try:
        parts = [part.strip().strip('"') for part in shlex.split(normalized, posix=False) if part.strip()]
    except ValueError as exc:
        raise ValueError("Uninstall entry could not be parsed safely.") from exc
    if not parts:
        raise ValueError("Uninstall entry has no executable.")

    executable = PureWindowsPath(parts[0]).name.lower()
    extension = PureWindowsPath(parts[0]).suffix.lower()
    if executable in BLOCKED_UNINSTALL_EXECUTABLES or extension in BLOCKED_UNINSTALL_EXTENSIONS:
        raise ValueError("Uninstall entry uses a shell/script host and requires manual removal.")
    return _normalize_msiexec_args(parts)


def _normalize_msiexec_args(parts: list[str]) -> list[str]:
    if not parts or PureWindowsPath(parts[0]).name.lower() not in {"msiexec", "msiexec.exe"}:
        return parts
    normalized = list(parts)
    for index, arg in enumerate(normalized[1:], start=1):
        lower = arg.lower()
        if lower == "/i":
            normalized[index] = "/X"
            break
        if lower.startswith("/i"):
            normalized[index] = "/X" + arg[2:]
            break
    return normalized


def launch_allowlisted(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    app = str(args.get("app", "")).lower()
    if app not in ALLOWLIST:
        return {"ok": False, "error": "Application is not allowlisted and requires manual confirmation."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "command": ALLOWLIST[app]}
    raise_if_tool_aborted(context)
    subprocess.Popen([ALLOWLIST[app]], shell=False)  # noqa: S603 - allowlisted app launch command.
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
        return {
            "ok": True,
            "dry_run": True,
            "app": app_name,
            "path": path,
            "allowlist_match": match.get("allowlist_match", ""),
        }
    raise_if_tool_aborted(context)
    os.startfile(path)  # noqa: S606  # type: ignore[attr-defined]
    record("app.launch_installed", "AppAgent", {"app": app_name, "path": path})
    return {"ok": True, "app": app_name, "path": path, "launched": True}


def open_file(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if not path.is_file():
        return {"ok": False, "error": "Path is not a file."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    raise_if_tool_aborted(context)
    os.startfile(str(path))  # noqa: S606  # type: ignore[attr-defined]
    record("app.open_file", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "opened": True}


def open_folder(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if not path.is_dir():
        return {"ok": False, "error": "Path is not a folder."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    raise_if_tool_aborted(context)
    os.startfile(str(path))  # noqa: S606  # type: ignore[attr-defined]
    record("app.open_folder", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "opened": True}


def reveal_in_explorer(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(context)
    path = resolve_authorized(str(args.get("path", "")), settings.allowed_directories)
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "path": str(path)}
    raise_if_tool_aborted(context)
    if platform.system().lower() == "windows":
        subprocess.Popen(  # noqa: S603
            ["explorer", "/select,", str(path)],  # noqa: S607
            shell=False,
        )
    else:
        os.startfile(str(path.parent if path.is_file() else path))  # noqa: S606  # type: ignore[attr-defined]
    record("app.reveal_in_explorer", "AppAgent", {"path": str(path)})
    return {"ok": True, "path": str(path), "revealed": True}


def _input_schema(name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "app.list_installed": {"type": "object", "properties": {}, "additionalProperties": False},
        "app.find_uninstall_entries": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        "app.uninstall_app": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "app.launch_allowlisted": {
            "type": "object",
            "properties": {"app": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["app"],
            "additionalProperties": False,
        },
        "app.launch_installed": {
            "type": "object",
            "properties": {"app": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["app"],
            "additionalProperties": False,
        },
        "app.open_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "app.open_folder": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "app.reveal_in_explorer": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    }
    return schemas.get(name, {"type": "object", "properties": {}, "additionalProperties": False})


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
        effects = ["read", "inspect"] if risk == RiskLevel.R0_READ_ONLY else ["open"]
        if name in {"app.launch_allowlisted", "app.launch_installed"}:
            effects = ["launch"]
        elif name == "app.reveal_in_explorer":
            effects = ["reveal"]
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema=_input_schema(name),
                output_schema={},
                risk_level=risk,
                agent_owner="AppAgent",
                supports_dry_run=True,
                requires_authorized_path=False,
                execute=fn,
                capabilities=["application"],
                effects=effects,
                resource_kinds=["application", "file"],
                fast_path_eligible=risk in {RiskLevel.R0_READ_ONLY, RiskLevel.R1_OPEN_ONLY},
                trust_tier="builtin",
            )
        )
