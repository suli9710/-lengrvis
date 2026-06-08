from __future__ import annotations

import json
import re
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.config import PROJECT_ROOT
from app.core import audit as audit_core, db
from app.llm.registry import LOCAL_PROVIDERS, get_effective_settings
from app.policy.redaction import contains_sensitive_key, redact_text
from app.services import mobile_pairing_service, ollama_service
from app.services import system_service


router = APIRouter()


SUPPORT_PACKAGE_REDACTED_FIELD = "[redacted:sensitive_field]"
SUPPORT_PACKAGE_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "device_id",
        "device_name",
        "grant_id",
        "model_path",
        "pairing_code",
        "pairing_id",
        "task_body",
        "task_goal",
        "task_prompt",
        "user_goal",
    }
)
SUPPORT_PACKAGE_INLINE_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b("
    r"device[_ -]?(?:id|name)"
    r"|grant[_ -]?id"
    r"|model[_ -]?path"
    r"|pairing[_ -]?(?:code|id)"
    r"|task[_ -]?(?:body|goal|prompt)"
    r"|user[_ -]?goal"
    r")\s*[:=]\s*['\"]?[^;,\r\n]+"
)
SUPPORT_PACKAGE_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>|,;]+"),
    re.compile(r"(?i)(?:/Users|/home)/[^\s\"'<>|,;]+"),
    re.compile(r"~[\\/][^\s\"'<>|,;]+"),
)


@router.get("/system/info")
def info():
    return system_service.info()


@router.get("/system/disks")
def disks():
    return system_service.disks()


@router.get("/system/network")
def network():
    return system_service.network()


@router.get("/system/diagnostics")
def diagnostics(request: Request):
    return _diagnostics_payload(request)


@router.post("/system/diagnostics/export")
def export_diagnostics(request: Request):
    settings = get_effective_settings()
    generated_at = datetime.now(UTC).isoformat()
    filename = f"lengrvis-diagnostics-{_safe_timestamp(generated_at)}.json"
    export_dir = Path(settings.data_dir) / "diagnostic-packages"
    export_dir.mkdir(parents=True, exist_ok=True)
    package_path = export_dir / filename
    package = {
        "schema_version": 1,
        "generated_at": generated_at,
        "diagnostic_scope": "local_only",
        "diagnostics": _diagnostics_export_payload(request),
    }
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "ok": True,
        "path": str(package_path),
        "filename": filename,
        "created_at": generated_at,
        "bytes": package_path.stat().st_size,
        "scope": "local_only",
    }


def _diagnostics_payload(request: Request) -> dict[str, Any]:
    payload = system_service.diagnostics()
    base = dict(payload) if isinstance(payload, dict) else {"diagnostics": payload}
    settings = get_effective_settings()
    metrics = db.local_product_diagnostics()
    product_metrics = dict(metrics.get("product_metrics") or {})
    product_funnel = dict(metrics.get("product_funnel") or {})
    local_model_metrics = _local_model_product_metrics(settings)
    product_metrics["local_model"] = local_model_metrics
    product_funnel["local_model"] = local_model_metrics
    verification = audit_core.verify_chain(limit=None)
    base.update(
        {
            "product": {
                "name": "Lengrvis",
                "version": str(getattr(request.app, "version", "") or ""),
            },
            "update_channel": _update_channel_status(),
            "local_paths": {
                "data_dir": str(Path(settings.data_dir)),
                "database": str(db.db_path()),
                "log_dirs": _log_dirs(settings.data_dir),
            },
            "audit": {
                "verification": verification,
                "latest_event": metrics.get("latest_audit_event"),
            },
            "lan_transport": _lan_transport_readiness(settings),
            "recent_counts": metrics.get("recent_counts", {}),
            "recent_success_counts": metrics.get("recent_success_counts", {}),
            "recent_failure_counts": metrics.get("recent_failure_counts", {}),
            "product_metrics": product_metrics,
            "product_funnel": product_funnel,
            "diagnostic_hints": _diagnostic_hints(verification, metrics),
            "diagnostic_scope": "local_only",
        }
    )
    return base


