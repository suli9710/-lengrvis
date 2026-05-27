"""Ollama lifecycle management — detect, install, pull models."""
from __future__ import annotations

import asyncio
import json
import os
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


async def status() -> dict[str, Any]:
    """Return Ollama installation and runtime status."""
    installed = is_installed()
    readiness = hardware_readiness()
    if not installed:
        return {
            "installed": False,
            "running": False,
            "models": [],
            "recommended_model": RECOMMENDED_MODEL,
            "has_recommended": False,
            "readiness": readiness,
        }
    running = await is_running()
    models = await list_models() if running else []
    return {
        "installed": True,
        "running": running,
        "models": models,
        "recommended_model": RECOMMENDED_MODEL,
        "has_recommended": RECOMMENDED_MODEL in " ".join(models),
        "readiness": readiness,
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
    """Attempt to install Ollama via winget."""
    if is_installed():
        return {"ok": True, "message": "Ollama is already installed."}

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
        subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        record("ollama.start", "OllamaService", {"ok": True})
        return {"ok": True, "message": "Ollama server is starting."}
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
    if not running:
        yield {"phase": "start", "status": "starting", "message": "Starting Ollama server..."}
        await start_server()
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

    # Step 3: Pull model with progress
    yield {"phase": "pull", "status": "starting", "model": target}
    async for progress in pull_model_streaming(target):
        yield {"phase": "pull", **progress}

    # Step 4: Switch provider
    yield {"phase": "switch", "status": "done", "message": f"Local model {target} is ready.", "model": target}
