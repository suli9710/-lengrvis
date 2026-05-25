"""Ollama lifecycle management — detect, install, pull models."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from typing import Any

import httpx

from app.core.audit import record

OLLAMA_API = "http://127.0.0.1:11434"
RECOMMENDED_MODEL = "qwen2.5:3b-instruct"
_TIMEOUT = 5.0


async def status() -> dict[str, Any]:
    """Return Ollama installation and runtime status."""
    installed = is_installed()
    if not installed:
        return {"installed": False, "running": False, "models": []}
    running = await is_running()
    models = await list_models() if running else []
    return {
        "installed": True,
        "running": running,
        "models": models,
        "recommended_model": RECOMMENDED_MODEL,
        "has_recommended": RECOMMENDED_MODEL in " ".join(models),
    }


def is_installed() -> bool:
    """Check if ollama binary is on PATH."""
    return shutil.which("ollama") is not None


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
