"""Ollama lifecycle management — detect, install, pull models."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import httpx

try:
    import psutil
except Exception:  # pragma: no cover - optional in stripped-down test envs
    psutil = None  # type: ignore[assignment]

from app.core.audit import record

OLLAMA_API = "http://127.0.0.1:11434"
RECOMMENDED_MODEL = "qwen2.5:3b"
FALLBACK_SMALL_MODEL = RECOMMENDED_MODEL
FALLBACK_MEDIUM_MODEL = "qwen2.5:7b"
_GIB = 1024**3
_MIN_CPU_CORES = 4
_MIN_RAM_BYTES = 8 * _GIB
_MIN_DISK_BYTES = 8 * _GIB
_MEDIUM_CPU_CORES = 6
_MEDIUM_RAM_BYTES = 16 * _GIB
_MEDIUM_DISK_BYTES = 12 * _GIB
_TIMEOUT = 5.0
_BUNDLED_ENV_KEYS = ("MAVRIS_BUNDLED_OLLAMA_DIR", "MARVIS_BUNDLED_OLLAMA_DIR")
_BUNDLED_MODEL_ENV_KEYS = ("MAVRIS_BUNDLED_OLLAMA_MODELS_DIR", "MARVIS_BUNDLED_OLLAMA_MODELS_DIR")
_BUNDLED_RELATIVE_DIRS = (
    ("resources", "ollama"),
    ("ollama",),
    ("vendor", "ollama"),
)
_BUNDLED_MODEL_RELATIVE_DIRS = (
    ("resources", "ollama-models"),
    ("ollama-models",),
    ("vendor", "ollama-models"),
)
_BUNDLED_MANIFEST_ENV_KEYS = ("MAVRIS_OLLAMA_BUNDLE_MANIFEST", "MARVIS_OLLAMA_BUNDLE_MANIFEST")
_BUNDLED_MANIFEST_RELATIVE_PATHS = (
    ("resources", "ollama-bundle-manifest.json"),
    ("ollama-bundle-manifest.json",),
    ("vendor", "ollama-bundle-manifest.json"),
)


async def status() -> dict[str, Any]:
    """Return Ollama installation and runtime status."""
    installed = is_installed()
    readiness = hardware_readiness()
    runtime_source = _ollama_runtime_source()
    bundled_models_dir = _bundled_ollama_models_dir()
    bundle_manifest = _ollama_bundle_manifest_summary()
    if not installed:
        return {
            "installed": False,
            "running": False,
            "models": [],
            "recommended_model": RECOMMENDED_MODEL,
            "has_recommended": False,
            "readiness": readiness,
            "runtime_source": runtime_source,
            "bundled_runtime_available": bundled_runtime_available(),
            "bundled_runtime_path": _safe_runtime_path(runtime_source),
            "bundled_models_available": bundled_models_dir is not None,
            "bundled_models_path": str(bundled_models_dir) if bundled_models_dir else "",
            "bundle_manifest": bundle_manifest,
        }
    running = await is_running()
    models = await list_models() if running else []
    return {
        "installed": True,
        "running": running,
        "models": models,
        "recommended_model": RECOMMENDED_MODEL,
        "has_recommended": _has_model(models, RECOMMENDED_MODEL),
        "readiness": readiness,
        "runtime_source": runtime_source,
        "bundled_runtime_available": runtime_source == "bundled",
        "bundled_runtime_path": _safe_runtime_path(runtime_source),
        "bundled_models_available": bundled_models_dir is not None,
        "bundled_models_path": str(bundled_models_dir) if bundled_models_dir else "",
        "bundle_manifest": bundle_manifest,
    }


async def setup_plan(model: str | None = None) -> dict[str, Any]:
    """Return a user-facing local AI setup plan without mutating the machine."""
    target = model or RECOMMENDED_MODEL
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
    bundle_manifest = _ollama_bundle_manifest_summary()
    return {
        "ready": readiness["can_install"] and installed and running and has_model,
        "can_install": readiness["can_install"],
        "model": target,
        "readiness": readiness,
        "installed": installed,
        "running": running,
        "models": models,
        "has_model": has_model,
        "runtime_source": runtime_source,
        "bundled_runtime_available": bundled_available,
        "bundled_runtime_path": _safe_runtime_path(runtime_source),
        "bundled_models_available": bundled_models_dir is not None,
        "bundled_models_path": str(bundled_models_dir or ""),
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
                "state": "done" if installed else "current",
                "detail": _runtime_setup_detail(installed, bundled_available),
            },
            {
                "key": "server",
                "label": "Start local AI service",
                "state": "done" if running else "current" if installed else "pending",
                "detail": "Ollama is running." if running else "Mavris will start Ollama after installation.",
            },
            {
                "key": "model",
                "label": "Use local model" if has_model else "Use bundled local model" if bundled_model_available else "Download recommended model",
                "state": "done" if has_model else "current" if running else "pending",
                "detail": _model_setup_detail(target, has_model, bundled_model_available, bundled_model_configured),
            },
        ],
        "next_action": _setup_next_action(readiness, installed, running, has_model, bundled_model_available),
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
    return {
        "can_install": can_install,
        "recommended_model": target,
        "reason": reason,
        "checks": checks,
        "memory_total_bytes": memory_total_bytes,
        "disk_free_bytes": disk_free_bytes,
        "cpu_logical_cores": cpu_logical_cores,
        "gpu_summary": gpu_summary,
    }


def is_installed() -> bool:
    """Check if ollama binary is on PATH."""
    return _ollama_executable() is not None


def bundled_runtime_available() -> bool:
    """Return whether Mavris can use an Ollama runtime shipped with the app."""
    return _bundled_ollama_executable() is not None


async def is_running() -> bool:
    """Check if Ollama server is responding."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{OLLAMA_API}/api/tags")
            return resp.status_code == 200
    except Exception:
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
    except Exception:
        return []


