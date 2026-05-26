from __future__ import annotations

import fnmatch
import shlex
import subprocess
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


READONLY_SHELL_COMMANDS = {
    "dir",
    "echo",
    "findstr",
    "git",
    "ls",
    "pwd",
    "rg",
    "select-string",
    "type",
    "where",
    "whoami",
}
SHELL_WRITE_TOKENS = {
    ">",
    ">>",
    "1>",
    "2>",
    "<",
    "|",
    ";",
    "&",
    "&&",
    "||",
    "del",
    "erase",
    "move",
    "copy",
    "rm",
    "rmdir",
    "mkdir",
    "ni",
    "new-item",
    "set-content",
    "add-content",
    "out-file",
    "remove-item",
    "move-item",
    "copy-item",
    "invoke-webrequest",
    "iwr",
    "curl",
    "wget",
}
SHELL_METACHARS = (">", "<", "|", ";", "&")
READONLY_GIT_SUBCOMMANDS = {"status", "diff", "log", "show"}
GIT_WRITE_FLAGS = {
    "--output",
    "--output-directory",
}


def _allowed(context: dict[str, Any]) -> list[str]:
    return list(context.get("allowed_directories") or [])


def _workspace_root(args: dict[str, Any], context: dict[str, Any]) -> Path:
    raw = str(args.get("path") or args.get("cwd") or "")
    allowed = _allowed(context)
    if raw:
        return resolve_authorized(raw, allowed)
    if allowed:
        return resolve_authorized(allowed[0], allowed)
    raise SecurityError("No authorized directories configured.")


def glob_files(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    pattern = str(args.get("pattern") or "*")
    limit = max(1, min(int(args.get("limit") or 100), 500))
    matches: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
            matches.append({"path": str(path), "relative_path": rel, "is_dir": path.is_dir()})
        if len(matches) >= limit:
            break
    return {"ok": True, "root": str(root), "pattern": pattern, "matches": matches, "count": len(matches)}


def grep_files(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    query = str(args.get("query") or "")
    pattern = str(args.get("pattern") or "*")
    limit = max(1, min(int(args.get("limit") or 100), 500))
    case_sensitive = bool(args.get("case_sensitive", False))
    needle = query if case_sensitive else query.casefold()
    results: list[dict[str, Any]] = []
    if not query:
        return {"ok": False, "error": "Missing query.", "results": []}
    for path in root.rglob("*"):
        if not path.is_file() or not (fnmatch.fnmatch(path.relative_to(root).as_posix(), pattern) or fnmatch.fnmatch(path.name, pattern)):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            haystack = line if case_sensitive else line.casefold()
            if needle in haystack:
                results.append(
                    {
                        "path": str(path),
                        "relative_path": path.relative_to(root).as_posix(),
                        "line": line_number,
                        "text": line[:500],
                    }
                )
                if len(results) >= limit:
                    return {"ok": True, "root": str(root), "query": query, "results": results, "count": len(results)}
    return {"ok": True, "root": str(root), "query": query, "results": results, "count": len(results)}


def git_status(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    result = _run_command(["git", "status", "--short", "--branch"], cwd=root)
    return {"ok": result["returncode"] == 0, "cwd": str(root), **result}


def diff_preview(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    result = _run_command(["git", "diff", "--", str(args.get("pathspec") or ".")], cwd=root)
    return {"ok": result["returncode"] == 0, "cwd": str(root), "diff": result["stdout"][:20000], "stderr": result["stderr"]}


def shell_readonly(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "Missing command."}
    tokens, reason = _parse_readonly_shell(command, allowed_directories=_allowed(context))
    if tokens is None:
        return {"ok": False, "error": reason, "readonly": False}
    try:
        root = _workspace_root(args, context)
    except SecurityError as exc:
        return {"ok": False, "error": str(exc), "readonly": False}
    result = _run_command(tokens, cwd=root, shell=False)
    return {"ok": result["returncode"] == 0, "cwd": str(root), "readonly": True, **result}


def worktree_preview(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    name = str(args.get("name") or "mavris-worktree").strip()
    branch = str(args.get("branch") or f"codex/{name}").strip()
    target = resolve_authorized(args.get("target_path") or root / ".worktrees" / name, _allowed(context))
    return {
        "ok": True,
        "dry_run": True,
        "cwd": str(root),
        "branch": branch,
        "target_path": str(target),
        "commands": [
            f"git worktree add {shlex.quote(str(target))} -b {shlex.quote(branch)}",
            f"git worktree remove {shlex.quote(str(target))}",
        ],
    }


def validate_readonly_shell(command: str, *, allowed_directories: list[str] | None = None) -> tuple[bool, str]:
    tokens, reason = _parse_readonly_shell(command, allowed_directories=allowed_directories)
    return (tokens is not None, reason)


def _parse_readonly_shell(command: str, *, allowed_directories: list[str] | None = None) -> tuple[list[str] | None, str]:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        return None, f"Could not parse command: {exc}"
    if not tokens:
        return None, "Missing command."
    tokens = [_strip_matching_quotes(token) for token in tokens]
    lowered = [token.casefold() for token in tokens]
    executable = Path(lowered[0]).name
    if executable not in READONLY_SHELL_COMMANDS:
        return None, f"Command '{tokens[0]}' is not in the read-only allowlist."
    if any(token in SHELL_WRITE_TOKENS or any(char in token for char in SHELL_METACHARS) for token in lowered):
        return None, "Command contains a write-like shell token."
    path_error = _shell_path_error(tokens, allowed_directories or [])
    if path_error:
        return None, path_error
    if executable == "git":
        if len(lowered) <= 1:
            return None, "git requires a read-only subcommand."
        if lowered[1] not in READONLY_GIT_SUBCOMMANDS:
            return None, f"git {tokens[1]} is not read-only allowlisted."
        git_flag_error = _git_readonly_flag_error(lowered[2:])
        if git_flag_error:
            return None, git_flag_error
    return tokens, ""


def _strip_matching_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def _shell_path_error(tokens: list[str], allowed_directories: list[str]) -> str:
    for token in tokens[1:]:
        text = token.strip().strip("\"'")
        if not text or text.startswith("-"):
            continue
        path = Path(text)
        if ".." in path.parts:
            return "Command path arguments may not contain '..'."
        if not path.is_absolute():
            continue
        if not allowed_directories:
            return "Absolute shell path arguments require configured allowed_directories."
        try:
            resolve_authorized(path, allowed_directories)
        except Exception as exc:  # noqa: BLE001
            return f"Shell path argument is outside authorized directories: {exc}"
    return ""


def _git_readonly_flag_error(args: list[str]) -> str:
    for token in args:
        flag = token.split("=", 1)[0]
        if flag in GIT_WRITE_FLAGS:
            return f"git option {flag} can write files and is not read-only allowlisted."
    return ""


def _run_command(command: list[str] | str, *, cwd: Path, shell: bool = False) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=shell,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[:20000],
        "stderr": completed.stderr[:8000],
    }


def _schema(name: str) -> dict[str, Any]:
    schemas = {
        "dev.glob": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["pattern"],
            "additionalProperties": False,
        },
        "dev.grep": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "query": {"type": "string"},
                "pattern": {"type": "string"},
                "limit": {"type": "integer"},
                "case_sensitive": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "dev.git_status": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}},
            "additionalProperties": False,
        },
        "dev.diff_preview": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "pathspec": {"type": "string"}},
            "additionalProperties": False,
        },
        "dev.shell_readonly": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "dev.worktree_preview": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}, "name": {"type": "string"}, "branch": {"type": "string"}, "target_path": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    return schemas[name]


