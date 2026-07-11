from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.core import audit as audit_core
from app.core import db
from app.llm.registry import get_effective_settings
from app.policy.redaction import contains_sensitive_key, redact_text
from app.services import (
    diagnostics_runtime_status,
    system_service,
    task_recording_service,
)

REDACTED_FIELD = "[redacted:sensitive_field]"
SENSITIVE_KEY_FRAGMENTS = frozenset(
    {
        "computer_name",
        "computer_id",
        "crash_report_id",
        "crash_id",
        "device_id",
        "device_name",
        "dump_file",
        "grant_id",
        "host_name",
        "hostname",
        "installation_id",
        "install_id",
        "machine_id",
        "machine_name",
        "minidump",
        "model_dir",
        "model_file",
        "model_path",
        "pairing_code",
        "pairing_id",
        "task_body",
        "task_goal",
        "task_prompt",
        "user_goal",
    }
)
LOCAL_USER_KEYS = frozenset(
    {
        "account_name",
        "account_username",
        "home_user",
        "local_user",
        "local_username",
        "login_name",
        "user_name",
        "username",
    }
)
IDENTIFIER_KEYS = frozenset({"code", "id", "name"})
IDENTIFIER_PARENT_CONTEXTS = frozenset(
    {
        "device",
        "devices",
        "grant",
        "grants",
        "mobile_device",
        "mobile_devices",
        "mobile_pairing",
        "mobile_pairings",
        "paired_device",
        "paired_devices",
        "pairing",
        "pairings",
        "remote_input_grant",
        "remote_input_grants",
    }
)
CONTENT_KEYS = frozenset({"body", "content", "goal", "message", "prompt", "text"})
CONTENT_PARENT_CONTEXTS = frozenset(
    {
        "approval",
        "approvals",
        "chat",
        "chats",
        "message",
        "messages",
        "prompt",
        "prompts",
        "run",
        "runs",
        "task",
        "tasks",
        "user_goal",
    }
)
TASK_RECORDING_CONTEXT_FRAGMENTS = (
    "task_recording",
    "recording",
    "screenshot",
    "screen_shot",
    "screen_capture",
)
TASK_RECORDING_ARTIFACT_FILENAME_PATTERN = re.compile(
    r"(?i)[^\\/\r\n\"'<>|,;]*(?:recording|screen[_ -]?shot)[^\\/\r\n\"'<>|,;]*\."
    r"(?:png|jpe?g|webp|gif|bmp|mp4|webm|mov|mkv|json|zip)\b"
)
TASK_RECORDING_STATUS_KEYS = frozenset(
    {"schema_version", "enabled", "default_policy", "local_only", "configuration", "export"}
)
TASK_RECORDING_EXPORT_STATUS_KEYS = frozenset(
    {"status_only", "contains_images", "contains_image_paths", "contains_recording_file_names"}
)
NETWORK_KEYS = frozenset({"network", "net_if_addrs", "network_interfaces"})
INLINE_SENSITIVE_PATTERN = re.compile(
    r"(?i)\b("
    r"computer[_ -]?name"
    r"|computer[_ -]?id"
    r"|crash[_ -]?(?:id|report[_ -]?id)"
    r"|device[_ -]?(?:id|name)"
    r"|dump[_ -]?(?:file|path)"
    r"|grant[_ -]?id"
    r"|host"
    r"|host[_ -]?name"
    r"|hostname"
    r"|installation[_ -]?id"
    r"|install[_ -]?id"
    r"|machine[_ -]?(?:id|name)"
    r"|minidump"
    r"|model[_ -]?(?:dir|file|path)"
    r"|pairing[_ -]?(?:code|id)"
    r"|task[_ -]?(?:body|goal|prompt)"
    r"|user[_ -]?goal"
    r")\s*[:=]\s*['\"]?[^;,\r\n]+"
)
LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)\\\\[^\\/\r\n\"'<>|,;]+\\[^\\/\r\n\"'<>|,;]+(?:\\[^\\/\r\n\"'<>|,;]+)*"),
    re.compile(r"(?i)\b[A-Z]:[\\/](?:[^\\/\r\n\"'<>|,;]*[\\/])*[^\\/\r\n\"'<>|,;]*"),
    re.compile(r"(?i)(?:/Users|/home|/tmp|/var|/private)/(?:[^/\r\n\"'<>|,;]+/)*[^/\r\n\"'<>|,;]*"),
    re.compile(r"~[\\/](?:[^\\/\r\n\"'<>|,;]*[\\/])*[^\\/\r\n\"'<>|,;]*"),
    re.compile(
        r"(?i)%(?:USERPROFILE|LOCALAPPDATA|APPDATA|TEMP|TMP|PROGRAMDATA|PUBLIC)%[\\/]"
        r"(?:[^\\/\r\n\"'<>|,;]*[\\/])*[^\\/\r\n\"'<>|,;]*"
    ),
    re.compile(r"(?i)\$(?:HOME|TMPDIR|TEMP|TMP)[\\/](?:[^\\/\r\n\"'<>|,;]*[\\/])*[^\\/\r\n\"'<>|,;]*"),
)
PATH_LABEL_CHILD_PATTERN = re.compile(r"(\[path_label:[^\]]+\])(?:[\\/][^\\/\r\n\"'<>|,;]+)+")
DIAGNOSTICS_REVIEWED_ARTIFACT_TYPE = "diagnostics-external-review-evidence-reviewed"
DIAGNOSTICS_REVIEWED_ARTIFACT_VALIDATOR = "scripts/verify_diagnostics_external_reviewed_evidence.py"
DIAGNOSTICS_REVIEWED_ARTIFACT_REQUIRED_CHECKS = (
    "actual_exported_package_opened",
    "logs_reviewed",
    "path_labels_reviewed",
    "task_traces_reviewed",
    "model_traces_reviewed",
    "device_identifiers_reviewed",
    "credentials_and_secrets_reviewed",
    "redaction_reviewed",
    "external_sharing_decision_recorded",
)


