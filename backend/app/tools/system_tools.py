from __future__ import annotations

import logging
import os
import platform
import re
from typing import Any

from app.config import get_env
from app.core.audit import record
from app.core.paths import resolve_authorized
from app.policy.redaction import redact_public_text, redact_value
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import raise_if_tool_aborted
from app.tools.tool_catalog import tool_description, tool_search_hint

logger = logging.getLogger(__name__)
LOCAL_AI_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s,;，。；、]+|(?:/Users|/home)/[^\s,;，。；、]+|~[\\/][^\s,;，。；、]+)"
)
LOCAL_AI_URL_RE = re.compile(r"https?://[^\s'\"<>]+")


def _safe_diagnostic_error(error: Exception | str) -> str:
    text = str(error or "")
    return redact_public_text(text) if text else ""


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
    except Exception as exc:  # noqa: BLE001 - psutil diagnostics are best-effort.
        data["psutil_error"] = _safe_diagnostic_error(exc)
    return data


def get_disks(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        disks: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        for partition in psutil.disk_partitions(all=False):
            if _skip_disk_partition(partition):
                skipped.append({"mountpoint": str(partition.mountpoint), "reason": "non_fixed_or_remote"})
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)._asdict()
            except Exception as exc:  # noqa: BLE001 - diagnostics must stay best-effort.
                errors.append({"mountpoint": str(partition.mountpoint), "error": _safe_diagnostic_error(exc)})
                continue
            disks.append(
                {
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "fstype": partition.fstype,
                    "usage": usage,
                }
            )
        return {"disks": disks, "skipped": skipped[:8], "errors": errors[:8]}
    except Exception as exc:  # noqa: BLE001 - psutil diagnostics are best-effort.
        return {"error": _safe_diagnostic_error(exc), "disks": []}


def _skip_disk_partition(partition: Any) -> bool:
    mountpoint = str(getattr(partition, "mountpoint", "") or "").strip()
    device = str(getattr(partition, "device", "") or "").strip()
    fstype = str(getattr(partition, "fstype", "") or "").strip().lower()
    opts = {
        item.strip().lower()
        for item in str(getattr(partition, "opts", "") or "").replace(";", ",").split(",")
        if item.strip()
    }
    if not mountpoint or not fstype:
        return True
    if {"cdrom", "remote", "network"}.intersection(opts):
        return True
    if device.startswith("\\\\"):
        return True
    return False