async def install() -> dict[str, Any]:
    """Make Ollama available, preferring a Mavris-bundled runtime over winget."""
    source = _ollama_runtime_source()
    if source == "bundled":
        return {
            "ok": True,
            "message": "Bundled Ollama runtime is ready.",
            "source": source,
            "executable": _ollama_executable(),
        }
    if source == "system":
        return {
            "ok": True,
            "message": "Ollama is already installed.",
            "source": source,
            "executable": _ollama_executable(),
        }

    if sys.platform != "win32":
        return {"ok": False, "error": "Auto-install is only supported on Windows."}

    try:
        proc = await asyncio.create_subprocess_exec(
            "winget", "install", "--id", "Ollama.Ollama",
            "--accept-package-agreements", "--accept-source-agreements",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        ok = proc.returncode == 0
        record("ollama.install", "OllamaService", {"ok": ok, "returncode": proc.returncode})
        return {
            "ok": ok,
            "message": stdout.decode(errors="replace").strip() if ok else stderr.decode(errors="replace").strip(),
            "source": "winget",
        }
    except FileNotFoundError:
        return {"ok": False, "error": "winget not found. Please install Ollama manually from https://ollama.com"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Installation timed out after 120 seconds."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def start_server() -> dict[str, Any]:
    """Start Ollama server in the background when the CLI is available."""
    if await is_running():
        return {"ok": True, "message": "Ollama server is already running."}
    if not is_installed():
        return {"ok": False, "error": "Ollama is not installed."}

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        executable = _ollama_executable()
        if not executable:
            return {"ok": False, "error": "Ollama executable not found."}
        env = os.environ.copy()
        models_dir = _preferred_ollama_models_dir()
        if models_dir:
            env["OLLAMA_MODELS"] = str(models_dir)
        subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            env=env,
        )
        record("ollama.start", "OllamaService", {"ok": True, "models_dir": str(models_dir) if models_dir else ""})
        return {"ok": True, "message": "Ollama server is starting.", "models_dir": str(models_dir) if models_dir else ""}
    except Exception as exc:
        record("ollama.start", "OllamaService", {"ok": False, "error": str(exc)})
        return {"ok": False, "error": str(exc)}


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
        value = os.getenv(key)
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
        value = os.getenv(key)
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
        value = os.getenv(key)
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
    except Exception as exc:
        return {"present": True, "valid": False, "path": str(path), "error": str(exc)}
    runtime_summary = data.get("runtime", {}).get("summary", {}) if isinstance(data, dict) else {}
    models_summary = data.get("models", {}).get("summary", {}) if isinstance(data, dict) else {}
    return {
        "present": True,
        "valid": data.get("schema") == 1 and bool(data.get("accepted_licenses")),
        "path": str(path),
        "model": str(data.get("model", "")),
        "accepted_licenses": bool(data.get("accepted_licenses")),
        "runtime_sha256": str(runtime_summary.get("sha256", "")),
        "models_sha256": str(models_summary.get("sha256", "")),
        "runtime_files": int(runtime_summary.get("files", 0) or 0),
        "models_files": int(models_summary.get("files", 0) or 0),
    }


def _preferred_ollama_models_dir() -> Path | None:
    existing = os.getenv("OLLAMA_MODELS")
    if existing:
        return Path(existing).expanduser()
    return _bundled_ollama_models_dir()


def _same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except Exception:
        return str(left.expanduser()).lower() == str(right.expanduser()).lower()


def _bundled_model_configured(model: str) -> bool:
    """Return whether the preferred Ollama model store points at the bundled model."""
    bundled_dir = _bundled_ollama_models_dir()
    return _bundled_model_available(model) and _same_path(_preferred_ollama_models_dir(), bundled_dir)


def _bundled_model_available(model: str) -> bool:
    models_dir = _bundled_ollama_models_dir()
    if not models_dir:
        return False
    name, tag = _ollama_model_name_and_tag(model)
    candidates = [
        models_dir / "manifests" / "registry.ollama.ai" / "library" / name / tag,
    ]
    if "/" in name:
        candidates.append(models_dir / "manifests" / name / tag)
    if any(candidate.exists() and candidate.is_file() for candidate in candidates):
        return True
    manifests_dir = models_dir / "manifests"
    if not manifests_dir.exists():
        return False
    return any(path.name == tag and path.parent.name == name for path in manifests_dir.rglob(tag))


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
        return "Mavris bundled Ollama runtime is available."
    if installed:
        return "Ollama is installed."
    if bundled_available:
        return "Mavris will use the bundled Ollama runtime."
    return "Ollama will be installed automatically on Windows."


def _model_setup_detail(
    target: str,
    has_model: bool,
    bundled_model_available: bool,
    bundled_model_configured: bool = False,
) -> str:
    if has_model:
        return f"{target} is ready."
    if bundled_model_configured:
        return f"{target} is included with Mavris and the local service is configured to read it."
    if bundled_model_available:
        return f"{target} is included with Mavris and will be used when the local service starts."
    return f"{target} will be downloaded for privacy mode."


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
) -> str:
    if not readiness.get("can_install"):
        return "hardware_blocked"
    if not installed:
        return "install_runtime"
    if not running:
        return "start_runtime"
    if bundled_model_available and not has_model:
        return "use_bundled_model"
    if not has_model:
        return "download_model"
    return "ready"


