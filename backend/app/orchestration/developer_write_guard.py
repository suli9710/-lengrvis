from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.tools.developer_tools import (
    DIFF_PREVIEW_LIMIT,
    TEST_OUTPUT_PREVIEW_LIMIT,
    _parse_test_command,
    _run_command,
    _run_test_foreground,
    _truncate_text,
    _trusted_guarded_git_command,
)

WRITE_TOOL_NAMES = frozenset({"Write", "Edit", "NotebookEdit"})
_PYTEST_GOAL_RE = re.compile(r"\b(pytest|unit\s*test|integration\s*test|failing\s+test)\b", re.IGNORECASE)


def extract_write_targets(tool_events: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for event in tool_events:
        name = str(event.get("name") or "")
        if name not in WRITE_TOOL_NAMES:
            continue
        tool_input = event.get("input")
        if not isinstance(tool_input, dict):
            continue
        raw_path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized = raw_path.strip()
        if normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return paths


def validate_write_paths(
    paths: list[str],
    *,
    workspace: str | Path,
    allowed_directories: list[str],
) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve(strict=False)
    allowed = list(allowed_directories or [str(root)])
    validated: list[str] = []
    rejected: list[dict[str, str]] = []
    for raw in paths:
        candidate = Path(raw)
        resolved = candidate if candidate.is_absolute() else root / candidate
        try:
            authorized = resolve_authorized(resolved, allowed)
            validated.append(str(authorized))
        except SecurityError as exc:
            rejected.append({"path": raw, "error": str(exc)})
    return {
        "ok": not rejected,
        "workspace": str(root),
        "validated_paths": validated,
        "rejected_paths": rejected,
    }


def git_worktree_diff_preview(
    workspace: str | Path,
    *,
    allowed_directories: list[str],
) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve(strict=False)
    allowed = list(allowed_directories or [str(root)])
    command, error = _trusted_guarded_git_command(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
        root=root,
        allowed_directories=allowed,
    )
    if command is None:
        return {
            "ok": False,
            "dry_run": True,
            "workspace": str(root),
            "changed_files": [],
            "diff_preview": "",
            "diff_truncated": False,
            "stderr": error,
            "returncode": 127,
        }
    result = _run_command(command, cwd=root, shell=False)
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    changed_files: list[dict[str, str]] = []
    prefix = ""
    if stdout and result.get("returncode") == 0:
        prefix_command, prefix_error = _trusted_guarded_git_command(
            ["rev-parse", "--show-prefix"], root=root, allowed_directories=allowed
        )
        prefix_result = _run_command(prefix_command, cwd=root, shell=False) if prefix_command else None
        if prefix_result is None or prefix_result.get("returncode") != 0:
            result["returncode"] = 1
            stderr = prefix_error or "Unable to resolve the Git workspace prefix."
            stdout = ""
        else:
            prefix = str(prefix_result.get("stdout") or "").rstrip("\r\n")
    for status, repo_path in _parse_git_status_records(stdout):
        # Porcelain paths are repository-relative even when Git is launched
        # from a subdirectory. Never combine them directly with the cwd.
        if not repo_path.startswith(prefix):
            continue
        rel_path = repo_path[len(prefix) :]
        try:
            candidate = resolve_authorized(root / rel_path, allowed)
            if not candidate.is_relative_to(root):
                continue
            changed_files.append({"status": status, "path": rel_path})
        except SecurityError as exc:
            changed_files.append({"status": status, "path": rel_path, "error": str(exc)})
    # Use the filtered paths, not raw Git output (rename source paths can be
    # outside the requested scope even when a pathspec was supplied).
    preview = "\n".join(f"{item['status']} {item['path']}" for item in changed_files)
    diff_text, truncated = _truncate_text(preview, DIFF_PREVIEW_LIMIT)
    return {
        "ok": result.get("returncode") == 0,
        "dry_run": True,
        "workspace": str(root),
        "changed_files": changed_files,
        "diff_preview": diff_text,
        "diff_truncated": truncated or bool(result.get("stdout_truncated")),
        "stderr": stderr[:500],
        "returncode": result.get("returncode"),
    }


def _parse_git_status_records(output: str) -> list[tuple[str, str]]:
    records = iter(output.split("\0")[:-1])
    parsed: list[tuple[str, str]] = []
    for record in records:
        if len(record) < 4 or record[2] != " ":
            continue
        status, path = record[:2], record[3:]
        if "R" in status or "C" in status:
            # NUL format places the destination first, then the source.
            if next(records, None) is None:
                break
        parsed.append((status.strip() or status, path))
    return parsed


def infer_verification_command(goal: str, changed_paths: list[str]) -> str | None:
    goal_text = goal.strip()
    if not goal_text:
        return None
    if not _PYTEST_GOAL_RE.search(goal_text):
        return None
    test_paths = [path for path in changed_paths if _looks_like_test_path(path)]
    if test_paths:
        quoted = " ".join(shlex.quote(path) for path in test_paths[:3])
        return f"python -m pytest -q {quoted}"
    backend_tests = [path for path in changed_paths if path.replace("\\", "/").startswith("backend/")]
    if backend_tests:
        return "python -m pytest -q backend/tests"
    if "backend" in goal_text.casefold():
        return "python -m pytest -q backend/tests"
    return "python -m pytest -q"


def _looks_like_test_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    return normalized.startswith("backend/tests/") or "/test_" in normalized or normalized.endswith("_test.py")


def run_write_verification(
    *,
    workspace: str | Path,
    allowed_directories: list[str],
    goal: str,
    tool_events: list[dict[str, Any]],
    require_verification: bool = True,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve(strict=False)
    allowed = list(allowed_directories or [str(root)])
    write_targets = extract_write_targets(tool_events)
    path_check = validate_write_paths(write_targets, workspace=root, allowed_directories=allowed)
    diff_preview = git_worktree_diff_preview(root, allowed_directories=allowed)
    changed_paths = [
        item["path"] for item in diff_preview.get("changed_files", []) if isinstance(item, dict) and item.get("path")
    ]
    verification_command = infer_verification_command(goal, changed_paths or write_targets)
    test_result: dict[str, Any] | None = None
    if require_verification and verification_command:
        tokens, reason = _parse_test_command(verification_command, allowed_directories=allowed)
        if tokens is None:
            test_result = {"ok": False, "skipped": True, "error": reason, "command_text": verification_command}
        else:
            output_dir = Path(data_dir or root) / "developer_test_runs"
            test_result = _run_test_foreground(
                tokens,
                cwd=root,
                command_text=verification_command,
                timeout_seconds=120,
                output_dir=output_dir,
                preview_chars=TEST_OUTPUT_PREVIEW_LIMIT,
            )
    writes_detected = bool(write_targets or changed_paths)
    ok = bool(path_check["ok"])
    if writes_detected:
        ok = ok and bool(diff_preview.get("ok", True))
    if test_result is not None and not test_result.get("skipped"):
        ok = ok and bool(test_result.get("ok"))
    return {
        "ok": ok,
        "writes_detected": writes_detected,
        "path_check": path_check,
        "diff_preview": diff_preview,
        "verification_command": verification_command,
        "test_result": test_result,
        "summary": _verification_summary(path_check, diff_preview, test_result),
    }


def _quote_shell_path(path: str) -> str:
    # P0-7 fix: Use shlex.quote() for robust shell escaping instead of manual
    # quote/escape logic that missed dangerous characters like $, `, \n, !, etc.
    return shlex.quote(path)


def _verification_summary(
    path_check: dict[str, Any],
    diff_preview: dict[str, Any],
    test_result: dict[str, Any] | None,
) -> str:
    if not path_check.get("ok"):
        rejected = path_check.get("rejected_paths") or []
        first = rejected[0]["path"] if rejected else "unknown"
        return f"Write guard rejected out-of-workspace path: {first}"
    changed = diff_preview.get("changed_files") or []
    if changed:
        names = ", ".join(str(item.get("path") or "") for item in changed[:5])
        base = f"Workspace diff preview: {len(changed)} file(s) ({names})"
    else:
        base = "No workspace diff detected after write tools."
    if test_result is None:
        return base
    if test_result.get("skipped"):
        return f"{base}; verification skipped: {test_result.get('error')}"
    if test_result.get("ok"):
        return f"{base}; verification passed ({test_result.get('command_text')})."
    return f"{base}; verification failed ({test_result.get('command_text')})."
