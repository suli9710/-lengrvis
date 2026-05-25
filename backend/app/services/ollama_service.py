"""Ollama lifecycle management — detect, install, pull models."""
from __future__ import annotations

import asyncio
import json
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
    target = model or RECOMMENDED_MODEL

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
