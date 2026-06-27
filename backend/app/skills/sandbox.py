from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_env
from app.core.process_tree import run_process_tree
from app.skills.schemas import SkillExecution, SkillExecutionType

MAX_STDOUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 128 * 1024
UNSAFE_LOCAL_SKILL_EXECUTION_ENV = "LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION"
SENSITIVE_ENV_HINTS = ("api", "auth", "cookie", "credential", "key", "password", "secret", "token")
WINDOWS_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1"}
POSIX_SCRIPT_EXTENSIONS = {".sh"}


class SkillSandboxError(RuntimeError):
    pass


class SkillSandbox:
    """Runs local skill handlers through bounded execution adapters."""

    def __init__(self, skill_root: str | Path, *, allow_unsafe_local_skill_execution: bool | None = None) -> None:
        self.skill_root = Path(skill_root).resolve(strict=True)
        self.allow_unsafe_local_skill_execution = allow_unsafe_local_skill_execution

    def execute(self, execution: SkillExecution, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if execution.type == SkillExecutionType.PYTHON:
            if not self._local_skill_execution_allowed(context):
                return _local_skill_execution_disabled_error(execution)
            return self._execute_process(self._python_command(execution), execution, args, context)
        if execution.type == SkillExecutionType.SHELL:
            if not self._local_skill_execution_allowed(context):
                return _local_skill_execution_disabled_error(execution)
            return self._execute_process(self._shell_command(execution), execution, args, context)
        if execution.type == SkillExecutionType.HTTP:
            return self._execute_http(execution, args, context)
        return {"error": f"Unsupported skill execution type: {execution.type}"}

    def _local_skill_execution_allowed(self, context: dict[str, Any]) -> bool:
        if _truthy(get_env(UNSAFE_LOCAL_SKILL_EXECUTION_ENV)):
            return True
        if self.allow_unsafe_local_skill_execution is True:
            return True
        if _truthy(context.get("allow_unsafe_local_skill_execution")):
            return True
        settings = context.get("settings")
        return bool(getattr(settings, "allow_unsafe_local_skill_execution", False))

    def resolve_local_entry(self, execution: SkillExecution) -> Path:
        return self.resolve_package_file(execution.entry, label="execution entry")

    def resolve_package_file(self, raw_path: str | Path, *, label: str = "package file") -> Path:
        raw_value = str(raw_path or "").strip()
        if not raw_value:
            raise SkillSandboxError(f"{label} must not be empty")
        if any(char in raw_value for char in ("\x00", "\n", "\r")):
            raise SkillSandboxError(f"{label} must not contain control characters")
        raw = Path(raw_value)
        if raw.is_absolute():
            raise SkillSandboxError(f"{label} must be relative to the skill package")
        if ".." in raw.parts:
            raise SkillSandboxError(f"{label} must not contain path traversal")
        candidate = (self.skill_root / raw).resolve(strict=False)
        try:
            candidate.relative_to(self.skill_root)
        except ValueError as exc:
            raise SkillSandboxError(f"{label} escapes the skill package") from exc
        if not candidate.exists() or not candidate.is_file():
            raise SkillSandboxError(f"{label} does not exist: {raw_value}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(self.skill_root)
        except ValueError as exc:
            raise SkillSandboxError(f"{label} symlink escapes the skill package") from exc
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
            completed = run_process_tree(
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
                hide_window=True,
            )
        except subprocess.TimeoutExpired:
            return {
                "error": f"Skill handler timed out after {execution.timeout_seconds:g}s.",
                "timeout_seconds": execution.timeout_seconds,
            }
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
            "allow_unsafe_local_skill_execution",
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
    # PSMODULEPATH/USERPROFILE/LOCALAPPDATA/APPDATA keep Windows PowerShell
    # handlers working: without them module auto-discovery and the startup
    # cache fall back to slow rediscovery paths that can exceed handler
    # timeouts on cold machines (observed on CI runners).
    for key in (
        "PATH",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PSMODULEPATH",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
    ):
        value = get_env(key)
        if value:
            env[key] = value
    for key, value in os.environ.items():
        lower = key.lower()
        if key in env or any(hint in lower for hint in SENSITIVE_ENV_HINTS):
            continue
        if key.startswith("LENGRVIS_SKILL_ENV_"):
            env[key.removeprefix("LENGRVIS_SKILL_ENV_")] = value
    return env


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _local_skill_execution_disabled_error(execution: SkillExecution) -> dict[str, Any]:
    return {
        "error": (
            f"Local {execution.type.value} skill execution is disabled by default because these handlers run as "
            "normal local subprocesses, not an OS sandbox. Enable only trusted development skills with "
            f"{UNSAFE_LOCAL_SKILL_EXECUTION_ENV}=1 or AppSettings.allow_unsafe_local_skill_execution=True."
        ),
        "policy": "local_skill_execution_disabled",
        "execution_type": execution.type.value,
    }


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