def diagnostics_response(*, app_version: str) -> dict[str, Any]:
    return sanitize_diagnostics_response(_diagnostics_base(app_version=app_version))


def write_diagnostics_export(*, app_version: str) -> dict[str, Any]:
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
        "diagnostics": diagnostics_export_payload(diagnostics_response(app_version=app_version)),
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


def _diagnostics_base(*, app_version: str) -> dict[str, Any]:
    payload = system_service.diagnostics()
    base = dict(payload) if isinstance(payload, dict) else {"diagnostics": payload}
    settings = get_effective_settings()
    metrics = db.local_product_diagnostics()
    product_metrics = dict(metrics.get("product_metrics") or {})
    product_funnel = dict(metrics.get("product_funnel") or {})
    local_model_metrics = diagnostics_runtime_status.local_model_product_metrics(settings)
    product_metrics["local_model"] = local_model_metrics
    product_funnel["local_model"] = local_model_metrics
    verification = audit_core.verify_chain(limit=None)
    local_paths = {
        "data_dir": str(Path(settings.data_dir)),
        "database": str(db.db_path()),
        "log_dirs": _log_dirs(settings.data_dir),
    }
    base.update(
        {
            "product": {
                "name": "Lengrvis",
                "version": app_version,
            },
            "update_channel": _update_channel_status(),
            "local_paths": local_paths,
            "audit": {
                "verification": verification,
                "latest_event": metrics.get("latest_audit_event"),
            },
            "lan_transport": diagnostics_runtime_status.lan_transport_readiness(settings),
            "task_recording": diagnostics_runtime_status.task_recording_privacy_status(),
            "recent_counts": metrics.get("recent_counts", {}),
            "recent_success_counts": metrics.get("recent_success_counts", {}),
            "recent_failure_counts": metrics.get("recent_failure_counts", {}),
            "product_metrics": product_metrics,
            "product_funnel": product_funnel,
            "diagnostic_hints": _diagnostic_hints(verification, metrics),
            "diagnostic_scope": "local_only",
            "support_package_redaction": redaction_guidance(
                local_paths,
                current_response_contains_local_paths=True,
            ),
        }
    )
    return base