def _total_memory_bytes() -> int:
    if psutil is None:
        return 0
    try:
        return int(psutil.virtual_memory().total)
    except Exception:
        return 0


def _ollama_disk_free_bytes() -> int:
    target = os.getenv("OLLAMA_MODELS") or os.path.expanduser("~/.ollama")
    try:
        existing = target
        while existing and not os.path.exists(existing):
            parent = os.path.dirname(existing)
            if parent == existing:
                break
            existing = parent
        return int(shutil.disk_usage(existing or os.path.expanduser("~")).free)
    except Exception:
        return 0


def _gpu_summary() -> str:
    if sys.platform != "win32":
        return ""
    try:
        proc = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
    except Exception:
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
    target = model or RECOMMENDED_MODEL
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
            return {"ok": False, "model": target, "error": f"Pull failed with status {resp.status_code}"}
    except Exception as exc:
        return {"ok": False, "model": target, "error": str(exc)}


async def pull_model_streaming(model: str | None = None):
    """Pull a model with streaming progress. Yields dicts with progress info."""
    target = model or RECOMMENDED_MODEL
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
                    yield {"status": "error", "error": f"Pull failed with status {resp.status_code}"}
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
                            "status": data.get("status", "downloading"),
                            "total": total,
                            "completed": completed,
                            "percent": pct,
                        }
                    except (json.JSONDecodeError, ZeroDivisionError):
                        continue
        record("ollama.pull_complete", "OllamaService", {"model": target})
        yield {"status": "success", "model": target}
    except Exception as exc:
        yield {"status": "error", "error": str(exc)}


