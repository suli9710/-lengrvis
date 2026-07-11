from __future__ import annotations

import ast
import fnmatch
import shlex
import shutil
import subprocess
import time
from pathlib import Path, PureWindowsPath
from typing import Any

from app.config import DEFAULT_DATA_DIR
from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.core.process_tree import ProcessCancelledError, run_process_tree
from app.core.subprocess_output import decode_process_output
from app.orchestration.background_tasks import background_task_status, start_background_process
from app.policy.risk import RiskLevel
from app.security.execution_isolation import arbitrary_execution_denial
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import ToolAbortedError, raise_if_tool_aborted, tool_abort_event
from app.tools.tool_catalog import tool_description, tool_search_hint

READONLY_SHELL_COMMANDS = {
    "dir",
    "echo",
    "findstr",
    "git",
    "ls",
    "pwd",
    "select-string",
    "type",
    "where",
    "whoami",
}
LOCAL_READONLY_BUILTINS = {"dir", "echo", "type"}
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
TEST_OUTPUT_PREVIEW_LIMIT = 12000
TEST_TIMEOUT_DEFAULT_SECONDS = 120
TEST_TIMEOUT_MAX_SECONDS = 1800
TEST_FOREGROUND_TIMEOUT_MAX_SECONDS = 300
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
TEST_EXECUTABLES = {
    "pytest",
    "pytest.exe",
    "python",
    "python.exe",
    "py",
    "py.exe",
    "npm",
    "npm.cmd",
    "pnpm",
    "pnpm.cmd",
}
PYTEST_WRITE_FLAGS = {
    "--basetemp",
    "--cache-clear-output",
    "--cov-report",
    "--html",
    "--junit-xml",
    "--junitxml",
    "--log-file",
    "--result-log",
    "--self-contained-html",
}
TEST_WATCH_FLAGS = {"--looponfail", "--watch", "--watch-all", "--watchall", "-f", "-w"}


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


def _authorized_rglob_paths(
    root: Path,
    allowed: list[str],
    *,
    pattern: str = "*",
    files_only: bool = False,
) -> list[Path]:
    """Walk under root while rejecting symlink escapes outside the workspace."""
    root_resolved = root.resolve()
    matches: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern)):
            continue
        if files_only and not path.is_file():
            continue
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root_resolved):
                continue
            authorized = resolve_authorized(str(path), allowed)
        except (OSError, SecurityError, ValueError):
            continue
        matches.append(authorized)
    return matches