def sanitize_diagnostics_response(base: dict[str, Any]) -> dict[str, Any]:
    local_paths = base.get("local_paths")
    lan_transport = base.get("lan_transport")
    redaction = base.get("support_package_redaction")
    replacements = path_replacements(base)
    sanitized = sanitize_value(base, replacements)
    result = dict(sanitized) if isinstance(sanitized, dict) else {"diagnostics": sanitized}
    if isinstance(local_paths, dict):
        result["local_paths"] = local_paths
    if isinstance(lan_transport, dict):
        result["lan_transport"] = lan_transport
    if isinstance(redaction, dict):
        result["support_package_redaction"] = redaction
    return result


def diagnostics_export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    local_paths = payload.get("local_paths") if isinstance(payload.get("local_paths"), dict) else {}
    replacements = path_replacements(payload)
    redacted = sanitize_value(payload, replacements)
    export_payload = dict(redacted) if isinstance(redacted, dict) else {"diagnostics": redacted}
    normalize_update_channel(export_payload)
    export_payload["local_paths"] = redacted_local_paths(local_paths)
    export_payload["support_package_redaction"] = redaction_guidance(
        local_paths,
        current_response_contains_local_paths=False,
    )
    return export_payload


def path_replacements(payload: dict[str, Any]) -> list[tuple[str, str]]:
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
            replacements.extend(_path_variants(text, f"[path_label:{log_dir_label(index)}]"))
    update_channel = payload.get("update_channel") if isinstance(payload.get("update_channel"), dict) else {}
    release_notes = update_channel.get("release_notes") if isinstance(update_channel.get("release_notes"), dict) else {}
    release_notes_path = str(release_notes.get("path") or "").strip()
    if release_notes_path:
        replacements.extend(_path_variants(release_notes_path, "[path_label:release_notes]"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    return replacements


def sanitize_value(
    value: Any,
    replacements: list[tuple[str, str]],
    *,
    key: str = "",
    key_path: tuple[str, ...] = (),
) -> Any:
    if _task_recording_status_value(value):
        return value
    if _network_status_value(value):
        return value
    if _task_recording_artifact_context(value, key, key_path):
        return _task_recording_status_only(value)
    if _network_context(value, key, key_path):
        return _network_status_only(value)
    if isinstance(value, dict):
        return {
            item_key: sanitize_value(
                item,
                replacements,
                key=str(item_key),
                key_path=(*key_path, str(item_key)),
            )
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item, replacements, key=key, key_path=key_path) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item, replacements, key=key, key_path=key_path) for item in value]
    if isinstance(value, str):
        if _task_recording_artifact_text(value):
            return "[redacted:task_recording_artifact]"
        if _local_user_key(key) and value:
            return "[redacted:local_user]"
        if _sensitive_key(key, key_path) and value:
            return REDACTED_FIELD
        text = value
        for raw_path, label in replacements:
            text = text.replace(raw_path, label)
        text = _redact_labeled_child_paths(text)
        text = redact_text(text)
        text = _redact_inline_sensitive_text(text)
        text = _redact_local_paths(text)
        return text
    return value


def redacted_local_paths(local_paths: dict[str, Any]) -> dict[str, Any]:
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
        "log_dirs": [_log_dir(index) for index in range(1, len(log_dirs) + 1)],
    }


def normalize_update_channel(export_payload: dict[str, Any]) -> None:
    update_channel = export_payload.get("update_channel")
    if not isinstance(update_channel, dict):
        return
    release_notes = update_channel.get("release_notes")
    if not isinstance(release_notes, dict):
        return
    if release_notes.get("path"):
        release_notes["path"] = "[path_label:release_notes]"
        release_notes["path_label"] = "release_notes"
        release_notes["path_redacted"] = True


