"""Ollama lifecycle management — detect, install, pull models."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

try:
    import psutil
except Exception:  # pragma: no cover - optional in stripped-down test envs  # noqa: BLE001
    psutil = None  # type: ignore[assignment]

from app.config import get_env
from app.core.audit import record
from app.policy.redaction import redact_public_text

OLLAMA_API = "http://127.0.0.1:11434"
RECOMMENDED_MODEL = "qwen2.5:3b"
FALLBACK_SMALL_MODEL = RECOMMENDED_MODEL
FALLBACK_MEDIUM_MODEL = "qwen2.5:7b"
INSTALLABLE_LOCAL_MODELS = frozenset(
    {
        RECOMMENDED_MODEL,
        FALLBACK_SMALL_MODEL,
        FALLBACK_MEDIUM_MODEL,
        "llama3.2:3b",
    }
)
_GIB = 1024**3
_MIN_CPU_CORES = 4
_MIN_RAM_BYTES = 8 * _GIB
_MIN_DISK_BYTES = 8 * _GIB
_MEDIUM_CPU_CORES = 6
_MEDIUM_RAM_BYTES = 16 * _GIB
_MEDIUM_DISK_BYTES = 12 * _GIB
_TIMEOUT = 5.0
_BUNDLED_ENV_KEYS = ("LENGRVIS_BUNDLED_OLLAMA_DIR",)
_BUNDLED_MODEL_ENV_KEYS = ("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR",)
_BUNDLED_RELATIVE_DIRS = (
    ("resources", "ollama"),
    ("ollama",),
)
_BUNDLED_MODEL_RELATIVE_DIRS = (
    ("resources", "ollama-models"),
    ("ollama-models",),
)
_BUNDLED_MANIFEST_ENV_KEYS = ("LENGRVIS_OLLAMA_BUNDLE_MANIFEST",)
_BUNDLED_MANIFEST_RELATIVE_PATHS = (
    ("resources", "ollama-bundle-manifest.json"),
    ("ollama-bundle-manifest.json",),
)
_PUBLIC_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_MAX_PUBLIC_ERROR_CHARS = 600


def normalize_install_model(model: str | None) -> str:
    """Return an approved local model name for install/pull operations."""
    normalized = str(model or "").strip()
    if not normalized:
        return RECOMMENDED_MODEL
    if normalized not in INSTALLABLE_LOCAL_MODELS:
        allowed = ", ".join(sorted(INSTALLABLE_LOCAL_MODELS))
        raise ValueError(f"Local model install is restricted to supported models: {allowed}")
    return normalized


def _public_text(value: Any, fallback: str = "Local AI action failed.") -> str:
    text = str(value or fallback)
    without_urls = _PUBLIC_URL_RE.sub("[REDACTED_URL]", text)
    redacted = redact_public_text(without_urls)
    redacted = " ".join(redacted.split())
    if len(redacted) > _MAX_PUBLIC_ERROR_CHARS:
        return f"{redacted[: _MAX_PUBLIC_ERROR_CHARS - 1].rstrip()}..."
    return redacted


def _public_model_name(model: str) -> str:
    return _public_text(model, fallback=RECOMMENDED_MODEL)


def _public_model_names(models: list[str]) -> list[str]:
    return [_public_model_name(model) for model in models if str(model or "").strip()]


def _public_manifest_string(value: Any) -> str:
    return _public_text(value, fallback="")


def _public_bundle_manifest_value(key: str, value: Any) -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key == "path" or normalized_key.endswith("_path"):
        return ""
    if isinstance(value, str):
        return _public_manifest_string(value)
    if isinstance(value, dict):
        return {str(item_key): _public_bundle_manifest_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_public_bundle_manifest_value("", item) for item in value]
    return value


def _public_bundle_manifest_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _public_bundle_manifest_value(str(key), value) for key, value in summary.items()}


async def status() -> dict[str, Any]:
    """Return Ollama installation and runtime status."""
    target = RECOMMENDED_MODEL
    installed = is_installed()
    readiness = hardware_readiness()
    runtime_source = _ollama_runtime_source()
    bundled_models_dir = _bundled_ollama_models_dir()
    bundled_model_available = _bundled_model_available(target)
    bundled_model_configured = _bundled_model_configured(target)
    bundle_manifest = _public_bundle_manifest_summary(_ollama_bundle_manifest_summary())
    if not installed:
        next_action = _setup_next_action(
            readiness,
            installed=False,
            running=False,
            has_model=False,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=bundled_model_configured,
        )
        return {
            "installed": False,
            "running": False,
            "models": [],
            "recommended_model": target,
            "has_recommended": False,
            "readiness": readiness,
            "runtime_source": runtime_source,
            "bundled_runtime_available": bundled_runtime_available(),
            "bundled_runtime_path": "",
            "bundled_models_available": bundled_models_dir is not None,
            "bundled_models_path": "",
            "bundled_model_available": bundled_model_available,
            "bundled_model_configured": bundled_model_configured,
            "bundle_manifest": bundle_manifest,
            "next_action": next_action,
            "repair_action": _setup_repair_action(next_action, target),
            "verification": _setup_verification(
                readiness=readiness,
                installed=False,
                running=False,
                models=[],
                target=target,
                runtime_source=runtime_source,
                bundled_model_available=bundled_model_available,
                bundled_model_configured=bundled_model_configured,
                next_action=next_action,
            ),
            "evidence": _setup_evidence(
                readiness=readiness,
                installed=False,
                running=False,
                models=[],
                target=target,
                runtime_source=runtime_source,
                bundled_model_available=bundled_model_available,
                bundled_model_configured=bundled_model_configured,
            ),
        }
    running = await is_running()
    models = await list_models() if running else []
    has_recommended = _has_model(models, target)
    bundled_model_configured = _bundled_model_configured(target) and (not running or has_recommended)
    next_action = _setup_next_action(
        readiness,
        installed=installed,
        running=running,
        has_model=has_recommended,
        bundled_model_available=bundled_model_available,
        bundled_model_configured=bundled_model_configured,
    )
    return {
        "installed": True,
        "running": running,
        "models": _public_model_names(models),
        "recommended_model": target,
        "has_recommended": has_recommended,
        "readiness": readiness,
        "runtime_source": runtime_source,
        "bundled_runtime_available": runtime_source == "bundled",
        "bundled_runtime_path": "",
        "bundled_models_available": bundled_models_dir is not None,
        "bundled_models_path": "",
        "bundled_model_available": bundled_model_available,
        "bundled_model_configured": bundled_model_configured,
        "bundle_manifest": bundle_manifest,
        "next_action": next_action,
        "repair_action": _setup_repair_action(next_action, target),
        "verification": _setup_verification(
            readiness=readiness,
            installed=installed,
            running=running,
            models=models,
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=bundled_model_configured,
            next_action=next_action,
        ),
        "evidence": _setup_evidence(
            readiness=readiness,
            installed=installed,
            running=running,
            models=models,
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=bundled_model_configured,
        ),
    }


async def setup_plan(model: str | None = None) -> dict[str, Any]:
    """Return a user-facing local AI setup plan without mutating the machine."""
    target = normalize_install_model(model)
    readiness = hardware_readiness(target)
    installed = is_installed()
    runtime_source = _ollama_runtime_source()
    running = await is_running() if installed else False
    models = await list_models() if running else []
    has_model = _has_model(models, target)
    bundled_available = runtime_source == "bundled"
    bundled_models_dir = _bundled_ollama_models_dir()
    bundled_model_available = _bundled_model_available(target)
    bundled_model_configured = _bundled_model_configured(target) and (not running or has_model)
    bundle_manifest = _public_bundle_manifest_summary(_ollama_bundle_manifest_summary())
    next_action = _setup_next_action(
        readiness,
        installed,
        running,
        has_model,
        bundled_model_available,
        bundled_model_configured,
    )
    hardware_blocked = not readiness["can_install"]
    return {
        "ready": readiness["can_install"] and installed and running and has_model,
        "can_install": readiness["can_install"],
        "model": target,
        "readiness": readiness,
        "installed": installed,
        "running": running,
        "models": _public_model_names(models),
        "has_model": has_model,
        "runtime_source": runtime_source,
        "bundled_runtime_available": bundled_available,
        "bundled_runtime_path": "",
        "bundled_models_available": bundled_models_dir is not None,
        "bundled_models_path": "",
        "bundled_model_available": bundled_model_available,
        "bundled_model_configured": bundled_model_configured,
        "bundle_manifest": bundle_manifest,
        "steps": [
            {
                "key": "hardware",
                "label": "Check this computer",
                "state": "done" if readiness["can_install"] else "blocked",
                "detail": readiness["reason"],
            },
            {
                "key": "runtime",
                "label": "Install local AI runtime",
                "state": "done" if installed else "pending" if hardware_blocked else "current",
                "detail": _runtime_setup_detail(installed, bundled_available),
            },
            {
                "key": "server",
                "label": "Start local AI service",
                "state": "done" if running else "current" if installed and not hardware_blocked else "pending",
                "detail": "Ollama is running." if running else "Lengrvis will start Ollama after installation.",
            },
            {
                "key": "model",
                "label": _model_setup_label(has_model, bundled_model_available, bundled_model_configured, running),
                "state": "done" if has_model else "current" if running and not hardware_blocked else "pending",
                "detail": _model_setup_detail(
                    target, has_model, bundled_model_available, bundled_model_configured, running
                ),
            },
        ],
        "next_action": next_action,
        "repair_action": _setup_repair_action(next_action, target),
        "verification": _setup_verification(
            readiness=readiness,
            installed=installed,
            running=running,
            models=models,
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=bundled_model_configured,
            next_action=next_action,
        ),
        "evidence": _setup_evidence(
            readiness=readiness,
            installed=installed,
            running=running,
            models=models,
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=bundled_model_configured,
        ),
    }


def hardware_readiness(model: str | None = None) -> dict[str, Any]:
    """Assess whether this computer is a reasonable target for local Ollama setup."""
    memory_total = _total_memory_bytes()
    disk_free = _ollama_disk_free_bytes()
    cpu_cores = os.cpu_count() or 0
    return assess_hardware(
        model=model,
        memory_total_bytes=memory_total,
        disk_free_bytes=disk_free,
        cpu_logical_cores=cpu_cores,
        gpu_summary=_gpu_summary(),
    )


def assess_hardware(
    *,
    model: str | None = None,
    memory_total_bytes: int = 0,
    disk_free_bytes: int = 0,
    cpu_logical_cores: int = 0,
    gpu_summary: str = "",
) -> dict[str, Any]:
    """Pure hardware gate used by runtime checks and tests."""
    target = model or _recommended_model_for_hardware(
        memory_total_bytes=memory_total_bytes,
        disk_free_bytes=disk_free_bytes,
        cpu_logical_cores=cpu_logical_cores,
    )
    requirements = _requirements_for_model(target)
    checks = [
        {
            "key": "memory",
            "label": "Memory",
            "ok": memory_total_bytes >= requirements["memory_total_bytes"],
            "actual": _format_bytes(memory_total_bytes),
            "required": _format_bytes(requirements["memory_total_bytes"]),
        },
        {
            "key": "disk",
            "label": "Free disk space",
            "ok": disk_free_bytes >= requirements["disk_free_bytes"],
            "actual": _format_bytes(disk_free_bytes),
            "required": _format_bytes(requirements["disk_free_bytes"]),
        },
        {
            "key": "cpu",
            "label": "CPU cores",
            "ok": cpu_logical_cores >= requirements["cpu_logical_cores"],
            "actual": str(cpu_logical_cores or "unknown"),
            "required": str(requirements["cpu_logical_cores"]),
        },
    ]
    can_install = all(check["ok"] for check in checks)
    failed = [check for check in checks if not check["ok"]]
    reason = (
        f"This computer is ready for {target}."
        if can_install
        else "Local AI setup needs " + ", ".join(f"{item['label']} >= {item['required']}" for item in failed) + "."
    )
    next_action = "continue_setup" if can_install else "hardware_blocked"
    return {
        "can_install": can_install,
        "recommended_model": target,
        "reason": reason,
        "checks": checks,
        "next_action": next_action,
        "repair_action": _setup_repair_action(next_action, target),
        "memory_total_bytes": memory_total_bytes,
        "disk_free_bytes": disk_free_bytes,
        "cpu_logical_cores": cpu_logical_cores,
        "gpu_summary": gpu_summary,
    }


def is_installed() -> bool:
    """Check if ollama binary is on PATH."""
    return _ollama_executable() is not None


def bundled_runtime_available() -> bool:
    """Return whether Lengrvis can use an Ollama runtime shipped with the app."""
    return _bundled_ollama_executable() is not None


async def is_running() -> bool:
    """Check if Ollama server is responding."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_API}/api/tags")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def list_models() -> list[str]:
    """List installed Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{OLLAMA_API}/api/tags")
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


async def install() -> dict[str, Any]:
    """Make Ollama available, preferring a Lengrvis-bundled runtime over winget."""
    source = _ollama_runtime_source()
    if source == "bundled":
        return {
            "ok": True,
            "message": "Bundled Ollama runtime is ready.",
            "source": source,
            "executable": "",
        }
    if source == "system":
        return {
            "ok": True,
            "message": "Ollama is already installed.",
            "source": source,
            "executable": "",
        }

    if sys.platform != "win32":
        return _ollama_action_error("Auto-install is only supported on Windows.", "install_runtime")

    try:
        proc = await asyncio.create_subprocess_exec(
            "winget",
            "install",
            "--id",
            "Ollama.Ollama",
            "--accept-package-agreements",
            "--accept-source-agreements",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        ok = proc.returncode == 0
        record("ollama.install", "OllamaService", {"ok": ok, "returncode": proc.returncode})
        message = stdout.decode(errors="replace").strip() if ok else stderr.decode(errors="replace").strip()
        if not ok:
            failure = _ollama_action_error(message or f"winget exited with {proc.returncode}.", "install_runtime")
            failure["source"] = "winget"
            return failure
        return {
            "ok": ok,
            "message": _public_text(message, fallback="Ollama installed successfully."),
            "source": "winget",
        }
    except FileNotFoundError:
        return _ollama_action_error(
            "winget not found. Install Ollama manually from the official Ollama website, then retry.",
            "install_runtime",
        )
    except TimeoutError:
        return _ollama_action_error("Installation timed out after 120 seconds.", "install_runtime")
    except Exception as exc:  # noqa: BLE001
        return _ollama_action_error(str(exc), "install_runtime")


# Handle to the `ollama serve` process we spawned (None if Ollama was already
# running or started externally). Used to stop it on backend shutdown.
_SERVER_PROCESS: subprocess.Popen | None = None


def stop_spawned_server() -> bool:
    """Terminate the `ollama serve` process this backend spawned, if any."""
    global _SERVER_PROCESS
    proc = _SERVER_PROCESS
    _SERVER_PROCESS = None
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        record("ollama.stop", "OllamaService", {"ok": True, "spawned_by_backend": True})
        return True
    except Exception as exc:  # noqa: BLE001
        record("ollama.stop", "OllamaService", {"ok": False, "error": _public_text(exc)})
        return False


async def start_server() -> dict[str, Any]:
    """Start Ollama server in the background when the CLI is available."""
    if await is_running():
        return {"ok": True, "message": "Ollama server is already running."}
    if not is_installed():
        return _ollama_action_error("Ollama is not installed.", "install_runtime")

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        executable = _ollama_executable()
        if not executable:
            return _ollama_action_error("Ollama executable not found.", "install_runtime")
        env = os.environ.copy()
        models_dir = _preferred_ollama_models_dir()
        if models_dir:
            env["OLLAMA_MODELS"] = str(models_dir)
        global _SERVER_PROCESS
        _SERVER_PROCESS = subprocess.Popen(  # noqa: S603
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            env=env,
        )
        record("ollama.start", "OllamaService", {"ok": True, "models_dir_configured": bool(models_dir)})
        return {
            "ok": True,
            "message": "Ollama server is starting.",
            "models_dir": "",
            "models_dir_configured": bool(models_dir),
        }
    except Exception as exc:  # noqa: BLE001
        record("ollama.start", "OllamaService", {"ok": False, "error": _public_text(exc)})
        return _ollama_action_error(str(exc), "start_runtime")


def _requirements_for_model(model: str) -> dict[str, int]:
    normalized = model.lower()
    if "7b" in normalized:
        return {
            "memory_total_bytes": _MEDIUM_RAM_BYTES,
            "disk_free_bytes": _MEDIUM_DISK_BYTES,
            "cpu_logical_cores": _MEDIUM_CPU_CORES,
        }
    return {
        "memory_total_bytes": _MIN_RAM_BYTES,
        "disk_free_bytes": _MIN_DISK_BYTES,
        "cpu_logical_cores": _MIN_CPU_CORES,
    }


def _ollama_executable() -> str | None:
    bundled = _bundled_ollama_executable()
    if bundled:
        return bundled
    path = shutil.which("ollama")
    if path:
        return path
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
        os.path.expandvars(r"%ProgramFiles%\Ollama\ollama.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Ollama\ollama.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _ollama_runtime_source() -> str:
    if _bundled_ollama_executable():
        return "bundled"
    if _system_ollama_executable():
        return "system"
    return "missing"


def _system_ollama_executable() -> str | None:
    path = shutil.which("ollama")
    if path:
        return path
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
        os.path.expandvars(r"%ProgramFiles%\Ollama\ollama.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Ollama\ollama.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _bundled_ollama_executable() -> str | None:
    candidates = [
        *(_candidate_ollama_executables_from_dir(value) for value in _bundled_runtime_dirs()),
    ]
    for group in candidates:
        for candidate in group:
            if candidate.exists() and candidate.is_file():
                return str(candidate)
    return None


def _bundled_runtime_dirs() -> list[Path]:
    roots: list[Path] = []
    for key in _BUNDLED_ENV_KEYS:
        value = get_env(key)
        if value:
            roots.append(Path(value).expanduser())

    for anchor in _bundle_anchor_dirs():
        for parts in _BUNDLED_RELATIVE_DIRS:
            roots.append(anchor.joinpath(*parts))
    return _unique_paths(roots)


def _bundled_ollama_models_dir() -> Path | None:
    for directory in _bundled_model_dirs():
        if directory.exists() and directory.is_dir():
            return directory
    return None


def _bundled_model_dirs() -> list[Path]:
    roots: list[Path] = []
    for key in _BUNDLED_MODEL_ENV_KEYS:
        value = get_env(key)
        if value:
            roots.append(Path(value).expanduser())

    for anchor in _bundle_anchor_dirs():
        for parts in _BUNDLED_MODEL_RELATIVE_DIRS:
            roots.append(anchor.joinpath(*parts))
    return _unique_paths(roots)


def _bundle_anchor_dirs() -> list[Path]:
    anchors = []
    if getattr(sys, "frozen", False):
        anchors.append(Path(sys.executable).resolve().parent)
    anchors.append(Path(__file__).resolve().parents[3])
    return _unique_paths(anchors)


def _ollama_bundle_manifest_path() -> Path | None:
    for key in _BUNDLED_MANIFEST_ENV_KEYS:
        value = get_env(key)
        if value:
            path = Path(value).expanduser()
            if path.exists() and path.is_file():
                return path
    for anchor in _bundle_anchor_dirs():
        for parts in _BUNDLED_MANIFEST_RELATIVE_PATHS:
            path = anchor.joinpath(*parts)
            if path.exists() and path.is_file():
                return path
    return None


def _ollama_bundle_manifest_summary() -> dict[str, Any]:
    path = _ollama_bundle_manifest_path()
    if not path:
        return {"present": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "valid": False, "path": "", "error": _public_text(exc)}
    runtime_summary = data.get("runtime", {}).get("summary", {}) if isinstance(data, dict) else {}
    models_payload = data.get("models", {}) if isinstance(data, dict) else {}
    models_summary = models_payload.get("summary", {}) if isinstance(models_payload, dict) else {}
    return {
        "present": True,
        "valid": data.get("schema") == 1 and bool(data.get("accepted_licenses")),
        "path": "",
        "model": _public_model_name(str(data.get("model", ""))),
        "accepted_licenses": bool(data.get("accepted_licenses")),
        "runtime_sha256": _public_manifest_string(runtime_summary.get("sha256", "")),
        "models_sha256": _public_manifest_string(models_summary.get("sha256", "")),
        "runtime_files": int(runtime_summary.get("files", 0) or 0),
        "models_files": int(models_summary.get("files", 0) or 0),
        "model_manifest": _public_manifest_string(models_payload.get("model_manifest", ""))
        if isinstance(models_payload, dict)
        else "",
    }


def _preferred_ollama_models_dir() -> Path | None:
    existing = get_env("OLLAMA_MODELS")
    if existing:
        return Path(existing).expanduser()
    return _bundled_ollama_models_dir()


def _same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except Exception:  # noqa: BLE001
        return str(left.expanduser()).lower() == str(right.expanduser()).lower()


def _bundled_model_configured(model: str) -> bool:
    """Return whether the preferred Ollama model store points at the bundled model."""
    bundled_dir = _bundled_ollama_models_dir()
    return _bundled_model_available(model) and _same_path(_preferred_ollama_models_dir(), bundled_dir)


def _bundled_model_available(model: str) -> bool:
    return bool(_bundled_model_evidence(model)["complete"])


def _bundled_model_evidence(
    model: str,
    *,
    runtime_source: str | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    target = normalize_install_model(model)
    source = runtime_source or _ollama_runtime_source()
    runtime_path = _safe_runtime_path(source)
    models_dir = _bundled_ollama_models_dir()
    model_manifest_path = _bundled_model_manifest_path(target, models_dir)
    bundle_manifest = _ollama_bundle_manifest_summary()
    raw_manifest_model = str(bundle_manifest.get("model") or "")
    manifest_model = _public_model_name(raw_manifest_model)
    manifest_valid = bool(bundle_manifest.get("present") and bundle_manifest.get("valid"))
    manifest_model_matches = manifest_valid and bool(raw_manifest_model) and _has_model([raw_manifest_model], target)
    runtime_available = source == "bundled" and bool(runtime_path)
    models_available = models_dir is not None
    model_manifest_present = model_manifest_path is not None
    configured = _same_path(_preferred_ollama_models_dir(), models_dir)
    has_model = _has_model(models or [], target) if models is not None else False
    complete = runtime_available and models_available and model_manifest_present and manifest_model_matches
    return {
        "complete": complete,
        "runtime_available": runtime_available,
        "runtime_source": source,
        "runtime_path": "",
        "models_available": models_available,
        "models_path": "",
        "model_manifest_present": model_manifest_present,
        "model_manifest_path": "",
        "bundle_manifest_present": bool(bundle_manifest.get("present")),
        "bundle_manifest_valid": manifest_valid,
        "bundle_manifest_path": "",
        "manifest_model": manifest_model,
        "manifest_model_matches": manifest_model_matches,
        "configured": configured,
        "has_model": has_model,
    }


def _bundled_model_manifest_path(model: str, models_dir: Path | None = None) -> Path | None:
    if not models_dir:
        return None
    name, tag = _ollama_model_name_and_tag(model)
    candidates = [
        models_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag,
    ]
    if "/" in name:
        candidates.append(models_dir / "manifests" / name / tag)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    manifests_dir = models_dir / "manifests"
    if not manifests_dir.exists():
        return None
    for path in manifests_dir.rglob(tag):
        if path.name == tag and path.parent.name == name and path.is_file():
            return path
    return None


def _ollama_model_name_and_tag(model: str) -> tuple[str, str]:
    value = (model or RECOMMENDED_MODEL).strip()
    if ":" not in value:
        return value, "latest"
    name, tag = value.rsplit(":", 1)
    return name, tag or "latest"


def _candidate_ollama_executables_from_dir(directory: Path) -> list[Path]:
    exe_name = "ollama.exe" if sys.platform == "win32" else "ollama"
    return [
        directory / exe_name,
        directory / "bin" / exe_name,
        directory / "Ollama" / exe_name,
    ]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _safe_runtime_path(runtime_source: str) -> str:
    if runtime_source != "bundled":
        return ""
    executable = _bundled_ollama_executable()
    return executable or ""


def _runtime_setup_detail(installed: bool, bundled_available: bool) -> str:
    if installed and bundled_available:
        return "Lengrvis bundled Ollama runtime is available."
    if installed:
        return "Ollama is installed."
    if bundled_available:
        return "Lengrvis will use the bundled Ollama runtime."
    return "Ollama is not installed yet; one-click setup can install it, start it, and prepare the model."


def _model_setup_label(
    has_model: bool,
    bundled_model_available: bool,
    bundled_model_configured: bool,
    running: bool,
) -> str:
    if has_model:
        return "Use local model"
    if bundled_model_available and running and not bundled_model_configured:
        return "Restart local service for bundled model"
    if bundled_model_available:
        return "Use bundled local model"
    return "Download recommended model"


def _model_setup_detail(
    target: str,
    has_model: bool,
    bundled_model_available: bool,
    bundled_model_configured: bool = False,
    running: bool = False,
) -> str:
    if has_model:
        return f"{target} is ready."
    if bundled_model_configured:
        return f"{target} is included with Lengrvis and the local service is configured to read it."
    if bundled_model_available and running:
        return (
            f"{target} is included with Lengrvis, but the running Ollama service is not using "
            "the Lengrvis bundled model directory."
        )
    if bundled_model_available:
        return f"{target} is included with Lengrvis and will be used when the local service starts."
    if running:
        return f"Ollama is running; download {target} before privacy mode can use local AI."
    return f"After Ollama is installed and running, {target} will be downloaded before privacy mode can use local AI."


def _recommended_model_for_hardware(
    *,
    memory_total_bytes: int,
    disk_free_bytes: int,
    cpu_logical_cores: int,
) -> str:
    if (
        memory_total_bytes >= _MEDIUM_RAM_BYTES
        and disk_free_bytes >= _MEDIUM_DISK_BYTES
        and cpu_logical_cores >= _MEDIUM_CPU_CORES
    ):
        return FALLBACK_MEDIUM_MODEL
    return FALLBACK_SMALL_MODEL


def _has_model(models: list[str], target: str) -> bool:
    normalized_target = target.lower()
    return any(
        model.lower() == normalized_target
        or model.lower().startswith(f"{normalized_target}-")
        or model.lower().startswith(f"{normalized_target}_")
        or model.lower().startswith(f"{normalized_target}.")
        for model in models
    )


def _setup_next_action(
    readiness: dict[str, Any],
    installed: bool,
    running: bool,
    has_model: bool,
    bundled_model_available: bool = False,
    bundled_model_configured: bool = False,
) -> str:
    if not readiness.get("can_install"):
        return "hardware_blocked"
    if not installed:
        return "install_runtime"
    if not running:
        return "start_runtime"
    if bundled_model_available and not has_model:
        if not bundled_model_configured:
            return "restart_runtime_with_bundled_models"
        return "use_bundled_model"
    if not has_model:
        return "download_model"
    return "ready"


def _setup_repair_action(next_action: str, target: str) -> dict[str, str]:
    actions = {
        "hardware_blocked": {
            "code": "free_resources_for_local_ai",
            "label": "Free resources for local AI",
            "detail": (
                "Close memory-heavy apps, free disk space, or choose a smaller supported local model, "
                "then check again. "
                "Privacy mode stays local-only and will not silently use cloud or mock AI."
            ),
        },
        "continue_setup": {
            "code": "continue_setup",
            "label": "Continue local AI setup",
            "detail": (
                f"This computer passes the hardware preflight for {target}. "
                "Continue setup to verify Ollama, start the local service, and prepare the model."
            ),
        },
        "install_runtime": {
            "code": "install_runtime",
            "label": "Install Ollama runtime",
            "detail": (
                f"Use one-click setup to install Ollama, start the local service, and prepare {target}. "
                "If automatic install is unavailable, install Ollama manually and retry. "
                "Privacy tasks stay paused until a local runtime is available."
            ),
        },
        "start_runtime": {
            "code": "start_runtime",
            "label": "Start local AI service",
            "detail": (
                "Start Ollama, or close any stuck Ollama process and retry. "
                "Lengrvis will not switch privacy tasks to cloud or mock AI while the service is down."
            ),
        },
        "restart_runtime_with_bundled_models": {
            "code": "restart_runtime_with_bundled_models",
            "label": "Restart Ollama with bundled models",
            "detail": (
                "Close Ollama, then retry setup so Lengrvis can restart it with the included local model files. "
                "Privacy tasks stay local-only until Ollama lists the model."
            ),
        },
        "use_bundled_model": {
            "code": "use_bundled_model",
            "label": "Use bundled local model",
            "detail": f"Use the bundled {target} model without downloading it.",
        },
        "download_model": {
            "code": "download_model",
            "label": "Download recommended model",
            "detail": (
                f"Keep Ollama running and download {target}. If this app should include the model, "
                "verify the bundled model package, then retry setup. "
                "Privacy tasks stay local-only until the model is present."
            ),
        },
        "ready": {
            "code": "none",
            "label": "No repair needed",
            "detail": f"{target} is ready for local AI.",
        },
    }
    return actions.get(
        next_action,
        {
            "code": "prepare_local_ai",
            "label": "Prepare local AI",
            "detail": f"Run setup again to prepare {target}.",
        },
    )


def _ollama_action_error(error: str, next_action: str, model: str = RECOMMENDED_MODEL) -> dict[str, Any]:
    target = model if model in INSTALLABLE_LOCAL_MODELS else RECOMMENDED_MODEL
    readiness = hardware_readiness(target)
    installed = is_installed()
    runtime_source = _ollama_runtime_source()
    bundled_model_available = _bundled_model_available(target)
    return {
        "ok": False,
        "status": "error",
        "model": _public_model_name(model or target),
        "error": _public_text(error),
        "next_action": next_action,
        "repair_action": _setup_repair_action(next_action, target),
        "verification": _setup_verification(
            readiness=readiness,
            installed=installed,
            running=False,
            models=[],
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=False,
            next_action=next_action,
        ),
        "evidence": _setup_evidence(
            readiness=readiness,
            installed=installed,
            running=False,
            models=[],
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=False,
        ),
    }


def _progress_error(
    phase: str,
    error: str,
    next_action: str,
    model: str,
    *,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = model if model in INSTALLABLE_LOCAL_MODELS else RECOMMENDED_MODEL
    current_readiness = readiness or hardware_readiness(target)
    installed = is_installed()
    runtime_source = _ollama_runtime_source()
    bundled_model_available = _bundled_model_available(target)
    return {
        "phase": phase,
        "status": "error",
        "model": _public_model_name(model or target),
        "error": _public_text(error),
        "next_action": next_action,
        "repair_action": _setup_repair_action(next_action, target),
        "verification": _setup_verification(
            readiness=current_readiness,
            installed=installed,
            running=False,
            models=[],
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=False,
            next_action=next_action,
        ),
        "evidence": _setup_evidence(
            readiness=current_readiness,
            installed=installed,
            running=False,
            models=[],
            target=target,
            runtime_source=runtime_source,
            bundled_model_available=bundled_model_available,
            bundled_model_configured=False,
        ),
    }


def _setup_verification(
    *,
    readiness: dict[str, Any],
    installed: bool,
    running: bool,
    models: list[str],
    target: str,
    runtime_source: str,
    bundled_model_available: bool,
    bundled_model_configured: bool,
    next_action: str,
) -> dict[str, Any]:
    bundle = _bundled_model_evidence(target, runtime_source=runtime_source, models=models)
    has_model = _has_model(models, target)
    return {
        "ready": bool(readiness.get("can_install") and installed and running and has_model),
        "next_action": next_action,
        "paths_redacted": True,
        "privacy_fallback": "local_only_until_ready",
        "runtime": {
            "checked": True,
            "found": installed,
            "source": runtime_source,
            "path": "",
        },
        "server": {
            "checked": installed,
            "responding": running,
        },
        "model": {
            "required": target,
            "listed": has_model,
            "models_seen": _public_model_names(models),
        },
        "bundle": {
            "runtime_found": runtime_source == "bundled",
            "model_proven": bundled_model_available,
            "model_configured": bundled_model_configured,
            "manifest_present": bool(bundle["bundle_manifest_present"]),
            "manifest_valid": bool(bundle["bundle_manifest_valid"]),
            "manifest_model_matches": bool(bundle["manifest_model_matches"]),
            "paths": "",
        },
    }


def _setup_evidence(
    *,
    readiness: dict[str, Any],
    installed: bool,
    running: bool,
    models: list[str],
    target: str,
    runtime_source: str,
    bundled_model_available: bool,
    bundled_model_configured: bool,
) -> list[dict[str, Any]]:
    bundle = _bundled_model_evidence(target, runtime_source=runtime_source, models=models)
    has_model = _has_model(models, target)
    return [
        {
            "key": "hardware",
            "ok": bool(readiness.get("can_install")),
            "detail": str(readiness.get("reason") or ""),
            "checks": readiness.get("checks") or [],
            "failed_checks": [
                str(check.get("key") or check.get("label") or "")
                for check in readiness.get("checks", [])
                if isinstance(check, dict) and not check.get("ok")
            ],
        },
        {
            "key": "runtime",
            "ok": installed,
            "value": runtime_source,
            "path": "",
            "detail": _runtime_evidence_detail(installed, runtime_source),
        },
        {
            "key": "server",
            "ok": running,
            "detail": "Ollama API is responding." if running else "Ollama API is not responding.",
        },
        {
            "key": "model",
            "ok": has_model,
            "value": target,
            "models_seen": _public_model_names(models),
            "detail": f"{target} is listed by Ollama." if has_model else f"{target} is not listed by Ollama.",
        },
        {
            "key": "bundle_manifest",
            "ok": bool(bundle["bundle_manifest_valid"] and bundle["manifest_model_matches"]),
            "path": "",
            "value": bundle["manifest_model"],
            "detail": _bundle_manifest_evidence_detail(bundle, target),
        },
        {
            "key": "bundled_model",
            "ok": bundled_model_available,
            "models_path": "",
            "model_manifest_path": "",
            "configured": bundled_model_configured,
            "detail": _bundled_model_evidence_detail(bundle, target, bundled_model_configured),
        },
    ]


def _runtime_evidence_detail(installed: bool, runtime_source: str) -> str:
    if installed and runtime_source == "bundled":
        return "Bundled Ollama runtime executable was found."
    if installed and runtime_source == "system":
        return "System Ollama executable was found."
    if runtime_source == "bundled":
        return "Bundled Ollama runtime is available but not yet started."
    return "No Ollama runtime executable was found."


def _bundle_manifest_evidence_detail(bundle: dict[str, Any], target: str) -> str:
    if not bundle["bundle_manifest_present"]:
        return "No Ollama bundle manifest was found."
    if not bundle["bundle_manifest_valid"]:
        return "Ollama bundle manifest is present but invalid or lacks accepted licenses."
    if not bundle["manifest_model_matches"]:
        return f"Ollama bundle manifest does not prove that {target} is included."
    return f"Ollama bundle manifest proves that {target} is included."


def _bundled_model_evidence_detail(bundle: dict[str, Any], target: str, configured: bool) -> str:
    missing = []
    if not bundle["runtime_available"]:
        missing.append("bundled runtime")
    if not bundle["models_available"]:
        missing.append("bundled models directory")
    if not bundle["model_manifest_present"]:
        missing.append("model manifest")
    if not bundle["bundle_manifest_valid"] or not bundle["manifest_model_matches"]:
        missing.append("valid bundle manifest")
    if missing:
        return f"Bundled {target} is not proven available; missing " + ", ".join(missing) + "."
    if not configured:
        return (
            f"Bundled {target} is proven available, but Ollama is not configured to read the bundled model directory."
        )
    return f"Bundled {target} is proven available and the preferred model directory points to it."


def _total_memory_bytes() -> int:
    if psutil is None:
        return 0
    try:
        return int(psutil.virtual_memory().total)
    except Exception:  # noqa: BLE001
        return 0


def _ollama_disk_free_bytes() -> int:
    target = get_env("OLLAMA_MODELS") or os.path.expanduser("~/.ollama")
    try:
        existing = target
        while existing and not os.path.exists(existing):
            parent = os.path.dirname(existing)
            if parent == existing:
                break
            existing = parent
        return int(shutil.disk_usage(existing or os.path.expanduser("~")).free)
    except Exception:  # noqa: BLE001
        return 0


def _gpu_summary() -> str:
    if sys.platform != "win32":
        return ""
    try:
        proc = subprocess.run(  # noqa: S603
            ["wmic", "path", "win32_VideoController", "get", "name"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return ""
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip() and line.strip().lower() != "name"]
    return ", ".join(lines[:3])


def _format_bytes(value: int) -> str:
    if value <= 0:
        return "unknown"
    gib = value / _GIB
    return f"{gib:.1f} GB"


async def pull_model(model: str | None = None) -> dict[str, Any]:
    """Pull a model. Returns final status (not streaming for simplicity)."""
    try:
        target = normalize_install_model(model)
    except ValueError as exc:
        return _ollama_action_error(str(exc), "download_model", model=str(model or ""))
    record("ollama.pull_start", "OllamaService", {"model": target})

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(
                f"{OLLAMA_API}/api/pull",
                json={"name": target, "stream": False},
                timeout=600.0,
            )
            if resp.status_code == 200:
                record("ollama.pull_complete", "OllamaService", {"model": target})
                return {"ok": True, "model": target, "message": f"Model {target} pulled successfully."}
            return _ollama_action_error(f"Pull failed with status {resp.status_code}", "download_model", model=target)
    except Exception as exc:  # noqa: BLE001
        return _ollama_action_error(str(exc), "download_model", model=target)


async def pull_model_streaming(model: str | None = None):
    """Pull a model with streaming progress. Yields dicts with progress info."""
    try:
        target = normalize_install_model(model)
    except ValueError as exc:
        yield _ollama_action_error(str(exc), "download_model", model=str(model or ""))
        return
    record("ollama.pull_start", "OllamaService", {"model": target, "streaming": True})

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_API}/api/pull",
                json={"name": target, "stream": True},
                timeout=600.0,
            ) as resp:
                if resp.status_code != 200:
                    yield _ollama_action_error(
                        f"Pull failed with status {resp.status_code}", "download_model", model=target
                    )
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        total = data.get("total", 0)
                        completed = data.get("completed", 0)
                        pct = round(completed / total * 100, 1) if total else 0
                        yield {
                            "status": _public_text(data.get("status", "downloading"), fallback="downloading"),
                            "total": total,
                            "completed": completed,
                            "percent": pct,
                        }
                    except (json.JSONDecodeError, ZeroDivisionError):
                        continue
        record("ollama.pull_complete", "OllamaService", {"model": target})
        yield {"status": "success", "model": target}
    except Exception as exc:  # noqa: BLE001
        yield _ollama_action_error(str(exc), "download_model", model=target)


async def install_local_model(model: str | None = None):
    """Full install flow: detect Ollama -> install if needed -> pull model.
    Yields progress dicts for WebSocket streaming."""
    try:
        requested_model = normalize_install_model(model) if model else None
    except ValueError as exc:
        yield _progress_error("model", str(exc), "download_model", str(model or ""))
        return

    readiness = hardware_readiness(requested_model)
    try:
        target = normalize_install_model(requested_model or str(readiness["recommended_model"]))
    except ValueError as exc:
        yield _progress_error("model", str(exc), "download_model", str(readiness.get("recommended_model") or ""))
        return

    if not readiness["can_install"]:
        yield {
            "phase": "hardware",
            "status": "error",
            "error": _public_text(readiness["reason"], fallback="Local AI hardware requirements are not met."),
            "readiness": readiness,
            "model": target,
            "next_action": "hardware_blocked",
            "repair_action": _setup_repair_action("hardware_blocked", target),
            "verification": _setup_verification(
                readiness=readiness,
                installed=is_installed(),
                running=False,
                models=[],
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=_bundled_model_available(target),
                bundled_model_configured=False,
                next_action="hardware_blocked",
            ),
            "evidence": _setup_evidence(
                readiness=readiness,
                installed=is_installed(),
                running=False,
                models=[],
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=_bundled_model_available(target),
                bundled_model_configured=False,
            ),
        }
        return

    yield {
        "phase": "hardware",
        "status": "done",
        "message": readiness["reason"],
        "readiness": readiness,
        "model": target,
    }

    # Step 1: Check if Ollama is installed
    if not is_installed():
        yield {"phase": "install", "status": "installing", "message": "Installing Ollama..."}
        result = await install()
        if not result.get("ok"):
            yield _progress_error(
                "install",
                result.get("error", "Installation failed"),
                "install_runtime",
                target,
                readiness=readiness,
            )
            return
        yield {"phase": "install", "status": "done", "message": "Ollama installed successfully."}
    else:
        yield {"phase": "install", "status": "skipped", "message": "Ollama already installed."}

    # Step 2: Check if Ollama is running
    running = await is_running()
    started_with_lengrvis_models = False
    if not running:
        yield {"phase": "start", "status": "starting", "message": "Starting Ollama server..."}
        start_result = await start_server()
        if not start_result.get("ok"):
            yield _progress_error(
                "start",
                start_result.get("error") or start_result.get("message") or "Ollama server could not be started.",
                "start_runtime",
                target,
                readiness=readiness,
            )
            return
        started_with_lengrvis_models = bool(start_result.get("models_dir_configured") or start_result.get("models_dir"))
        yield {"phase": "start", "status": "waiting", "message": "Waiting for Ollama server to start..."}
        for _ in range(10):
            await asyncio.sleep(2)
            if await is_running():
                running = True
                break
        if not running:
            yield _progress_error(
                "start",
                "Ollama server not responding. Please start it manually.",
                "start_runtime",
                target,
                readiness=readiness,
            )
            return
    yield {"phase": "start", "status": "done", "message": "Ollama server is running."}

    models = await list_models()
    if _has_model(models, target):
        yield {
            "phase": "pull",
            "status": "skipped",
            "message": f"Local model {target} is already installed.",
            "model": target,
        }
        yield {"phase": "switch", "status": "done", "message": f"Local model {target} is ready.", "model": target}
        return

    bundled_model_available = _bundled_model_available(target)
    if bundled_model_available and started_with_lengrvis_models:
        yield {
            "phase": "pull",
            "status": "error",
            "error": (
                f"Bundled model {target} files were found, but Ollama did not list the model after startup. "
                "Download the model, or verify the bundled model package and retry setup."
            ),
            "model": target,
            "next_action": "download_model",
            "repair_action": _setup_repair_action("download_model", target),
            "evidence": _setup_evidence(
                readiness=readiness,
                installed=True,
                running=True,
                models=models,
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=True,
                bundled_model_configured=False,
            ),
        }
        return

    if bundled_model_available:
        yield {
            "phase": "pull",
            "status": "error",
            "error": (
                f"Bundled model {target} files were found, but Ollama did not list the model. "
                "Close Ollama and retry setup so Lengrvis can restart it with the included local model files."
            ),
            "model": target,
            "next_action": "restart_runtime_with_bundled_models",
            "repair_action": _setup_repair_action("restart_runtime_with_bundled_models", target),
            "evidence": _setup_evidence(
                readiness=readiness,
                installed=True,
                running=True,
                models=models,
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=True,
                bundled_model_configured=False,
            ),
        }
        return

    # Step 3: Pull model with progress
    pull_succeeded = False
    yield {"phase": "pull", "status": "starting", "model": target}
    async for progress in pull_model_streaming(target):
        if progress.get("status") == "error":
            yield {
                "phase": "pull",
                **progress,
                "model": _public_model_name(str(progress.get("model") or target)),
                "error": _public_text(progress.get("error"), fallback="Model download failed."),
                "repair_action": progress.get("repair_action") or _setup_repair_action("download_model", target),
                "evidence": progress.get("evidence")
                or _setup_evidence(
                    readiness=readiness,
                    installed=True,
                    running=True,
                    models=models,
                    target=target,
                    runtime_source=_ollama_runtime_source(),
                    bundled_model_available=False,
                    bundled_model_configured=False,
                ),
            }
            return
        yield {"phase": "pull", **progress}
        if progress.get("status") == "success":
            pull_succeeded = True

    if not pull_succeeded:
        yield _progress_error(
            "pull",
            f"Model {target} did not finish downloading.",
            "download_model",
            target,
            readiness=readiness,
        )
        return

    models = await list_models()
    if not _has_model(models, target):
        yield {
            "phase": "pull",
            "status": "error",
            "error": (
                f"Model {target} download reported success, but Ollama did not list the model after refresh. "
                "Retry the download or verify the local Ollama model store."
            ),
            "model": target,
            "next_action": "download_model",
            "repair_action": _setup_repair_action("download_model", target),
            "verification": _setup_verification(
                readiness=readiness,
                installed=True,
                running=True,
                models=models,
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=False,
                bundled_model_configured=False,
                next_action="download_model",
            ),
            "evidence": _setup_evidence(
                readiness=readiness,
                installed=True,
                running=True,
                models=models,
                target=target,
                runtime_source=_ollama_runtime_source(),
                bundled_model_available=False,
                bundled_model_configured=False,
            ),
        }
        return

    # Step 4: Switch provider
    yield {"phase": "switch", "status": "done", "message": f"Local model {target} is ready.", "model": target}