def glob_files(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    pattern = str(args.get("pattern") or "*")
    limit = max(1, min(int(args.get("limit") or 100), 500))
    matches: list[dict[str, Any]] = []
    for path in _authorized_rglob_paths(root, _allowed(context), pattern=pattern):
        rel = path.relative_to(root).as_posix()
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
    for path in _authorized_rglob_paths(root, _allowed(context), pattern=pattern, files_only=True):
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
    command, error = _trusted_guarded_git_command(
        ["status", "--short", "--branch"],
        root=root,
        allowed_directories=_allowed(context),
    )
    if command is None:
        return {"ok": False, "cwd": str(root), "error": error}
    result = _run_command(command, cwd=root, abort_context=context)
    payload = {"ok": result["returncode"] == 0, "cwd": str(root), **result}
    payload["summary"] = _summarize_git_status(payload)
    return payload


def diff_preview(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    root = _workspace_root(args, context)
    pathspec = str(args.get("pathspec") or ".")
    pathspec_error = _path_candidate_error(pathspec, _allowed(context), root=root)
    if pathspec_error:
        return {"ok": False, "cwd": str(root), "error": pathspec_error}
    command, error = _trusted_guarded_git_command(
        ["diff", "--", pathspec],
        root=root,
        allowed_directories=_allowed(context),
    )
    if command is None:
        return {"ok": False, "cwd": str(root), "error": error}
    result = _run_command(command, cwd=root, abort_context=context)
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
    allowed_directories = _allowed(context)
    try:
        root = _workspace_root(args, context)
    except SecurityError as exc:
        return {"ok": False, "error": str(exc), "readonly": False}
    tokens, reason = _parse_readonly_shell(command, allowed_directories=allowed_directories, root=root)
    if tokens is None:
        return {"ok": False, "error": reason, "readonly": False}
    result = _run_local_readonly_builtin(tokens, root, allowed_directories)
    if result is None:
        tokens, reason = _trusted_readonly_external_command(
            tokens,
            root=root,
            allowed_directories=allowed_directories,
        )
        if tokens is None:
            return {"ok": False, "error": reason, "readonly": False}
        result = _run_command(tokens, cwd=root, shell=False, abort_context=context)
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

    for path in _authorized_rglob_paths(root, _allowed(context), pattern=pattern, files_only=True):
        rel = path.relative_to(root).as_posix()
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
    name = str(args.get("name") or "lengrvis-worktree").strip()
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


def test_run(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "Missing command."}
    try:
        root = _workspace_root(args, context)
    except SecurityError as exc:
        return {"ok": False, "error": str(exc), "controlled": False}
    tokens, reason = _parse_test_command(command, allowed_directories=_allowed(context), root=root)
    if tokens is None:
        return {"ok": False, "error": reason, "controlled": False}

    timeout_seconds = _bounded_timeout(args.get("timeout_seconds"), background=bool(args.get("background", False)))
    output_dir = _test_output_dir(context)
    if args.get("dry_run", False):
        return _test_run_dry_run_preview(tokens, cwd=root, command_text=command, timeout_seconds=timeout_seconds)
    isolation_denial = arbitrary_execution_denial("Python/Node developer test execution")
    if isolation_denial is not None:
        return {
            "ok": False,
            "controlled": False,
            "background": bool(args.get("background", False)),
            "cwd": str(root),
            **isolation_denial,
        }
    if args.get("background", False):
        raise_if_tool_aborted(context)
        try:
            task = start_background_process(
                tokens,
                cwd=root,
                env=_safe_command_env(),
                output_dir=output_dir,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            return {"ok": False, "error": str(exc), "controlled": True, "background": True}
        snapshot = task.snapshot(preview_chars=_preview_limit(args))
        return {
            "ok": True,
            "controlled": True,
            "background": True,
            "cwd": str(root),
            **snapshot,
            "summary": f"Started background test run {task.id}: {command}",
        }

    raise_if_tool_aborted(context)
    return _run_test_foreground(
        tokens,
        cwd=root,
        command_text=command,
        timeout_seconds=timeout_seconds,
        output_dir=output_dir,
        preview_chars=_preview_limit(args),
        abort_context=context,
    )


def test_status(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "Missing task_id."}
    payload = background_task_status(task_id)
    if payload.get("ok"):
        payload["summary"] = f"Background test run {task_id} is {payload.get('status')}."
    return payload


def validate_readonly_shell(command: str, *, allowed_directories: list[str] | None = None) -> tuple[bool, str]:
    tokens, reason = _parse_readonly_shell(command, allowed_directories=allowed_directories)
    return (tokens is not None, reason)


def _parse_readonly_shell(
    command: str, *, allowed_directories: list[str] | None = None, root: Path | None = None
) -> tuple[list[str] | None, str]:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        return None, f"Could not parse command: {exc}"
    if not tokens:
        return None, "Missing command."
    tokens = [_strip_matching_quotes(token) for token in tokens]
    lowered = [token.casefold() for token in tokens]
    executable = _readonly_command_key(tokens[0])
    if executable not in READONLY_SHELL_COMMANDS:
        return None, f"Command '{tokens[0]}' is not in the read-only allowlist."
    if any(token in SHELL_WRITE_TOKENS or any(char in token for char in SHELL_METACHARS) for token in lowered):
        return None, "Command contains a write-like shell token."
    path_error = _shell_path_error(tokens, allowed_directories or [], root=root)
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


def _run_local_readonly_builtin(tokens: list[str], root: Path, allowed_directories: list[str]) -> dict[str, Any] | None:
    executable = _readonly_command_key(tokens[0])
    if executable not in LOCAL_READONLY_BUILTINS:
        return None
    try:
        if executable == "echo":
            return _command_result(0, " ".join(tokens[1:]) + ("\n" if len(tokens) > 1 else "\n"), "")
        if executable == "dir":
            target = _builtin_path_arg(tokens, root, allowed_directories, default=root)
            if not target.exists():
                return _command_result(1, "", f"Path does not exist: {target}")
            if target.is_file():
                return _command_result(0, f"{target.name}\n", "")
            entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
            lines = [f"{item.name}{'/' if item.is_dir() else ''}" for item in entries]
            return _command_result(0, "\n".join(lines) + ("\n" if lines else ""), "")
        if executable == "type":
            if len(tokens) < 2:
                return _command_result(1, "", "type requires a file path.")
            output: list[str] = []
            for raw_path in tokens[1:]:
                target = _builtin_path_arg([tokens[0], raw_path], root, allowed_directories)
                if not target.exists():
                    return _command_result(1, "", f"Path does not exist: {target}")
                if not target.is_file():
                    return _command_result(1, "", f"Path is not a file: {target}")
                output.append(target.read_text(encoding="utf-8", errors="replace"))
            return _command_result(0, "".join(output), "")
    except SecurityError as exc:
        return _command_result(1, "", str(exc))
    except OSError as exc:
        return _command_result(1, "", str(exc))
    return None


def _builtin_path_arg(
    tokens: list[str],
    root: Path,
    allowed_directories: list[str],
    *,
    default: Path | None = None,
) -> Path:
    if len(tokens) <= 1:
        if default is None:
            raise SecurityError("Missing path argument.")
        return default
    if len(tokens) > 2:
        raise SecurityError(f"{tokens[0]} accepts at most one path argument.")
    raw = _strip_matching_quotes(tokens[1])
    path = Path(raw)
    if raw.startswith("-") or (raw.startswith("/") and not path.is_absolute()):
        raise SecurityError(f"{tokens[0]} options are not supported by read-only built-in execution.")
    candidate = path if path.is_absolute() else root / path
    return resolve_authorized(candidate, allowed_directories or [str(root)])


def _command_result(returncode: int, stdout: str, stderr: str) -> dict[str, Any]:
    stdout, stdout_truncated = _truncate_text(stdout, COMMAND_STDOUT_LIMIT)
    stderr, stderr_truncated = _truncate_text(stderr, COMMAND_STDERR_LIMIT)
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def validate_test_command(command: str, *, allowed_directories: list[str] | None = None) -> tuple[bool, str]:
    tokens, reason = _parse_test_command(command, allowed_directories=allowed_directories)
    return (tokens is not None, reason)


def _parse_test_command(
    command: str, *, allowed_directories: list[str] | None = None, root: Path | None = None
) -> tuple[list[str] | None, str]:
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        return None, f"Could not parse command: {exc}"
    if not tokens:
        return None, "Missing command."
    tokens = [_strip_matching_quotes(token) for token in tokens]
    lowered = [token.casefold() for token in tokens]
    executable = Path(lowered[0]).name
    if executable not in TEST_EXECUTABLES:
        return None, f"Command '{tokens[0]}' is not in the controlled test allowlist."
    if any(token in SHELL_WRITE_TOKENS or any(char in token for char in SHELL_METACHARS) for token in lowered):
        return None, "Test command contains a write-like shell token."
    if any(token in TEST_WATCH_FLAGS for token in lowered):
        return None, "Watch/looping test modes are not allowed."
    path_error = _shell_path_error(tokens, allowed_directories or [], root=root)
    if path_error:
        return None, path_error
    shape_error = _test_command_shape_error(lowered)
    if shape_error:
        return None, shape_error
    flag_error = _test_flag_error(lowered)
    if flag_error:
        return None, flag_error
    return tokens, ""


def _test_command_shape_error(lowered: list[str]) -> str:
    executable = Path(lowered[0]).name
    if executable in {"pytest", "pytest.exe"}:
        return ""
    if executable in {"python", "python.exe", "py", "py.exe"}:
        if len(lowered) >= 3 and lowered[1] == "-m" and lowered[2] == "pytest":
            return ""
        return "Python test commands must use 'python -m pytest'."
    if executable in {"npm", "npm.cmd", "pnpm", "pnpm.cmd"}:
        if len(lowered) >= 2 and lowered[1] == "test":
            return ""
        if len(lowered) >= 3 and lowered[1] == "run" and lowered[2] == "test":
            return ""
        return "Package-manager test commands must use 'test' or 'run test'."
    return "Unsupported test command."


def _test_flag_error(lowered: list[str]) -> str:
    for index, token in enumerate(lowered):
        flag = token.split("=", 1)[0]
        if flag in PYTEST_WRITE_FLAGS:
            return f"pytest option {flag} writes files and is not allowed through dev.test_run."
        if _pytest_override_writes(token):
            return "pytest -o/--override-ini may not set addopts or file-writing options through dev.test_run."
        if token in {"-o", "--override-ini"} and index + 1 < len(lowered):
            value = lowered[index + 1]
            if _pytest_override_value_writes(value):
                return "pytest -o/--override-ini may not set addopts or file-writing options through dev.test_run."
    return ""


def _pytest_override_writes(token: str) -> bool:
    for prefix in ("--override-ini=", "-o="):
        if token.startswith(prefix):
            return _pytest_override_value_writes(token[len(prefix) :])
    if token.startswith("-o") and len(token) > 2:
        return _pytest_override_value_writes(token[2:])
    return False


def _pytest_override_value_writes(value: str) -> bool:
    # ``addopts`` injects arbitrary extra CLI options (e.g. --rootdir / -p / -c),
    # which would re-open the path-sandbox bypass that _shell_path_error closes,
    # so it is rejected outright alongside ini keys that cause file writes.
    return any(item in value for item in ("cache_dir=", "junit", "log_file", "addopts="))


def _strip_matching_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


# Short option flags whose value may be attached directly to the flag token
# (e.g. ``-cFILE``). pytest's ``-c`` selects a config file (and thus an
# autoloaded ``conftest.py``); npm/git ``-C`` selects a working directory.
_SHORT_PATH_VALUE_FLAGS = ("-c", "-C")


def _flag_path_value(token: str) -> str:
    """Return a candidate path carried *inside* a single ``-`` flag token.

    Handles ``--flag=VALUE`` / ``-c=VALUE`` (value after ``=``) and the attached
    short form ``-cVALUE``. Space-separated flag values are a separate token and
    are validated as positional arguments by :func:`_shell_path_error`. This
    closes the bypass where ``pytest --rootdir=C:\\outside`` or
    ``pytest --rootdir=..\\evil`` smuggled an out-of-sandbox path (and thus an
    attacker-controlled ``conftest.py`` → code execution) through a token that
    the old check skipped purely because it started with ``-``.
    """
    _, sep, inline = token.partition("=")
    if sep:
        return inline
    for short in _SHORT_PATH_VALUE_FLAGS:
        if token.startswith(short) and len(token) > len(short):
            return token[len(short) :]
    return ""


def _resolve_quietly(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False)
    except OSError:
        return None


def _relative_symlink_escape_error(relative: Path, root: Path | None) -> str:
    """Reject a relative path whose ancestors symlink/junction out of ``root``.

    Containment-only (no sensitive/system-path checks) so legitimate filenames
    that merely contain words like ``password`` or ``.env`` are not rejected;
    we only care whether the path still resolves inside the authorized
    workspace the subprocess runs in.
    """
    if root is None:
        return ""
    resolved = _resolve_quietly(root / relative)
    root_resolved = _resolve_quietly(root)
    if resolved is None or root_resolved is None:
        return ""
    try:
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return ""
    except ValueError:
        pass
    return "Command path argument resolves outside the authorized workspace (symlink/junction)."


def _path_candidate_error(value: str, allowed_directories: list[str], *, root: Path | None = None) -> str:
    text = _strip_matching_quotes(value.strip())
    if not text:
        return ""
    path = Path(text)
    if _has_parent_path_part(text):
        return "Command path arguments may not contain '..'."
    if path.is_absolute() or _is_windows_absolute_path(text):
        if not allowed_directories:
            return "Absolute shell path arguments require configured allowed_directories."
        try:
            resolve_authorized(path, allowed_directories)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            return f"Shell path argument is outside authorized directories: {exc}"
        return ""
    # Relative paths without ``..`` stay inside the subprocess cwd (the
    # authorized workspace root) unless a symlink/junction ancestor redirects
    # them outside it; the containment check below catches that case.
    return _relative_symlink_escape_error(path, root)


def _has_parent_path_part(text: str) -> bool:
    return any(part == ".." for part in text.replace("\\", "/").split("/"))


def _is_windows_absolute_path(text: str) -> bool:
    parsed = PureWindowsPath(text)
    return bool(parsed.drive and parsed.root)


def _shell_path_error(tokens: list[str], allowed_directories: list[str], *, root: Path | None = None) -> str:
    for token in tokens[1:]:
        text = _strip_matching_quotes(token.strip())
        if not text:
            continue
        candidate = _flag_path_value(text) if text.startswith("-") else text
        error = _path_candidate_error(candidate, allowed_directories, root=root)
        if error:
            return error
    return ""


def _git_readonly_flag_error(args: list[str]) -> str:
    for token in args:
        flag = token.split("=", 1)[0]
        if flag in GIT_WRITE_FLAGS:
            return f"git option {flag} can write files and is not read-only allowlisted."
    return ""


def _guarded_git_command(args: list[str], *, git_executable: str = "git") -> list[str]:
    if not args:
        return [git_executable, *GIT_CONFIG_GUARDS]
    subcommand = args[0].casefold()
    guarded = [git_executable, *GIT_CONFIG_GUARDS, args[0], *args[1:]]
    if subcommand in {"diff", "log", "show"}:
        insert_at = len([git_executable, *GIT_CONFIG_GUARDS, args[0]])
        guarded[insert_at:insert_at] = GIT_DIFF_GUARD_FLAGS
    return guarded


def _trusted_guarded_git_command(
    args: list[str],
    *,
    root: Path,
    allowed_directories: list[str],
) -> tuple[list[str] | None, str]:
    git_executable, error = _trusted_external_executable(
        "git",
        root=root,
        allowed_directories=allowed_directories,
    )
    if git_executable is None:
        return None, error
    return _guarded_git_command(args, git_executable=git_executable), ""


def _trusted_readonly_external_command(
    tokens: list[str],
    *,
    root: Path,
    allowed_directories: list[str],
) -> tuple[list[str] | None, str]:
    if not tokens:
        return None, "Missing command."
    command_key = _readonly_command_key(tokens[0])
    executable, error = _trusted_external_executable(
        command_key,
        root=root,
        allowed_directories=allowed_directories,
    )
    if executable is None:
        return None, error
    return [executable, *tokens[1:]], ""


def _trusted_external_executable(
    command_name: str,
    *,
    root: Path,
    allowed_directories: list[str],
) -> tuple[str | None, str]:
    command_key = _readonly_command_key(command_name)
    if command_key not in READONLY_SHELL_COMMANDS:
        return None, f"Command '{command_name}' is not in the read-only allowlist."
    env = _safe_command_env()
    search_path = env.get("PATH") or ""
    resolved = shutil.which(command_key, path=search_path)
    if not resolved:
        return None, f"Trusted executable for '{command_key}' was not found on PATH."
    resolved_path = Path(resolved).expanduser().resolve(strict=False)
    if _path_is_under_any(resolved_path, _trusted_executable_blocked_roots(root, allowed_directories)):
        return None, f"Trusted executable for '{command_key}' resolves inside an authorized workspace."
    return str(resolved_path), ""


def _trusted_executable_blocked_roots(root: Path, allowed_directories: list[str]) -> list[Path]:
    roots = [root, *(Path(raw) for raw in allowed_directories or [])]
    blocked: list[Path] = []
    seen: set[str] = set()
    for raw_root in roots:
        resolved = raw_root.expanduser().resolve(strict=False)
        if resolved.parent == resolved:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        blocked.append(resolved)
    return blocked


def _path_is_under_any(path: Path, roots: list[Path]) -> bool:
    normalized = path.resolve(strict=False)
    for root in roots:
        try:
            if normalized == root or normalized.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def _readonly_command_key(value: str) -> str:
    name = _command_token_name(value).casefold()
    for suffix in (".exe", ".cmd", ".bat", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _command_token_name(value: str) -> str:
    text = _strip_matching_quotes(str(value or "").strip())
    if "\\" in text or ":" in text:
        return PureWindowsPath(text).name
    return Path(text).name


def _run_command(
    command: list[str] | str,
    *,
    cwd: Path,
    shell: bool = False,
    abort_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        raise_if_tool_aborted(abort_context)
        completed = run_process_tree(
            command,
            cwd=str(cwd),
            shell=shell,
            env=_safe_command_env(),
            capture_output=True,
            timeout=15,
            check=False,
            cancel_event=tool_abort_event(abort_context),
        )
    except ProcessCancelledError:
        raise ToolAbortedError("Tool execution was cancelled.") from None
    except OSError as exc:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
    stdout, stdout_truncated = _truncate_text(decode_process_output(completed.stdout), COMMAND_STDOUT_LIMIT)
    stderr, stderr_truncated = _truncate_text(decode_process_output(completed.stderr), COMMAND_STDERR_LIMIT)
    return {
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def _run_test_foreground(
    command: list[str],
    *,
    cwd: Path,
    command_text: str,
    timeout_seconds: int,
    output_dir: Path,
    preview_chars: int,
    abort_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = time.time()
    try:
        raise_if_tool_aborted(abort_context)
        completed = run_process_tree(
            command,
            cwd=str(cwd),
            shell=False,
            env=_safe_command_env(),
            capture_output=True,
            timeout=min(timeout_seconds, TEST_FOREGROUND_TIMEOUT_MAX_SECONDS),
            check=False,
            cancel_event=tool_abort_event(abort_context),
        )
        stdout = decode_process_output(completed.stdout)
        stderr = decode_process_output(completed.stderr)
        returncode = completed.returncode
        timed_out = False
        error = ""
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        returncode = None
        timed_out = True
        error = f"Test run exceeded {min(timeout_seconds, TEST_FOREGROUND_TIMEOUT_MAX_SECONDS)}s timeout."
    except ProcessCancelledError:
        raise ToolAbortedError("Tool execution was cancelled.") from None

    raise_if_tool_aborted(abort_context)
    stdout_path, stderr_path = _persist_test_output(output_dir, stdout, stderr, abort_context=abort_context)
    stdout_preview, stdout_truncated = _truncate_text(stdout, preview_chars)
    stderr_preview, stderr_truncated = _truncate_text(stderr, preview_chars)
    ok = returncode == 0 and not timed_out
    payload = {
        "ok": ok,
        "controlled": True,
        "background": False,
        "cwd": str(cwd),
        "command": command,
        "command_text": command_text,
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "started_at": started_at,
        "completed_at": time.time(),
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
    }
    payload["summary"] = _summarize_test_run(payload)
    return payload


def _safe_command_env() -> dict[str, str]:
    from app.config import get_env

    keys = (
        "COMSPEC",
        "HOME",
        "LANG",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )
    env = {key: value for key in keys if (value := get_env(key))}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        }
    )
    return env


def _test_output_dir(context: dict[str, Any]) -> Path:
    settings = context.get("settings")
    raw = getattr(settings, "data_dir", "") if settings is not None else ""
    root = Path(raw) if raw else DEFAULT_DATA_DIR
    return root / "developer_test_runs"


def _persist_test_output(
    output_dir: Path,
    stdout: str,
    stderr: str,
    *,
    abort_context: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    raise_if_tool_aborted(abort_context)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"testrun_{int(time.time() * 1000)}"
    stdout_path = output_dir / f"{stem}.stdout.log"
    stderr_path = output_dir / f"{stem}.stderr.log"
    raise_if_tool_aborted(abort_context)
    stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
    raise_if_tool_aborted(abort_context)
    stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    return stdout_path, stderr_path


def _decode_timeout_output(value: Any) -> str:
    return decode_process_output(value)


def _test_run_dry_run_preview(
    command: list[str],
    *,
    cwd: Path,
    command_text: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "ok": True,
        "controlled": True,
        "background": False,
        "dry_run": True,
        "cwd": str(cwd),
        "command": command,
        "command_text": command_text,
        "timeout_seconds": timeout_seconds,
        "would_execute": True,
        "summary": f"Dry-run preview: controlled test command would run for up to {timeout_seconds}s.",
    }


def _bounded_timeout(value: Any, *, background: bool) -> int:
    try:
        seconds = int(value or TEST_TIMEOUT_DEFAULT_SECONDS)
    except (TypeError, ValueError):
        seconds = TEST_TIMEOUT_DEFAULT_SECONDS
    limit = TEST_TIMEOUT_MAX_SECONDS if background else TEST_FOREGROUND_TIMEOUT_MAX_SECONDS
    return max(1, min(seconds, limit))


def _preview_limit(args: dict[str, Any]) -> int:
    try:
        return max(1000, min(int(args.get("max_output_chars") or TEST_OUTPUT_PREVIEW_LIMIT), 60000))
    except (TypeError, ValueError):
        return TEST_OUTPUT_PREVIEW_LIMIT


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _pytest_tests_from_source(source: str) -> list[dict[str, Any]]:
    tree = ast.parse(source)
    tests: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
            tests.append({"name": node.name, "line": node.lineno, "kind": "function"})
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith("test_"):
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
    return (
        f"Read-only shell command {status}: {command} "
        f"({stdout_len} stdout char(s), {stderr_len} stderr char(s)).{truncated}"
    )


def _summarize_pytest_inventory(payload: dict[str, Any]) -> str:
    error_count = len(payload.get("errors") or [])
    suffix = f" with {error_count} parse error(s)" if error_count else ""
    return (
        "Static pytest inventory found "
        f"{payload.get('test_count', 0)} test(s) in {payload.get('file_count', 0)} file(s){suffix}."
    )


def _summarize_test_run(payload: dict[str, Any]) -> str:
    if payload.get("timed_out"):
        return f"Test run timed out: {payload.get('command_text') or payload.get('command')}."
    status = "passed" if payload.get("ok") else "failed"
    stdout_len = int(payload.get("stdout_bytes") or 0)
    stderr_len = int(payload.get("stderr_bytes") or 0)
    truncated = " Preview truncated." if payload.get("stdout_truncated") or payload.get("stderr_truncated") else ""
    return (
        f"Controlled test run {status} with return code {payload.get('returncode')} "
        f"({stdout_len} stdout byte(s), {stderr_len} stderr byte(s)).{truncated}"
    )


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
            "properties": {
                "cwd": {"type": "string"},
                "name": {"type": "string"},
                "branch": {"type": "string"},
                "target_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "dev.test_run": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
                "background": {"type": "boolean"},
                "max_output_chars": {"type": "integer"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "dev.test_status": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
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
        ("dev.test_run", test_run, ["tests", "developer_execution"], ["read", "inspect", "execute_test"]),
        ("dev.test_status", test_status, ["tests", "developer_execution"], ["read", "inspect"]),
    ]
    for name, execute, capabilities, effects in defs:
        risk_level = RiskLevel.R2_REVERSIBLE_MODIFY if name == "dev.test_run" else RiskLevel.R0_READ_ONLY
        read_only = name != "dev.test_run"
        registry.register(
            ToolDefinition(
                name=name,
                description=tool_description(name),
                search_hint=tool_search_hint(name),
                input_schema=_schema(name),
                output_schema={"type": "object"},
                risk_level=risk_level,
                agent_owner="ComputerAgent",
                supports_dry_run=name == "dev.test_run",
                requires_authorized_path=name not in {"dev.git_status", "dev.diff_preview", "dev.shell_readonly"},
                execute=execute,
                permission_mode="ask_on_write" if name == "dev.test_run" else "auto_readonly",
                read_only=read_only,
                concurrency_safe=read_only,
                result_summary=_result_summary,
                ui_summary=f"{name} developer tool",
                capabilities=capabilities,
                effects=effects,
                resource_kinds=["workspace", "repository"],
                fast_path_eligible=read_only,
                trust_tier="builtin",
                origin="builtin",
                max_result_size=40000 if name == "dev.test_run" else 24000,
            )
        )
