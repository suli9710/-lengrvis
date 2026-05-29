from __future__ import annotations

import importlib
import importlib.metadata
import os
import platform
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from app.config import AppSettings


WINML_PROVIDER = "WindowsMLExecutionProvider"
DIRECTML_PROVIDER = "DmlExecutionProvider"
OPENVINO_PROVIDER = "OpenVINOExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"

PREFERRED_EXECUTION_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("winml", WINML_PROVIDER),
    ("directml", DIRECTML_PROVIDER),
    ("openvino", OPENVINO_PROVIDER),
    ("cpu", CPU_PROVIDER),
)

EXECUTION_PROVIDER_ALIASES = {
    "auto": "",
    "winml": WINML_PROVIDER,
    "windowsml": WINML_PROVIDER,
    "windows_ml": WINML_PROVIDER,
    "windowsmachinelearning": WINML_PROVIDER,
    "winml_execution_provider": WINML_PROVIDER,
    "windowsml_execution_provider": WINML_PROVIDER,
    "windows_ml_execution_provider": WINML_PROVIDER,
    "directml": DIRECTML_PROVIDER,
    "dml": DIRECTML_PROVIDER,
    "dml_execution_provider": DIRECTML_PROVIDER,
    "openvino": OPENVINO_PROVIDER,
    "openvino_execution_provider": OPENVINO_PROVIDER,
    "cpu": CPU_PROVIDER,
    "cpu_execution_provider": CPU_PROVIDER,
}

RUNTIME_MODULES = (
    "onnxruntime_windowsml",
    "onnxruntime_directml",
    "onnxruntime",
    "openvino",
)

_SESSION_CACHE: dict[str, Any] = {}
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_GLOBAL_LOCK = threading.Lock()


class OnnxAccelerationUnavailable(RuntimeError):
    """Raised when an optional ONNX acceleration path cannot run."""


@dataclass(slots=True)
class OnnxSessionBackend:
    kind: str
    model_path: str
    execution_provider: str
    available_providers: list[str]
    provider_options: dict[str, str] = field(default_factory=dict)
    runtime_package: str = ""
    model_id: str = ""

    def cache_key(self) -> str:
        return "|".join(
            [
                self.model_path,
                self.execution_provider,
                ",".join(self.available_providers),
                str(sorted(self.provider_options.items())),
            ]
        )


def runtime_packages_snapshot() -> dict[str, dict[str, Any]]:
    return {module_name: _runtime_package_snapshot(module_name) for module_name in RUNTIME_MODULES}