def redaction_guidance(
    local_paths: dict[str, Any],
    *,
    current_response_contains_local_paths: bool,
) -> dict[str, Any]:
    log_dirs = local_paths.get("log_dirs") if isinstance(local_paths.get("log_dirs"), list) else []
    external_review = external_review_metadata()
    response_kind = (
        "diagnostics_get_response" if current_response_contains_local_paths else "diagnostics_export_payload"
    )
    local_path_state = "full_local_paths_present" if current_response_contains_local_paths else "path_labels_only"
    return {
        "schema_version": 1,
        "applies_to": "diagnostics_export_payload",
        "scope": "local_only",
        "intended_audience": "trusted_support",
        "public_safe": False,
        "fail_closed": True,
        "review_status": external_review["status"],
        "review_required": True,
        "review_before_external_sharing": True,
        "external_sharing_allowed": False,
        "current_response": {
            "public_safe": False,
            "contains_local_paths": current_response_contains_local_paths,
            "external_review_required": True,
        },
        "current_response_contract": {
            "schema_version": 1,
            "response_kind": response_kind,
            "public_safe": False,
            "contains_local_paths": current_response_contains_local_paths,
            "local_path_state": local_path_state,
            "review_status": external_review["status"],
            "review_required": True,
            "external_sharing_allowed": False,
            "machine_decision": "block_external_sharing_until_manual_review",
        },
        "checklist_summary": external_review["checklist_summary"],
        "reviewed_artifact_contract": diagnostics_reviewed_artifact_contract(),
        "local_paths": "redacted_to_path_labels",
        "local_path_labels": {
            "data_dir": "app_data_dir",
            "database": "app_database",
            "release_notes": "release_notes",
            "log_dirs": [log_dir_label(index) for index in range(1, len(log_dirs) + 1)],
        },
        "process_usernames": "redacted_to_user_labels",
        "device_names_and_ids": "redacted",
        "grant_and_pairing_identifiers": "redacted",
        "task_content": "redacted",
        "tokens_and_credentials": "redacted",
        "model_paths": "redacted",
        "task_recording": "status_only_no_images_or_file_names",
        "release_notes_path": "redacted_to_path_label_when_present",
        "full_local_paths_removed": not current_response_contains_local_paths,
        "export_full_local_paths_removed": True,
        "external_review": external_review,
        "guidance": (
            "优先发送这个脱敏 JSON 支持包，不要直接发送原始日志；原始日志、截图和任务记录仍可能包含私人内容，"
            "外发前需要单独检查。"
        ),
    }


def external_review_metadata() -> dict[str, Any]:
    checklist = [
        {
            "id": "scope_and_audience",
            "label": "Confirm local-only trusted-support scope",
            "status": "requires_reviewer_confirmation",
            "required": True,
        },
        {
            "id": "raw_logs_and_artifacts",
            "label": "Confirm no raw logs, screenshots, or recording files are attached",
            "status": "export_metadata_only",
            "required": True,
        },
        {
            "id": "local_paths",
            "label": "Confirm local paths are labels only",
            "status": "automated_redaction_applied",
            "required": True,
        },
        {
            "id": "secrets_and_identifiers",
            "label": "Confirm tokens, credentials, device, grant, and pairing identifiers are redacted",
            "status": "automated_redaction_applied",
            "required": True,
        },
        {
            "id": "task_content",
            "label": "Confirm task prompts, goals, messages, and approvals are redacted",
            "status": "automated_redaction_applied",
            "required": True,
        },
        {
            "id": "external_sharing_decision",
            "label": "Record a human decision before external sharing",
            "status": "pending",
            "required": True,
        },
    ]
    return {
        "schema_version": 1,
        "status": "manual_review_required",
        "review_status": "manual_review_required",
        "review_required": True,
        "required_before_external_sharing": True,
        "public_safe": False,
        "external_sharing_allowed": False,
        "fail_closed": True,
        "machine_decision": "block_external_sharing_until_manual_review",
        "reviewed_artifact_contract": diagnostics_reviewed_artifact_contract(),
        "checklist_summary": _external_review_checklist_summary(checklist),
        "checklist": checklist,
    }


def diagnostics_reviewed_artifact_contract() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_type": DIAGNOSTICS_REVIEWED_ARTIFACT_TYPE,
        "validator": DIAGNOSTICS_REVIEWED_ARTIFACT_VALIDATOR,
        "actual_package_content_review_required": True,
        "template_helper_can_satisfy": False,
        "allowed_review_decisions": ["do_not_share", "support_only"],
        "required_checks": list(DIAGNOSTICS_REVIEWED_ARTIFACT_REQUIRED_CHECKS),
        "required_summary": {
            "diagnostics_review_pass": True,
            "public_safe": False,
            "external_sharing_allowed": False,
            "rc_signoff": False,
            "release_signoff": False,
        },
        "machine_gate": {
            "strict_pipeline_stage": "diagnostics-evidence",
            "rr_id": "RR-P0-005",
        },
    }


