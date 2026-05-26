from __future__ import annotations

import os
import platform
import subprocess
from typing import Any

from app.core.audit import record
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def get_info(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    data = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }
    try:
        import psutil

        data.update(
            {
                "cpu_count": psutil.cpu_count(),
                "memory_total": psutil.virtual_memory().total,
                "memory_available": psutil.virtual_memory().available,
            }
        )
    except Exception as exc:
        data["psutil_error"] = str(exc)
    return data


def get_disks(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        return {
            "disks": [
                {
                    "device": p.device,
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "usage": psutil.disk_usage(p.mountpoint)._asdict(),
                }
                for p in psutil.disk_partitions()
            ]
        }
    except Exception as exc:
        return {"error": str(exc), "disks": []}


def get_network(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        return {"network": {name: [addr._asdict() for addr in addrs] for name, addrs in psutil.net_if_addrs().items()}}
    except Exception as exc:
        return {"error": str(exc), "network": {}}


def get_battery(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        battery = psutil.sensors_battery()
        return {"battery": battery._asdict() if battery else None}
    except Exception as exc:
        return {"error": str(exc), "battery": None}


def get_startup_items(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    startup_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
        os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
    ]
    for raw_dir in startup_dirs:
        if not raw_dir or "%" in raw_dir:
            continue
        try:
            for path in os.scandir(raw_dir):
                items.append({"name": path.name, "path": path.path, "source": "startup_folder"})
        except OSError:
            continue

    try:
        import winreg

        registry_locations = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
        ]
        for hive, key_path, source in registry_locations:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    index = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        items.append({"name": name, "command": str(value), "source": source})
                        index += 1
            except OSError:
                continue
    except Exception:
        pass

    return {"startup_items": items, "count": len(items)}


def open_settings_uri(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    uri = str(args.get("uri", "ms-settings:"))
    if not uri.startswith("ms-settings:"):
        return {"ok": False, "error": "Only ms-settings: URIs are allowed."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "uri": uri}
    if platform.system().lower() != "windows":
        return {"ok": False, "error": "Windows settings URIs are only supported on Windows."}
    os.startfile(uri)  # type: ignore[attr-defined]
    record("system.open_settings_uri", "ComputerAgent", {"uri": uri})
    return {"ok": True, "uri": uri, "opened": True}


def find_large_files(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    threshold_mb = float(args.get("threshold_mb") or 100)
    limit = int(args.get("limit") or 50)
    threshold_bytes = int(threshold_mb * 1024 * 1024)

    raw_roots = args.get("roots") or context.get("allowed_directories") or []
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    if not raw_roots:
        return {"files": [], "count": 0, "note": "No authorized roots configured."}

    results: list[dict[str, Any]] = []
    visited: set[str] = set()
    for raw in raw_roots:
        try:
            root_path = os.path.abspath(str(raw))
        except Exception:
            continue
        if not os.path.isdir(root_path):
            continue
        for current, _dirs, files in os.walk(root_path):
            for name in files:
                full = os.path.join(current, name)
                if full in visited:
                    continue
                visited.add(full)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                if stat.st_size < threshold_bytes:
                    continue
                results.append(
                    {
                        "path": full,
                        "name": name,
                        "size": stat.st_size,
                        "size_mb": round(stat.st_size / 1024 / 1024, 2),
                        "modified_at": stat.st_mtime,
                        "category": _categorize(name),
                    }
                )
    results.sort(key=lambda item: -int(item["size"]))
    return {"files": results[:limit], "count": len(results), "threshold_mb": threshold_mb}


def cleanup_suggestions(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    immediate: list[dict[str, Any]] = []
    approval: list[dict[str, Any]] = []
    info_only: list[dict[str, Any]] = []

    temp_dir = os.environ.get("TEMP") or os.path.expandvars(r"%TEMP%")
    if temp_dir and os.path.isdir(temp_dir):
        immediate.append({
            "action": "clean_temp",
            "path": temp_dir,
            "detail": "Windows %TEMP% directory is safe to clean periodically.",
        })

    cache_locations = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache"),
    ]
    for path in cache_locations:
        if path and os.path.isdir(path):
            approval.append({
                "action": "clear_browser_cache",
                "path": path,
                "detail": "Clearing browser cache is safe but requires user approval.",
            })

    # Surface the top-N largest files inside authorized directories.
    large = find_large_files({"threshold_mb": float(args.get("threshold_mb") or 200), "limit": 8}, context)
    for file_info in large.get("files", [])[:8]:
        info_only.append({
            "action": "review_large_file",
            "path": file_info["path"],
            "size_mb": file_info["size_mb"],
            "category": file_info["category"],
            "detail": "Large file in your workspace; review before deleting.",
        })

    return {
        "ok": True,
        "buckets": {
            "immediate": immediate,
            "approval": approval,
            "info_only": info_only,
        },
        "count": len(immediate) + len(approval) + len(info_only),
    }


def _categorize(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    media = {".mp4", ".mov", ".mkv", ".avi", ".mp3", ".wav", ".flac"}
    docs = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md"}
    archives = {".zip", ".rar", ".7z", ".tar", ".gz"}
    installers = {".msi", ".exe", ".iso", ".dmg"}
    if ext in media:
        return "media"
    if ext in docs:
        return "document"
    if ext in archives:
        return "archive"
    if ext in installers:
        return "installer"
    return "other"


def get_processes(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit", 25))
    try:
        import psutil

        processes = []
        for process in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "status"]):
            try:
                info = process.info
                memory_info = info.get("memory_info")
                processes.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "username": info.get("username"),
                        "cpu_percent": info.get("cpu_percent") or 0,
                        "memory_bytes": getattr(memory_info, "rss", 0) if memory_info else 0,
                        "status": info.get("status"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda item: int(item.get("memory_bytes") or 0), reverse=True)
        return {"processes": processes[:limit], "count": len(processes)}
    except Exception as exc:
        return {"error": str(exc), "processes": []}


def diagnostics(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    info = get_info(args, context)
    disks = get_disks(args, context)
    network = get_network(args, context)
    battery = get_battery(args, context)
    processes = get_processes({"limit": 8}, context)
    suggestions = []
    memory_total = int(info.get("memory_total") or 0)
    memory_available = int(info.get("memory_available") or 0)
    if memory_total and memory_available / memory_total < 0.15:
        suggestions.append("Memory is low; close large apps before running heavy automation.")
    if not suggestions:
        suggestions.append("No critical system issue detected from read-only diagnostics.")
    return {
        "info": info,
        "disks": disks.get("disks", []),
        "network": network.get("network", {}),
        "battery": battery.get("battery"),
        "top_processes": processes.get("processes", []),
        "suggestions": suggestions,
    }


def register(registry) -> None:
    defs = [
        ("system.get_info", get_info, RiskLevel.R0_READ_ONLY),
        ("system.get_disks", get_disks, RiskLevel.R0_READ_ONLY),
        ("system.get_network", get_network, RiskLevel.R0_READ_ONLY),
        ("system.get_battery", get_battery, RiskLevel.R0_READ_ONLY),
        ("system.get_startup_items", get_startup_items, RiskLevel.R0_READ_ONLY),
        ("system.open_settings_uri", open_settings_uri, RiskLevel.R1_OPEN_ONLY),
        ("system.find_large_files", find_large_files, RiskLevel.R0_READ_ONLY),
        ("system.cleanup_suggestions", cleanup_suggestions, RiskLevel.R0_READ_ONLY),
        ("system.get_processes", get_processes, RiskLevel.R0_READ_ONLY),
        ("system.diagnostics", diagnostics, RiskLevel.R0_READ_ONLY),
    ]
    for name, fn, risk in defs:
        read_only = risk == RiskLevel.R0_READ_ONLY
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={},
                risk_level=risk,
                agent_owner="ComputerAgent",
                supports_dry_run=risk != RiskLevel.R0_READ_ONLY,
                requires_authorized_path=False,
                execute=fn,
                capabilities=["system"],
                effects=["read", "inspect"] if read_only else ["open"],
                resource_kinds=["system"],
                fast_path_eligible=True,
                trust_tier="builtin",
            )
        )