def register(registry) -> None:
    defs = [
        ("dev.glob", glob_files, ["filesystem", "developer_search"], ["read", "list", "search"]),
        ("dev.grep", grep_files, ["filesystem", "developer_search"], ["read", "search"]),
        ("dev.git_status", git_status, ["git", "developer_status"], ["read", "inspect"]),
        ("dev.diff_preview", diff_preview, ["git", "developer_status"], ["read", "inspect"]),
        ("dev.shell_readonly", shell_readonly, ["shell", "developer_status"], ["read", "inspect"]),
        ("dev.worktree_preview", worktree_preview, ["git", "worktree"], ["preview"]),
    ]
    for name, execute, capabilities, effects in defs:
        registry.register(
            ToolDefinition(
                name=name,
                description=name.replace(".", " "),
                input_schema=_schema(name),
                output_schema={"type": "object"},
                risk_level=RiskLevel.R0_READ_ONLY,
                agent_owner="ComputerAgent",
                supports_dry_run=False,
                requires_authorized_path=name not in {"dev.git_status", "dev.diff_preview", "dev.shell_readonly"},
                execute=execute,
                permission_mode="auto_readonly",
                read_only=True,
                concurrency_safe=True,
                search_hint="developer cli grep glob git diff shell read-only worktree preview",
                ui_summary=f"{name} developer tool",
                capabilities=capabilities,
                effects=effects,
                resource_kinds=["workspace", "repository"],
                fast_path_eligible=True,
                trust_tier="builtin",
                origin="builtin",
                max_result_size=24000,
            )
        )
