from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

from app.config import env_raw
from app.llm.registry import LOCAL_PROVIDERS
from app.services import mobile_pairing_service, ollama_service, task_recording_service


def lan_transport_readiness(settings: Any) -> dict[str, Any]:
    transport = dict(mobile_pairing_service.lan_transport_security(settings))
    self_signed = certificate_self_signed(str(getattr(settings, "lan_tls_cert_file", "") or ""))
    transport["certificate_self_signed"] = self_signed
    transport["certificate_trust"] = "requires_client_trust" if transport.get("trust_required") else "not_required"
    if transport.get("https_enabled") and self_signed is True:
        transport["certificate_trust"] = "self_signed_requires_client_trust"
    elif transport.get("https_enabled") and self_signed is None:
        transport["certificate_trust"] = "requires_client_trust_unknown_issuer"
    return transport


def local_model_product_metrics(settings: Any) -> dict[str, Any]:
    readiness: dict[str, Any] = {}
    try:
        readiness = ollama_service.hardware_readiness()
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: native probe failures become redacted safe evidence.
        readiness = {"can_install": False, "recommended_model": "", "error_type": exc.__class__.__name__}

    installed = safe_bool_call(ollama_service.is_installed)
    bundled_runtime_available = safe_bool_call(ollama_service.bundled_runtime_available)
    runtime_present = installed or bundled_runtime_available
    onnx_evidence = {
        "enabled": bool(getattr(settings, "onnx_enabled", False)),
        "llm_model": configured_path_evidence(getattr(settings, "onnx_model_path", "")),
        "text_embedding_model": configured_path_evidence(getattr(settings, "onnx_embedding_model_path", "")),
        "image_embedding_model": configured_path_evidence(getattr(settings, "onnx_image_embedding_model_path", "")),
        "ocr_model": configured_path_evidence(getattr(settings, "ocr_openvino_model_dir", "")),
    }
    return {
        "schema_version": 1,
        "mode": str(getattr(settings, "mode", "") or ""),
        "provider_kind": provider_kind(settings),
        "privacy_mode_requested": str(getattr(settings, "mode", "") or "").casefold() == "privacy",
        "ollama": {
            "runtime_present": runtime_present,
            "installed": installed,
            "bundled_runtime_available": bundled_runtime_available,
            "hardware_can_install": bool(readiness.get("can_install")),
            "recommended_model": str(readiness.get("recommended_model") or ""),
            "next_action": local_model_next_action(readiness, runtime_present, onnx_evidence),
            "readiness_error_type": str(readiness.get("error_type") or ""),
        },
        "onnx": onnx_evidence,
    }


def provider_kind(settings: Any) -> str:
    provider_name = str(getattr(settings, "provider_name", "") or "").casefold()
    if provider_name in LOCAL_PROVIDERS:
        return "local"
    if provider_name == "mock":
        return "mock"
    return "cloud_or_remote"


def configured_path_evidence(value: Any) -> dict[str, bool]:
    text = str(value or "").strip()
    if not text:
        return {"configured": False, "present": False}
    try:
        present = Path(text).expanduser().exists()
    except OSError:
        present = False
    return {"configured": True, "present": bool(present)}


def safe_bool_call(func: Any) -> bool:
    try:
        return bool(func())
    except Exception:  # noqa: BLE001 - broad-exception-boundary: optional runtime probes fail closed to False.
        return False


def local_model_next_action(readiness: dict[str, Any], runtime_present: bool, onnx_evidence: dict[str, Any]) -> str:
    if any(item.get("present") for item in onnx_evidence.values() if isinstance(item, dict)):
        return "ready"
    if not bool(readiness.get("can_install")):
        return "hardware_blocked"
    if not runtime_present:
        return "install_runtime"
    return "start_or_verify_model"


def task_recording_privacy_status() -> dict[str, Any]:
    env_override = task_recording_env_override()
    return {
        "schema_version": 1,
        "enabled": safe_bool_call(task_recording_service.recording_enabled),
        "default_policy": {
            "mode": "opt_in",
            "enabled_by_default": False,
            "scope": "local_only",
        },
        "local_only": True,
        "configuration": {
            "env_override": env_override,
            "explicit_opt_in": env_override == "enabled",
        },
        "export": {
            "status_only": True,
            "contains_images": False,
            "contains_image_paths": False,
            "contains_recording_file_names": False,
        },
    }


def task_recording_env_override() -> str:
    raw = env_raw("LENGRVIS_TASK_RECORDING_ENABLED")
    if raw is None:
        return "unset"
    if raw.strip().casefold() in {"1", "true", "yes", "on"}:
        return "enabled"
    return "disabled"


def certificate_self_signed(cert_file: str) -> bool | None:
    if not cert_file:
        return None
    path = Path(cert_file).expanduser()
    if not path.exists():
        return None
    try:
        cert = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - broad-exception-boundary: certificate decode failures remain unknown and require trust.
        return None
    return cert.get("subject") == cert.get("issuer")