def _diagnostics_export_payload(request: Request) -> dict[str, Any]:
    payload = _diagnostics_payload(request)
    local_paths = payload.get("local_paths") if isinstance(payload.get("local_paths"), dict) else {}
    path_replacements = _support_package_path_replacements(payload)
    redacted = _sanitize_support_package_value(payload, path_replacements)
    export_payload = dict(redacted) if isinstance(redacted, dict) else {"diagnostics": redacted}
    export_payload["local_paths"] = _support_package_local_paths(local_paths)
    export_payload["support_package_redaction"] = {
        "local_paths": "redacted_to_path_labels",
        "process_usernames": "redacted_to_user_labels",
        "release_notes_path": "redacted_to_path_label_when_present",
        "full_local_paths_removed": True,
        "data_dir_path_label": "app_data_dir",
        "database_path_label": "app_database",
        "scope": "local_only",
    }
    return export_payload


@router.get("/system/processes")
def processes(limit: int = 25):
    return system_service.processes(limit)


@router.get("/system/startup-items")
def startup_items():
    return system_service.startup_items()


@router.post("/system/open-settings")
def open_settings(payload: dict):
    return system_service.open_settings(str(payload.get("uri", "ms-settings:")), bool(payload.get("dry_run", False)))


def _log_dirs(data_dir: str) -> list[str]:
    candidates = [PROJECT_ROOT / "logs", Path(data_dir) / "logs"]
    seen: set[str] = set()
    result: list[str] = []
    for path in candidates:
        text = str(path)
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _support_package_path_replacements(payload: dict[str, Any]) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    local_paths = payload.get("local_paths") if isinstance(payload.get("local_paths"), dict) else {}
    data_dir = str(local_paths.get("data_dir") or "").strip()
    database = str(local_paths.get("database") or "").strip()
    log_dirs = local_paths.get("log_dirs") if isinstance(local_paths.get("log_dirs"), list) else []
    if data_dir:
        replacements.extend(_path_variants(data_dir, "[path_label:app_data_dir]"))
    if database:
        replacements.extend(_path_variants(database, "[path_label:app_database]"))
    for index, raw_path in enumerate(log_dirs, start=1):
        text = str(raw_path or "").strip()
        if text:
            replacements.extend(_path_variants(text, f"[path_label:log_dir_{index}]"))
    update_channel = payload.get("update_channel") if isinstance(payload.get("update_channel"), dict) else {}
    release_notes = update_channel.get("release_notes") if isinstance(update_channel.get("release_notes"), dict) else {}
    release_notes_path = str(release_notes.get("path") or "").strip()
    if release_notes_path:
        replacements.extend(_path_variants(release_notes_path, "[path_label:release_notes]"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    return replacements


def _path_variants(path: str, label: str) -> list[tuple[str, str]]:
    variants = {path, path.replace("\\", "/"), path.replace("/", "\\")}
    return [(variant, label) for variant in variants if variant]


def _sanitize_support_package_value(value: Any, replacements: list[tuple[str, str]], *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            item_key: _sanitize_support_package_value(item, replacements, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_support_package_value(item, replacements, key=key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_support_package_value(item, replacements, key=key) for item in value]
    if isinstance(value, str):
        if key.replace("-", "_").casefold() in {"username", "user_name"} and value:
            return "[redacted:local_user]"
        if _support_package_sensitive_key(key) and value:
            return SUPPORT_PACKAGE_REDACTED_FIELD
        text = value
        for raw_path, label in replacements:
            text = text.replace(raw_path, label)
        text = redact_text(text)
        text = _redact_support_package_inline_sensitive_text(text)
        text = _redact_support_package_local_paths(text)
        return text
    return value


def _support_package_sensitive_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return contains_sensitive_key(normalized) or any(
        fragment in normalized for fragment in SUPPORT_PACKAGE_SENSITIVE_KEY_FRAGMENTS
    )


def _redact_support_package_inline_sensitive_text(text: str) -> str:
    return SUPPORT_PACKAGE_INLINE_SENSITIVE_PATTERN.sub(
        lambda match: f"{match.group(1)}=[redacted:sensitive_value]",
        text,
    )


def _redact_support_package_local_paths(text: str) -> str:
    redacted = text
    for pattern in SUPPORT_PACKAGE_LOCAL_PATH_PATTERNS:
        redacted = pattern.sub("[redacted:local_path]", redacted)
    return redacted


def _support_package_local_paths(local_paths: dict[str, Any]) -> dict[str, Any]:
    log_dirs = local_paths.get("log_dirs") if isinstance(local_paths.get("log_dirs"), list) else []
    return {
        "data_dir": {
            "path_label": "app_data_dir",
            "kind": "data_dir",
            "redacted": True,
        },
        "database": {
            "path_label": "app_database",
            "kind": "database",
            "filename": _safe_filename(local_paths.get("database"), fallback="lengrvis.db"),
            "parent_path_label": "app_data_dir",
            "redacted": True,
        },
        "log_dirs": [_support_package_log_dir(index) for index in range(1, len(log_dirs) + 1)],
    }


def _support_package_log_dir(index: int) -> dict[str, Any]:
    label = "project_logs" if index == 1 else "app_data_logs" if index == 2 else f"log_dir_{index}"
    return {
        "path_label": label,
        "replacement_label": f"log_dir_{index}",
        "kind": "log_dir",
        "redacted": True,
    }


def _safe_filename(raw_path: Any, *, fallback: str) -> str:
    try:
        name = Path(str(raw_path or "")).name
    except OSError:
        name = ""
    return name or fallback


def _safe_timestamp(value: str) -> str:
    return value.replace(":", "-").replace("+", "Z")


def _update_channel_status() -> dict[str, Any]:
    return {
        "configured": False,
        "status": "not_configured",
        "label": "未配置在线更新通道",
        "detail": "当前未配置在线更新通道，只显示本机版本与本地发布说明。",
        "check_action": "refresh_local_status",
        "offline_only": True,
        "user_action_label": "刷新本机状态",
        "release_notes": _release_notes_status(),
        "next_steps": [
            "确认是否有新版：查看本地发布说明或新的安装包说明。",
            "遇到故障：导出诊断包，再打开日志位置排查。",
        ],
    }


def _release_notes_status() -> dict[str, Any]:
    candidates = [
        PROJECT_ROOT / "RELEASE_NOTES.md",
        PROJECT_ROOT / "CHANGELOG.md",
        PROJECT_ROOT / "README.md",
    ]
    for path in candidates:
        if path.exists():
            return {
                "available": True,
                "label": "本地发布说明",
                "detail": "打开随安装包提供的说明文件；本页不会联网检查更新。",
                "path": str(path),
                "source": "local_file",
            }
    return {
        "available": False,
        "label": "发布说明",
        "detail": "当前安装包未附带可打开的本地发布说明，请以安装包来源说明为准。",
        "source": "not_packaged",
    }


def _lan_transport_readiness(settings: Any) -> dict[str, Any]:
    transport = dict(mobile_pairing_service.lan_transport_security(settings))
    self_signed = _certificate_self_signed(str(getattr(settings, "lan_tls_cert_file", "") or ""))
    transport["certificate_self_signed"] = self_signed
    transport["certificate_trust"] = "requires_client_trust" if transport.get("trust_required") else "not_required"
    if transport.get("https_enabled") and self_signed is True:
        transport["certificate_trust"] = "self_signed_requires_client_trust"
    elif transport.get("https_enabled") and self_signed is None:
        transport["certificate_trust"] = "requires_client_trust_unknown_issuer"
    return transport


def _local_model_product_metrics(settings: Any) -> dict[str, Any]:
    readiness: dict[str, Any] = {}
    try:
        readiness = ollama_service.hardware_readiness()
    except Exception as exc:  # noqa: BLE001 - diagnostics should degrade to evidence, not fail.
        readiness = {"can_install": False, "recommended_model": "", "error_type": exc.__class__.__name__}

    installed = _safe_bool_call(ollama_service.is_installed)
    bundled_runtime_available = _safe_bool_call(ollama_service.bundled_runtime_available)
    runtime_present = installed or bundled_runtime_available
    onnx_evidence = {
        "enabled": bool(getattr(settings, "onnx_enabled", False)),
        "llm_model": _configured_path_evidence(getattr(settings, "onnx_model_path", "")),
        "text_embedding_model": _configured_path_evidence(getattr(settings, "onnx_embedding_model_path", "")),
        "image_embedding_model": _configured_path_evidence(getattr(settings, "onnx_image_embedding_model_path", "")),
        "ocr_model": _configured_path_evidence(getattr(settings, "ocr_openvino_model_dir", "")),
    }
    return {
        "schema_version": 1,
        "mode": str(getattr(settings, "mode", "") or ""),
        "provider_kind": _provider_kind(settings),
        "privacy_mode_requested": str(getattr(settings, "mode", "") or "").casefold() == "privacy",
        "ollama": {
            "runtime_present": runtime_present,
            "installed": installed,
            "bundled_runtime_available": bundled_runtime_available,
            "hardware_can_install": bool(readiness.get("can_install")),
            "recommended_model": str(readiness.get("recommended_model") or ""),
            "next_action": _local_model_next_action(readiness, runtime_present, onnx_evidence),
            "readiness_error_type": str(readiness.get("error_type") or ""),
        },
        "onnx": onnx_evidence,
    }


def _provider_kind(settings: Any) -> str:
    provider_name = str(getattr(settings, "provider_name", "") or "").casefold()
    if provider_name in LOCAL_PROVIDERS:
        return "local"
    if provider_name == "mock":
        return "mock"
    return "cloud_or_remote"


def _configured_path_evidence(value: Any) -> dict[str, bool]:
    text = str(value or "").strip()
    if not text:
        return {"configured": False, "present": False}
    try:
        present = Path(text).expanduser().exists()
    except OSError:
        present = False
    return {"configured": True, "present": bool(present)}


def _safe_bool_call(func: Any) -> bool:
    try:
        return bool(func())
    except Exception:  # noqa: BLE001 - local diagnostics are best-effort.
        return False


def _local_model_next_action(readiness: dict[str, Any], runtime_present: bool, onnx_evidence: dict[str, Any]) -> str:
    if any(item.get("present") for item in onnx_evidence.values() if isinstance(item, dict)):
        return "ready"
    if not bool(readiness.get("can_install")):
        return "hardware_blocked"
    if not runtime_present:
        return "install_runtime"
    return "start_or_verify_model"


def _certificate_self_signed(cert_file: str) -> bool | None:
    if not cert_file:
        return None
    path = Path(cert_file).expanduser()
    if not path.exists():
        return None
    try:
        cert = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - diagnostics should report readiness, not fail.
        return None
    return cert.get("subject") == cert.get("issuer")


def _diagnostic_hints(verification: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not verification.get("ok", False):
        reason = str(verification.get("failure_reason") or "unknown")
        index = verification.get("failure_index")
        hints.append(f"Audit chain verification failed at event {index}: {reason}.")

    failure_counts = metrics.get("recent_failure_counts") if isinstance(metrics, dict) else {}
    if isinstance(failure_counts, dict):
        for key, value in failure_counts.items():
            if int(value or 0) > 0:
                hints.append(f"Recent {key.replace('_', ' ')}: {value}.")

    if not hints:
        hints.append("No local audit integrity failure or recent product failure detected.")
    return hints