def winml_snapshot(
    runtime_packages: dict[str, dict[str, Any]] | None = None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    packages = runtime_packages or runtime_packages_snapshot()
    provider_list = providers if providers is not None else available_execution_providers()
    available_packages = [
        name for name in ("onnxruntime_windowsml", "onnxruntime_genai_winml") if packages.get(name, {}).get("available")
    ]
    package_errors = {
        name: packages.get(name, {}).get("error", "")
        for name in ("onnxruntime_windowsml", "onnxruntime_genai_winml")
        if packages.get(name, {}).get("error")
    }
    return {
        "available": bool(available_packages or WINML_PROVIDER in provider_list),
        "provider": WINML_PROVIDER,
        "provider_available": WINML_PROVIDER in provider_list or bool(available_packages),
        "os": platform.platform(),
        "build": platform.version(),
        "windows_app_sdk": _module_status("winrt.windows.applicationmodel.dynamicdependency")
        if os.name == "nt"
        else {"available": False, "module": "winrt.windows.applicationmodel.dynamicdependency", "error": "not Windows"},
        "registered_eps": [provider for provider in provider_list if provider == WINML_PROVIDER],
        "packages": available_packages,
        "errors": package_errors,
    }


def available_execution_providers() -> list[str]:
    ort = import_onnxruntime()
    if ort is None:
        return []
    try:
        return [str(provider) for provider in ort.get_available_providers()]
    except Exception:
        return []


def import_onnxruntime() -> Any | None:
    for module_name in ("onnxruntime_windowsml", "onnxruntime_directml", "onnxruntime"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
        except Exception:
            continue
    return None


def detect_session_backend(
    *,
    model_path: str,
    configured_provider: str = "",
    provider_preference: str = "",
    settings: AppSettings | None = None,
    model_id: str = "",
) -> OnnxSessionBackend | None:
    candidate = resolve_onnx_model_path(model_path)
    if candidate is None:
        return None
    providers = available_execution_providers()
    if not providers:
        return None
    selected = select_execution_provider(
        providers,
        configured_provider=configured_provider,
        provider_preference=provider_preference,
        settings=settings,
    )
    if selected is None:
        return None
    kind, execution_provider = selected
    return OnnxSessionBackend(
        kind=f"onnx-{kind}",
        model_path=str(candidate),
        execution_provider=execution_provider,
        available_providers=providers,
        provider_options=provider_options(execution_provider, settings=settings),
        runtime_package=runtime_package_for_provider(execution_provider),
        model_id=model_id,
    )


def select_execution_provider(
    providers: list[str],
    *,
    configured_provider: str = "",
    provider_preference: str = "",
    settings: AppSettings | None = None,
) -> tuple[str, str] | None:
    configured = normalize_execution_provider(configured_provider)
    candidates: list[tuple[str, str]] = []
    if configured:
        candidates.append((kind_for_execution_provider(configured), configured))
    candidates.extend(
        (kind, provider)
        for kind, provider in preferred_execution_providers(provider_preference, settings=settings)
        if provider != configured
    )
    provider_names = set(providers)
    for kind, provider in candidates:
        if provider in provider_names:
            return kind, provider
    return None


def preferred_execution_providers(
    provider_preference: str = "",
    *,
    settings: AppSettings | None = None,
) -> list[tuple[str, str]]:
    raw = provider_preference.strip()
    if not raw and settings is not None:
        raw = str(getattr(settings, "onnx_provider_preference", "") or "").strip()
    if not raw:
        raw = (
            os.environ.get("MARVIS_ONNX_PROVIDER_PREFERENCE")
            or os.environ.get("MAVRIS_ONNX_PROVIDER_PREFERENCE")
            or ""
        )
    if not raw:
        return list(PREFERRED_EXECUTION_PROVIDERS)

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in raw.replace(";", ",").split(","):
        provider = normalize_execution_provider(token)
        if not provider or provider in seen:
            continue
        seen.add(provider)
        result.append((kind_for_execution_provider(provider), provider))
    for kind, provider in PREFERRED_EXECUTION_PROVIDERS:
        if provider not in seen:
            result.append((kind, provider))
    return result


def normalize_execution_provider(value: str | None) -> str:
    if not value:
        return ""
    stripped = str(value).strip()
    if not stripped:
        return ""
    if stripped.endswith("ExecutionProvider"):
        return stripped
    normalized = stripped.lower().replace("-", "_").replace(" ", "_")
    return EXECUTION_PROVIDER_ALIASES.get(normalized, "")


def kind_for_execution_provider(execution_provider: str) -> str:
    if execution_provider == WINML_PROVIDER:
        return "winml"
    if execution_provider == DIRECTML_PROVIDER:
        return "directml"
    if execution_provider == OPENVINO_PROVIDER:
        return "openvino"
    if execution_provider == CPU_PROVIDER:
        return "cpu"
    return execution_provider.lower().replace("executionprovider", "") or "onnx"


def provider_options(execution_provider: str, *, settings: AppSettings | None = None) -> dict[str, str]:
    if execution_provider == DIRECTML_PROVIDER:
        device_id = str(getattr(settings, "onnx_directml_device_id", "") or "").strip() if settings is not None else ""
        if not device_id:
            device_id = (
                os.environ.get("MARVIS_ONNX_DIRECTML_DEVICE_ID")
                or os.environ.get("MAVRIS_ONNX_DIRECTML_DEVICE_ID")
                or ""
            )
        return {"device_id": device_id.strip()} if device_id and device_id.strip() else {}
    if execution_provider == OPENVINO_PROVIDER:
        device = str(getattr(settings, "onnx_openvino_device", "") or "").strip() if settings is not None else ""
        cache_dir = str(getattr(settings, "onnx_openvino_cache_dir", "") or "").strip() if settings is not None else ""
        if not device:
            device = (
                os.environ.get("MARVIS_ONNX_OPENVINO_DEVICE")
                or os.environ.get("MAVRIS_ONNX_OPENVINO_DEVICE")
                or ""
            )
        if not cache_dir:
            cache_dir = (
                os.environ.get("MARVIS_ONNX_OPENVINO_CACHE_DIR")
                or os.environ.get("MAVRIS_ONNX_OPENVINO_CACHE_DIR")
                or ""
            )
        options: dict[str, str] = {}
        if device and device.strip():
            options["device_type"] = device.strip()
        if cache_dir and cache_dir.strip():
            options["cache_dir"] = cache_dir.strip()
        return options
    return {}


def runtime_package_for_provider(execution_provider: str) -> str:
    if execution_provider == WINML_PROVIDER and _module_available("onnxruntime_windowsml"):
        return "onnxruntime_windowsml"
    if execution_provider == DIRECTML_PROVIDER and _module_available("onnxruntime_directml"):
        return "onnxruntime_directml"
    if _module_available("onnxruntime"):
        return "onnxruntime"
    return ""


def resolve_onnx_model_path(raw: str | Path | None) -> Path | None:
    if raw is None:
        return None
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError:
        return None
    if path.is_file() and path.suffix.lower() in {".onnx", ".ort"}:
        return path
    if path.is_dir():
        for name in ("model.onnx", "embedding.onnx", "vision_model.onnx", "encoder_model.onnx", "det_model.onnx"):
            candidate = path / name
            if candidate.is_file():
                return candidate
        candidates = sorted(candidate for candidate in path.rglob("*.onnx") if candidate.is_file())
        return candidates[0] if candidates else None
    return None


def create_inference_session(backend: OnnxSessionBackend) -> Any:
    key = backend.cache_key()
    if key in _SESSION_CACHE:
        return _SESSION_CACHE[key]
    lock = _session_lock(key)
    with lock:
        if key in _SESSION_CACHE:
            return _SESSION_CACHE[key]
        ort = import_onnxruntime()
        if ort is None:
            raise OnnxAccelerationUnavailable("onnxruntime is not installed.")
        providers: list[Any] = [backend.execution_provider]
        if backend.provider_options:
            providers = [(backend.execution_provider, backend.provider_options)]
        try:
            session = ort.InferenceSession(backend.model_path, providers=providers)
        except Exception as exc:  # noqa: BLE001 - optional native runtime failures should degrade.
            raise OnnxAccelerationUnavailable(f"Failed to create ONNX Runtime session: {exc}") from exc
        _SESSION_CACHE[key] = session
        return session


def run_session(session: Any, inputs: dict[str, np.ndarray], output_names: list[str] | None = None) -> list[Any]:
    try:
        return list(session.run(output_names, inputs))
    except Exception as exc:  # noqa: BLE001
        raise OnnxAccelerationUnavailable(f"ONNX Runtime inference failed: {exc}") from exc


def session_input_names(session: Any) -> list[str]:
    try:
        return [str(item.name) for item in session.get_inputs()]
    except Exception as exc:  # noqa: BLE001
        raise OnnxAccelerationUnavailable(f"Unable to inspect ONNX model inputs: {exc}") from exc


def session_output_names(session: Any) -> list[str]:
    try:
        return [str(item.name) for item in session.get_outputs()]
    except Exception:
        return []


def health_payload(
    *,
    component: str,
    backend: OnnxSessionBackend | None,
    configured_model_path: str,
    configured_provider: str = "",
    error: str = "",
) -> dict[str, Any]:
    packages = runtime_packages_snapshot()
    providers = available_execution_providers()
    if backend is None:
        return {
            "available": False,
            "component": component,
            "kind": component,
            "model_path": configured_model_path,
            "execution_provider": "",
            "available_providers": providers,
            "runtime_package": "",
            "configured_provider": configured_provider,
            "selected_provider": "",
            "runtime_packages": packages,
            "winml": winml_snapshot(packages, providers),
            "errors": [error] if error else [],
            "error": error,
        }
    return {
        "available": True,
        "component": component,
        **asdict(backend),
        "configured_provider": configured_provider,
        "selected_provider": backend.execution_provider,
        "runtime_packages": packages,
        "winml": winml_snapshot(packages, providers),
        "errors": [],
        "error": "",
    }


def preprocess_image_for_onnx(
    image_path: Path,
    *,
    size: int = 224,
    normalize: bool = True,
) -> np.ndarray:
    try:
        from PIL import Image
    except ImportError as exc:
        raise OnnxAccelerationUnavailable("Pillow is not installed for image preprocessing.") from exc
    try:
        with Image.open(image_path) as image:
            image = image.convert("RGB").resize((size, size))
            array = np.asarray(image, dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        raise OnnxAccelerationUnavailable(f"Failed to load image for ONNX preprocessing: {exc}") from exc
    array = array / 255.0
    if normalize:
        mean = np.asarray([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
        std = np.asarray([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
        array = (array - mean) / std
    return np.transpose(array, (2, 0, 1))[None, :, :, :].astype(np.float32, copy=False)


def first_present(values: Iterable[str]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _session_lock(key: str) -> threading.Lock:
    with _GLOBAL_LOCK:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = threading.Lock()
        return _SESSION_LOCKS[key]


def _runtime_package_snapshot(module_name: str) -> dict[str, Any]:
    status = _module_status(module_name)
    if not status["available"]:
        return status
    version = ""
    try:
        version = importlib.metadata.version(module_name.replace("_", "-"))
    except Exception:
        module = importlib.import_module(module_name)
        version = str(getattr(module, "__version__", "") or "")
    return {**status, "version": version}


def _module_status(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return {"available": False, "module": module_name, "error": str(exc)}
    except Exception as exc:
        return {"available": False, "module": module_name, "error": str(exc)}
    version = str(getattr(module, "__version__", "") or "")
    return {"available": True, "module": module_name, "version": version, "error": ""}


def _module_available(module_name: str) -> bool:
    return _module_status(module_name)["available"]
