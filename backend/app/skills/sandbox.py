from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.skills.schemas import SkillExecution, SkillExecutionType


MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 128 * 1024
SENSITIVE_ENV_HINTS = ("api", "auth", "cookie", "credential", "key", "password", "secret", "token")
WINDOWS_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1"}
POSIX_SCRIPT_EXTENSIONS = {".sh"}


class SkillSandboxError(RuntimeError):
    pass


class SkillSandbox:
    """Runs local skill handlers through bounded execution adapters."""

    def __init__(self, skill_root: str | Path) -> None:
        self.skill_root = Path(skill_root).resolve(strict=True)

    def execute(self, execution: SkillExecution, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if execution.type == SkillExecutionType.PYTHON:
            return self._execute_process(self._python_command(execution), execution, args, context)
        if execution.type == SkillExecutionType.SHELL:
            return self._execute_process(self._shell_command(execution), execution, args, context)
        if execution.type == SkillExecutionType.HTTP:
            return self._execute_http(execution, args, context)
        return {"error": f"Unsupported skill execution type: {execution.type}"}

    def resolve_local_entry(self, execution: SkillExecution) -> Path:
        raw = Path(execution.entry)
        if raw.is_absolute():
            raise SkillSandboxError("execution entry must be relative to the skill package")
        if ".." in raw.parts:
            raise SkillSandboxError("execution entry must not contain path traversal")
        candidate = (self.skill_root / raw).resolve(strict=False)
        try:
            candidate.relative_to(self.skill_root)
        except ValueError as exc:
            raise SkillSandboxError("execution entry escapes the skill package") from exc
        if not candidate.exists() or not candidate.is_file():
            raise SkillSandboxError(f"execution entry does not exist: {execution.entry}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self.skill_root)
        except ValueError as exc:
            raise SkillSandboxError("execution entry symlink escapes the skill package") from exc
        return resolved

    def _python_command(self, execution: SkillExecution) -> list[str]:
        entry = self.resolve_local_entry(execution)
        if entry.suffix.lower() != ".py":
            raise SkillSandboxError("python skill execution entry must be a .py file")
        return [sys.executable, "-I", "-B", str(entry)]

    def _shell_command(self, execution: SkillExecution) -> list[str]:
        entry = self.resolve_local_entry(execution)
        suffix = entry.suffix.lower()
        if suffix == ".ps1":
            return [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(entry),
            ]
        if suffix in {".bat", ".cmd"}:
            return ["cmd", "/d", "/c", str(entry)]
        if suffix == ".sh":
            if os.name == "nt":
                return ["bash", str(entry)]
            return ["/bin/sh", str(entry)]
        raise SkillSandboxError("shell skill execution entry must be a .ps1, .cmd, .bat, or .sh file")

    def _execute_process(
        self,
        command: list[str],
        execution: SkillExecution,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._payload(args, context)
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload),
                cwd=str(self.skill_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_sandbox_env(),
                timeout=execution.timeout_seconds,
                shell=False,
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired:
            return {"error": f"Skill handler timed out after {execution.timeout_seconds:g}s.", "timeout_seconds": execution.timeout_seconds}
        except OSError as exc:
            return {"error": f"Skill handler could not start: {exc}"}

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if len(stdout.encode("utf-8", errors="replace")) > MAX_STDOUT_BYTES:
            return {"error": "Skill handler stdout exceeded the sandbox limit."}
        if len(stderr.encode("utf-8", errors="replace")) > MAX_STDERR_BYTES:
            stderr = stderr[:MAX_STDERR_BYTES] + "...<truncated>"
        if completed.returncode != 0:
            return {
                "error": "Skill handler exited with a non-zero status.",
                "return_code": completed.returncode,
                "stderr": stderr.strip(),
            }
        try:
            return _parse_json_output(stdout)
        except SkillSandboxError as exc:
            return {"error": str(exc), "stdout": stdout.strip()[:2000], "stderr": stderr.strip()[:2000]}

    def _execute_http(self, execution: SkillExecution, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        url = execution.entry
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"error": "HTTP skill execution entry must be an absolute http(s) URL."}
        if not is_loopback_http_url(url):
            return {"error": "HTTP skill handlers must use a loopback host."}

        payload = self._payload(args, context)
        try:
            with httpx.Client(timeout=execution.timeout_seconds, follow_redirects=False) as client:
                response = client.request(execution.method, url, json=payload, headers=execution.headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"error": f"HTTP skill handler failed: {exc}"}

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            return {"content": response.text}
        data = response.json()
        return data if isinstance(data, dict) else {"result": data}

    def _payload(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {
            "args": args,
            "context": _safe_context(context),
        }


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    settings = context.get("settings")
    settings_payload: dict[str, Any] = {}
    if settings is not None:
        for key in (
            "mode",
            "network_access",
            "allow_browser_network",
            "allow_cloud_context",
            "allow_file_content_upload",
            "allowed_directories",
            "data_dir",
        ):
            if hasattr(settings, key):
                settings_payload[key] = getattr(settings, key)
    return {
        "allowed_directories": list(context.get("allowed_directories") or []),
        "settings": settings_payload,
    }


def _sandbox_env() -> dict[str, str]:
    env: dict[str, str] = {
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    for key, value in os.environ.items():
        lower = key.lower()
        if key in env or any(hint in lower for hint in SENSITIVE_ENV_HINTS):
            continue
        if key.startswith("MARVIS_SKILL_ENV_"):
            env[key.removeprefix("MARVIS_SKILL_ENV_")] = value
    return env


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def is_loopback_http_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")


def _parse_json_output(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    candidates = [text, *[line.strip() for line in reversed(text.splitlines()) if line.strip()]]
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
        return {"result": data}
    raise SkillSandboxError("Skill handler must write a JSON object to stdout.")