def log_dir_label(index: int) -> str:
    return "project_logs" if index == 1 else "app_data_logs" if index == 2 else f"log_dir_{index}"


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


def _safe_timestamp(value: str) -> str:
    return value.replace(":", "-").replace("+", "Z")


def _update_channel_status() -> dict[str, Any]:
    release_notes = _release_notes_status()
    return {
        "schema_version": 1,
        "configured": False,
        "status": "not_configured",
        "label": "未配置在线更新通道",
        "detail": "当前未配置在线更新通道，只显示本机版本与本地发布说明。",
        "check_action": "refresh_local_status",
        "offline_only": True,
        "network_check_performed": False,
        "auto_update_claim": "not_configured",
        "crash_pipeline_claim": "not_reported",
        "user_action_label": "刷新本机状态",
        "release_notes": release_notes,
        "evidence": {
            "update_channel_configured": False,
            "network_update_check_performed": False,
            "release_notes_available": bool(release_notes.get("available")),
            "release_notes_source": str(release_notes.get("source") or ""),
            "auto_update_pipeline": "not_configured",
            "crash_pipeline": "not_reported",
        },
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
                "filename": path.name,
                "path": str(path),
                "path_kind": "local_file",
                "source": "local_file",
            }
    return {
        "available": False,
        "label": "发布说明",
        "detail": "当前安装包未附带可打开的本地发布说明，请以安装包来源说明为准。",
        "source": "not_packaged",
    }


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


def _path_variants(path: str, label: str) -> list[tuple[str, str]]:
    variants = {path, path.replace("\\", "/"), path.replace("/", "\\")}
    return [(variant, label) for variant in variants if variant]


def _network_context(value: Any, key: str, key_path: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    normalized = _normalized_key(key)
    if normalized in NETWORK_KEYS:
        return True
    normalized_path = tuple(_normalized_key(item) for item in key_path)
    return any(item in NETWORK_KEYS for item in normalized_path)


def _network_status_value(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value).issubset({"redacted", "status_only", "interface_count", "address_count"})
        and value.get("redacted") is True
        and value.get("status_only") is True
    )


def _network_status_only(value: Any) -> dict[str, Any]:
    interface_count = len(value) if isinstance(value, dict) else 0
    address_count = 0
    if isinstance(value, dict):
        for addresses in value.values():
            if isinstance(addresses, list | tuple | set):
                address_count += len(addresses)
            elif addresses:
                address_count += 1
    return {
        "redacted": True,
        "status_only": True,
        "interface_count": interface_count,
        "address_count": address_count,
    }


def _task_recording_artifact_context(value: Any, key: str, key_path: tuple[str, ...]) -> bool:
    if _task_recording_status_value(value):
        return False
    normalized = _normalized_key(key)
    if normalized in TASK_RECORDING_EXPORT_STATUS_KEYS:
        return False
    if any(fragment in normalized for fragment in TASK_RECORDING_CONTEXT_FRAGMENTS):
        return True
    normalized_path = tuple(_normalized_key(item) for item in key_path)
    if any(fragment in path_item for path_item in normalized_path for fragment in TASK_RECORDING_CONTEXT_FRAGMENTS):
        return True
    if not isinstance(value, dict):
        return False
    if str(value.get("kind") or "").casefold() == task_recording_service.RECORDING_KIND:
        return True
    keys = {_normalized_key(item) for item in value}
    if "recording_id" in keys:
        return True
    image_keys = {"image", "image_base64", "screenshot", "screenshot_base64"}
    location_keys = {"file_name", "filename", "path", "url"}
    if keys & image_keys and keys & location_keys:
        return True
    return any(
        _task_recording_artifact_text(str(value.get(item) or ""))
        for item in ("path", "url", "file_name", "filename")
    )