def get_network(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        return {"network": {name: [addr._asdict() for addr in addrs] for name, addrs in psutil.net_if_addrs().items()}}
    except Exception as exc:  # noqa: BLE001 - psutil diagnostics are best-effort.
        return {"error": _safe_diagnostic_error(exc), "network": {}}


def get_battery(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        import psutil

        battery = psutil.sensors_battery()
        return {"battery": battery._asdict() if battery else None}
    except Exception as exc:  # noqa: BLE001 - psutil diagnostics are best-effort.
        return {"error": _safe_diagnostic_error(exc), "battery": None}


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
    except Exception as exc:  # noqa: BLE001 - startup scan is best-effort.
        logger.debug("startup registry scan failed: %s", exc, exc_info=True)

    return {"startup_items": items, "count": len(items)}


def open_settings_uri(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    uri = str(args.get("uri", "ms-settings:"))
    if not uri.startswith("ms-settings:"):
        return {"ok": False, "error": "Only ms-settings: URIs are allowed."}
    if args.get("dry_run", False):
        return {"ok": True, "dry_run": True, "uri": uri}
    if platform.system().lower() != "windows":
        return {"ok": False, "error": "Windows settings URIs are only supported on Windows."}
    raise_if_tool_aborted(context)
    os.startfile(uri)  # noqa: S606  # type: ignore[attr-defined]
    record("system.open_settings_uri", "ComputerAgent", {"uri": uri})
    return {"ok": True, "uri": uri, "opened": True}


def find_large_files(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    threshold_mb = float(args.get("threshold_mb") or 100)
    limit = int(args.get("limit") or 50)
    max_scanned = max(1, int(args.get("max_scanned") or 5000))
    threshold_bytes = int(threshold_mb * 1024 * 1024)

    allowed = [str(path) for path in context.get("allowed_directories") or []]
    raw_roots = args.get("roots") or allowed
    if isinstance(raw_roots, str):
        raw_roots = [raw_roots]
    if not raw_roots or not allowed:
        return {"files": [], "count": 0, "note": "No authorized roots configured."}

    results: list[dict[str, Any]] = []
    visited: set[str] = set()
    scanned = 0
    for raw in raw_roots:
        try:
            root_path = str(resolve_authorized(str(raw), allowed))
        except Exception:  # noqa: BLE001, S112 - unauthorized or malformed roots are skipped.
            continue
        if not os.path.isdir(root_path):
            continue
        for current, _dirs, files in os.walk(root_path):
            for name in files:
                scanned += 1
                if scanned > max_scanned:
                    break
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
            if scanned > max_scanned:
                break
    results.sort(key=lambda item: -int(item["size"]))
    return {
        "files": results[:limit],
        "count": len(results),
        "threshold_mb": threshold_mb,
        "scanned": scanned,
        "truncated": scanned > max_scanned,
    }


def cleanup_suggestions(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    immediate: list[dict[str, Any]] = []
    approval: list[dict[str, Any]] = []
    info_only: list[dict[str, Any]] = []

    temp_dir = get_env("TEMP") or os.path.expandvars(r"%TEMP%")
    if temp_dir and os.path.isdir(temp_dir):
        immediate.append(
            {
                "action": "clean_temp",
                "path": temp_dir,
                "detail": "Windows %TEMP% directory is safe to clean periodically.",
            }
        )

    cache_locations = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache"),
    ]
    for path in cache_locations:
        if path and os.path.isdir(path):
            approval.append(
                {
                    "action": "clear_browser_cache",
                    "path": path,
                    "detail": "Clearing browser cache is safe but requires user approval.",
                }
            )

    # Surface the top-N largest files inside authorized directories.
    large = find_large_files({"threshold_mb": float(args.get("threshold_mb") or 200), "limit": 8}, context)
    for file_info in large.get("files", [])[:8]:
        info_only.append(
            {
                "action": "review_large_file",
                "path": file_info["path"],
                "size_mb": file_info["size_mb"],
                "category": file_info["category"],
                "detail": "Large file in your workspace; review before deleting.",
            }
        )

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
    except Exception as exc:  # noqa: BLE001 - process diagnostics are best-effort.
        return {"error": _safe_diagnostic_error(exc), "processes": []}


def local_ai_status(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        from app.llm.local_provider import health_snapshot
        from app.llm.registry import get_effective_settings

        timeout = float(args.get("timeout") or 0.25)
        snapshot = health_snapshot(get_effective_settings(), timeout=max(0.05, min(timeout, 1.5)))
    except Exception as exc:  # noqa: BLE001 - diagnostics should report status, not fail the system check.
        return {
            "scope": "local_only",
            "available": False,
            "selected_backend_kind": "",
            "probe_order": [],
            "error": "Local AI readiness check failed.",
            "error_type": exc.__class__.__name__,
            "readiness": {"can_install": False, "recommended_model": "", "reason": ""},
            "onnx": {"available": False, "configured_model": False, "model_present": False},
        }
    return _safe_local_ai_status(snapshot)


def diagnostics(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    info = get_info(args, context)
    disks = get_disks(args, context)
    network = get_network(args, context)
    battery = get_battery(args, context)
    processes = get_processes({"limit": 8}, context)
    local_ai = _quick_local_ai_status()
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
        "local_ai": local_ai,
        "suggestions": suggestions,
    }


def _quick_local_ai_status() -> dict[str, Any]:
    configured = any(
        str(get_env(name) or "").strip()
        for name in (
            "LENGRVIS_ONNX_MODEL_PATH",
            "LENGRVIS_ONNX_MODELS_DIR",
            "OLLAMA_MODELS",
            "LENGRVIS_LLM_BASE_URL",
        )
    )
    try:
        from app.llm.local_provider import hardware_readiness, unavailable_message

        readiness = hardware_readiness()
        error = unavailable_message()
    except Exception as exc:  # noqa: BLE001 - diagnostics must remain read-only and fail closed.
        readiness = {"can_install": False, "recommended_model": "", "reason": ""}
        error = f"Local AI readiness summary failed: {exc.__class__.__name__}"
    readiness_payload = readiness if isinstance(readiness, dict) else {}
    return {
        "scope": "local_only",
        "available": False,
        "selected_backend_kind": "",
        "selected_model": "",
        "models_count": 0,
        "probe_order": [],
        "probe_mode": "summary_only",
        "configured": configured,
        "full_probe_deferred": True,
        "error": _safe_diagnostic_text(error),
        "readiness": {
            "can_install": bool(readiness_payload.get("can_install")),
            "recommended_model": _safe_model_label(readiness_payload.get("recommended_model")),
            "reason": _safe_diagnostic_text(readiness_payload.get("reason")),
        },
        "onnx": {"available": False, "configured_model": False, "model_present": False},
        "detail": "Full local model runtime checks run from Settings, not from the general system diagnostics refresh.",
    }


def _safe_local_ai_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    selected = snapshot.get("selected_backend")
    selected_backend = selected if isinstance(selected, dict) else {}
    onnx = snapshot.get("onnx")
    onnx_payload = onnx if isinstance(onnx, dict) else {}
    readiness = snapshot.get("readiness")
    readiness_payload = readiness if isinstance(readiness, dict) else {}
    selected_kind = str(selected_backend.get("kind") or snapshot.get("kind") or "")
    selected_model = _safe_model_label(selected_backend.get("model"))
    return {
        "scope": "local_only",
        "available": bool(snapshot.get("available")),
        "selected_backend_kind": selected_kind,
        "selected_model": selected_model,
        "models_count": _safe_count(snapshot.get("models") or selected_backend.get("models")),
        "probe_order": [str(item) for item in list(snapshot.get("probe_order") or [])],
        "error": _safe_diagnostic_text(snapshot.get("error")),
        "readiness": {
            "can_install": bool(readiness_payload.get("can_install")),
            "recommended_model": _safe_model_label(readiness_payload.get("recommended_model")),
            "reason": _safe_diagnostic_text(readiness_payload.get("reason")),
        },
        "onnx": {
            "available": bool(onnx_payload.get("available")),
            "configured_model": bool(onnx_payload.get("model_path")),
            "model_present": bool(onnx_payload.get("available")),
            "execution_provider": str(
                onnx_payload.get("provider")
                or onnx_payload.get("execution_provider")
                or onnx_payload.get("selected_provider")
                or ""
            ),
        },
    }


def _safe_count(value: Any) -> int:
    if isinstance(value, list | tuple | set):
        return len(value)
    return 0


def _safe_model_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text or "\\" in text or "/" in text:
        return ""
    return text[:120]


def _safe_diagnostic_text(value: Any, *, limit: int = 300) -> str:
    text = str(redact_value(str(value or "")) or "")
    text = LOCAL_AI_URL_RE.sub("[url]", text)
    text = LOCAL_AI_PATH_RE.sub("[local path]", text)
    return text[:limit]


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
        ("system.local_ai_status", local_ai_status, RiskLevel.R0_READ_ONLY),
        ("system.diagnostics", diagnostics, RiskLevel.R0_READ_ONLY),
    ]
    for name, fn, risk in defs:
        read_only = risk == RiskLevel.R0_READ_ONLY
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
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