async def install_local_model(model: str | None = None):
    """Full install flow: detect Ollama -> install if needed -> pull model.
    Yields progress dicts for WebSocket streaming."""
    readiness = hardware_readiness(model)
    target = model or str(readiness["recommended_model"])

    if not readiness["can_install"]:
        yield {
            "phase": "hardware",
            "status": "error",
            "error": readiness["reason"],
            "readiness": readiness,
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
            yield {"phase": "install", "status": "error", "error": result.get("error", "Installation failed")}
            return
        yield {"phase": "install", "status": "done", "message": "Ollama installed successfully."}
    else:
        yield {"phase": "install", "status": "skipped", "message": "Ollama already installed."}

    # Step 2: Check if Ollama is running
    running = await is_running()
    started_with_mavris_models = False
    if not running:
        yield {"phase": "start", "status": "starting", "message": "Starting Ollama server..."}
        start_result = await start_server()
        if not start_result.get("ok"):
            yield {
                "phase": "start",
                "status": "error",
                "error": start_result.get("error") or start_result.get("message") or "Ollama server could not be started.",
            }
            return
        started_with_mavris_models = bool(start_result.get("models_dir"))
        yield {"phase": "start", "status": "waiting", "message": "Waiting for Ollama server to start..."}
        for _ in range(10):
            await asyncio.sleep(2)
            if await is_running():
                running = True
                break
        if not running:
            yield {"phase": "start", "status": "error", "error": "Ollama server not responding. Please start it manually."}
            return
    yield {"phase": "start", "status": "done", "message": "Ollama server is running."}

    models = await list_models()
    if _has_model(models, target):
        yield {"phase": "pull", "status": "skipped", "message": f"Local model {target} is already installed.", "model": target}
        yield {"phase": "switch", "status": "done", "message": f"Local model {target} is ready.", "model": target}
        return

    if started_with_mavris_models and _bundled_model_available(target):
        yield {
            "phase": "pull",
            "status": "skipped",
            "message": f"Bundled model {target} is ready; no download is needed.",
            "model": target,
        }
        yield {"phase": "switch", "status": "done", "message": f"Bundled local model {target} is ready.", "model": target}
        return

    if _bundled_model_available(target):
        yield {
            "phase": "pull",
            "status": "error",
            "error": (
                f"Bundled model {target} is available, but the running Ollama service is not using "
                "the Mavris bundled model directory. Stop the existing Ollama service and try again."
            ),
            "model": target,
        }
        return

    # Step 3: Pull model with progress
    pull_succeeded = False
    yield {"phase": "pull", "status": "starting", "model": target}
    async for progress in pull_model_streaming(target):
        yield {"phase": "pull", **progress}
        if progress.get("status") == "error":
            return
        if progress.get("status") == "success":
            pull_succeeded = True

    if not pull_succeeded:
        yield {"phase": "pull", "status": "error", "error": f"Model {target} did not finish downloading."}
        return

    # Step 4: Switch provider
    yield {"phase": "switch", "status": "done", "message": f"Local model {target} is ready.", "model": target}