def _task_recording_artifact_text(value: str) -> bool:
    normalized = value.replace("\\", "/").casefold()
    return (
        "/recordings/" in normalized
        or "task_recordings/" in normalized
        or bool(TASK_RECORDING_ARTIFACT_FILENAME_PATTERN.search(value))
    )


def _task_recording_status_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    export = value.get("export")
    return (
        set(value).issubset(TASK_RECORDING_STATUS_KEYS)
        and value.get("default_policy")
        == {
            "mode": "opt_in",
            "enabled_by_default": False,
            "scope": "local_only",
        }
        and value.get("local_only") is True
        and isinstance(export, dict)
        and export.get("status_only") is True
        and export.get("contains_images") is False
        and export.get("contains_image_paths") is False
        and export.get("contains_recording_file_names") is False
    )


def _task_recording_status_only(value: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "redacted": True,
        "status_only": True,
        "contains_images": False,
        "contains_image_paths": False,
        "contains_recording_file_names": False,
    }
    if isinstance(value, dict) and "enabled" in value:
        status["enabled"] = bool(value.get("enabled"))
    if isinstance(value, dict) and "ok" in value:
        status["ok"] = bool(value.get("ok"))
    return status


def _local_user_key(key: str) -> bool:
    return _normalized_key(key) in LOCAL_USER_KEYS


def _sensitive_key(key: str, key_path: tuple[str, ...] = ()) -> bool:
    normalized = _normalized_key(key)
    normalized_path = tuple(_normalized_key(item) for item in key_path)
    if _host_key(normalized):
        return True
    if normalized in IDENTIFIER_KEYS and _path_has_context(normalized_path, IDENTIFIER_PARENT_CONTEXTS):
        return True
    if normalized in CONTENT_KEYS and _path_has_context(normalized_path, CONTENT_PARENT_CONTEXTS):
        return True
    return contains_sensitive_key(normalized) or any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _path_has_context(key_path: tuple[str, ...], contexts: frozenset[str]) -> bool:
    return any(item in contexts for item in key_path[:-1])


def _host_key(normalized: str) -> bool:
    return (
        normalized == "host"
        or normalized.startswith("host_")
        or normalized.endswith("_host")
        or normalized.endswith("_host_name")
        or normalized.endswith("_hostname")
    )


def _normalized_key(key: str) -> str:
    text = str(key).replace("-", "_").replace(" ", "_")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    return re.sub(r"_+", "_", text).casefold()


def _redact_inline_sensitive_text(text: str) -> str:
    return INLINE_SENSITIVE_PATTERN.sub(
        lambda match: f"{match.group(1)}=[redacted:sensitive_value]",
        text,
    )


def _redact_local_paths(text: str) -> str:
    redacted = text
    for pattern in LOCAL_PATH_PATTERNS:
        redacted = pattern.sub("[redacted:local_path]", redacted)
    return redacted


def _redact_labeled_child_paths(text: str) -> str:
    return PATH_LABEL_CHILD_PATTERN.sub(r"\1/[redacted:relative_path]", text)


def _log_dir(index: int) -> dict[str, Any]:
    label = log_dir_label(index)
    return {
        "path_label": label,
        "replacement_label": label,
        "kind": "log_dir",
        "source": "project_root" if index == 1 else "app_data_dir" if index == 2 else "additional",
        "redacted": True,
    }


def _external_review_checklist_summary(checklist: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    required_pending_statuses = {"pending", "requires_reviewer_confirmation"}
    required = 0
    required_pending = 0
    all_required_have_status = True
    for item in checklist:
        status = str(item.get("status") or "")
        if status:
            status_counts[status] = status_counts.get(status, 0) + 1
        if item.get("required") is True:
            required += 1
            if not status:
                all_required_have_status = False
            if status in required_pending_statuses:
                required_pending += 1
    return {
        "total": len(checklist),
        "required": required,
        "required_pending": required_pending,
        "all_required_have_status": all_required_have_status,
        "status_counts": status_counts,
    }


def _safe_filename(raw_path: Any, *, fallback: str) -> str:
    try:
        name = Path(str(raw_path or "")).name
    except OSError:
        name = ""
    return name or fallback
