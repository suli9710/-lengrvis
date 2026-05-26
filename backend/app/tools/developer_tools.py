from __future__ import annotations

import ast
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
COMMAND_STDOUT_LIMIT = 20000
COMMAND_STDERR_LIMIT = 8000
DIFF_PREVIEW_LIMIT = 20000
GIT_CONFIG_GUARDS = [
    "-c",
    "advice.detachedHead=false",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=",
    "-c",
    "diff.external=",
    "-c",
    "diff.trustExitCode=false",
]
GIT_DIFF_GUARD_FLAGS = ["--no-ext-diff", "--no-textconv"]


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
    result = _run_command(_guarded_git_command(["status", "--short", "--branch"]), cwd=root)
    payload = {"ok": result["returncode"] == 0, "cwd": str(root), **result}
    payload["summary"] = _summarize_git_status(payload)
    return payload


def diff_preview(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    result = _run_command(_guarded_git_command(["diff", "--", str(args.get("pathspec") or ".")]), cwd=root)
    diff, diff_truncated = _truncate_text(str(result.get("stdout") or ""), DIFF_PREVIEW_LIMIT)
    payload = {
        "ok": result["returncode"] == 0,
        "cwd": str(root),
        "diff": diff,
        "diff_truncated": diff_truncated or bool(result.get("stdout_truncated")),
        "stderr": result["stderr"],
        "stderr_truncated": result.get("stderr_truncated", False),
    }
    payload["summary"] = _summarize_diff_preview(payload)
    return payload


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
    payload = {"ok": result["returncode"] == 0, "cwd": str(root), "readonly": True, **result}
    payload["summary"] = _summarize_shell_readonly(command, payload)
    return payload


def pytest_inventory(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    pattern = str(args.get("pattern") or "test_*.py")
    limit = max(1, min(int(args.get("limit") or 100), 500))
    test_files: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    test_count = 0

    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern)):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tests = _pytest_tests_from_source(source)
        except (OSError, SyntaxError) as exc:
            errors.append({"path": str(path), "relative_path": rel, "error": str(exc)})
            tests = []
        if tests:
            test_count += len(tests)
            test_files.append({"path": str(path), "relative_path": rel, "tests": tests, "count": len(tests)})
        if len(test_files) >= limit:
            break

    payload = {
        "ok": True,
        "root": str(root),
        "pattern": pattern,
        "test_files": test_files,
        "file_count": len(test_files),
        "test_count": test_count,
        "errors": errors,
        "truncated": len(test_files) >= limit,
    }
    payload["summary"] = _summarize_pytest_inventory(payload)
    return payload


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
        tokens = _guarded_git_command(tokens[1:])
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


def _guarded_git_command(args: list[str]) -> list[str]:
    if not args:
        return ["git", *GIT_CONFIG_GUARDS]
    subcommand = args[0].casefold()
    guarded = ["git", *GIT_CONFIG_GUARDS, args[0], *args[1:]]
    if subcommand in {"diff", "log", "show"}:
        insert_at = len(["git", *GIT_CONFIG_GUARDS, args[0]])
        guarded[insert_at:insert_at] = GIT_DIFF_GUARD_FLAGS
    return guarded


def _run_command(command: list[str] | str, *, cwd: Path, shell: bool = False) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        shell=shell,
        env=_safe_command_env(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    stdout, stdout_truncated = _truncate_text(completed.stdout, COMMAND_STDOUT_LIMIT)
    stderr, stderr_truncated = _truncate_text(completed.stderr, COMMAND_STDERR_LIMIT)
    return {
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def _safe_command_env() -> dict[str, str]:
    import os

    keys = ("COMSPEC", "HOME", "LANG", "LOCALAPPDATA", "PATH", "PATHEXT", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "WINDIR")
    env = {key: value for key in keys if (value := os.environ.get(key))}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return env


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _pytest_tests_from_source(source: str) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    tests: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            tests.append({"name": node.name, "line": node.lineno, "kind": "function"})
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    tests.append({"name": f"{node.name}.{child.name}", "line": child.lineno, "kind": "method"})
    return tests


def _summarize_git_status(payload: dict[str, Any]) -> str:
    if not payload.get("ok", False):
        return f"git status failed: {payload.get('stderr') or 'unknown error'}"
    lines = [line for line in str(payload.get("stdout") or "").splitlines() if line.strip()]
    branch = next((line.removeprefix("##").strip() for line in lines if line.startswith("##")), "")
    changed_count = len([line for line in lines if not line.startswith("##")])
    location = f" on {branch}" if branch else ""
    if changed_count == 0:
        return f"Git status is clean{location}."
    return f"Git status found {changed_count} changed item(s){location}."


def _summarize_diff_preview(payload: dict[str, Any]) -> str:
    if not payload.get("ok", False):
        return f"git diff failed: {payload.get('stderr') or 'unknown error'}"
    diff = str(payload.get("diff") or "")
    file_count = diff.count("diff --git ")
    hunk_count = diff.count("@@")
    truncated = " Truncated." if payload.get("diff_truncated") else ""
    return f"Diff preview captured {len(diff)} char(s), {file_count} file(s), {hunk_count} hunk(s).{truncated}"


def _summarize_shell_readonly(command: str, payload: dict[str, Any]) -> str:
    status = "succeeded" if payload.get("ok", False) else "failed"
    stdout_len = len(str(payload.get("stdout") or ""))
    stderr_len = len(str(payload.get("stderr") or ""))
    truncated = " Truncated." if payload.get("stdout_truncated") or payload.get("stderr_truncated") else ""
    return f"Read-only shell command {status}: {command} ({stdout_len} stdout char(s), {stderr_len} stderr char(s)).{truncated}"


def _summarize_pytest_inventory(payload: dict[str, Any]) -> str:
    error_count = len(payload.get("errors") or [])
    suffix = f" with {error_count} parse error(s)" if error_count else ""
    return f"Static pytest inventory found {payload.get('test_count', 0)} test(s) in {payload.get('file_count', 0)} file(s){suffix}."


def _result_summary(output: dict[str, Any]) -> str:
    summary = output.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    if not output.get("ok", False):
        return str(output.get("error") or output.get("stderr") or "Developer tool failed.")
    return "Developer tool completed."


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
        "dev.pytest_inventory": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}, "limit": {"type": "integer"}},
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
        ("dev.pytest_inventory", pytest_inventory, ["tests", "developer_status"], ["read", "inspect"]),
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
                result_summary=_result_summary,
                search_hint="developer cli grep glob git diff shell read-only pytest test inventory worktree preview",
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
