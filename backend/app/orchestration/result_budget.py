from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import stat
from pathlib import Path

from app.config import AppSettings
from app.core.schemas import ToolResult
from app.orchestration.runtime_context import LargeResultReference, TaskRuntimeContext
from app.policy.policy_rules import BROWSER_CONTENT_TRUST

DEFAULT_PREVIEW_CHARS = 2000
logger = logging.getLogger(__name__)
_ARTIFACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_FILENAME_DOMAIN = b"large-result-artifact/v2\0"
_ARTIFACT_FILENAME_DIGEST_CHARS = 32
FULL_RESULT_REVIEW_MARKER = "full_result_review_completed"
_PRESERVED_SAFETY_METADATA_KEYS = {
    "browser_content_warnings",
    "content_trust",
}


def apply_result_budget(
    result: ToolResult,
    *,
    tool_name: str,
    max_result_size: int,
    runtime: TaskRuntimeContext,
    review_completed: bool,
) -> ToolResult:
    if max_result_size <= 0:
        return result
    content = json.dumps(result.output, ensure_ascii=False, default=str)
    if len(content) <= max_result_size:
        return result

    reference = persist_large_result(
        runtime.settings,
        runtime.task.id,
        result.id,
        tool_name,
        content,
    )
    runtime.remember_large_result(result.id, reference)
    budgeted_output = {
        "persisted_result": True,
        "path": reference.path,
        "original_size": reference.original_size,
        "preview": reference.preview,
        "has_more": reference.has_more,
        "artifact_sha256": reference.artifact_sha256,
        "artifact_size_bytes": reference.artifact_size_bytes,
        FULL_RESULT_REVIEW_MARKER: review_completed,
    }
    if isinstance(result.output, dict):
        for key in _PRESERVED_SAFETY_METADATA_KEYS:
            if key in result.output:
                budgeted_output[key] = result.output[key]
        if result.output.get("content_trust") == BROWSER_CONTENT_TRUST:
            budgeted_output["content_trust"] = BROWSER_CONTENT_TRUST
    result.output = budgeted_output
    if result.observation:
        result.observation = f"{result.observation} Large output persisted as an internal result artifact."
    else:
        result.observation = "Large output persisted as an internal result artifact."
    return result


def persist_large_result(
    settings: AppSettings,
    task_id: str,
    result_id: str,
    tool_name: str,
    content: str,
) -> LargeResultReference:
    path = large_result_artifact_path(settings.data_dir, task_id, result_id, tool_name)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    # The v2 filename hashes the result/tool identity to preserve the binding
    # without spending the narrow Win32 path budget on a long tool name.
    # Exclusive creation avoids following or replacing a pre-positioned file.
    encoded = content.encode("utf-8")
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(content)
    preview = content[:DEFAULT_PREVIEW_CHARS]
    return LargeResultReference(
        path=str(path),
        original_size=len(content),
        preview=preview,
        has_more=len(content) > len(preview),
        artifact_sha256=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        artifact_size_bytes=len(encoded),
    )


def reviewed_large_result_artifact_valid(
    result: ToolResult,
    *,
    data_dir: str | Path,
    task_id: str,
    tool_name: str,
) -> bool:
    """Verify that a persisted result is the exact artifact reviewed in full."""

    output = result.output if isinstance(result.output, dict) else {}
    if not output.get("persisted_result"):
        return True
    if (
        result.runtime_review_completed is not True
        or result.runtime_review_verdict != "allow"
        or not result.runtime_review_id
    ):
        return False
    if output.get(FULL_RESULT_REVIEW_MARKER) is not True:
        return False
    expected_hash = str(output.get("artifact_sha256") or "").strip().casefold()
    expected_size = output.get("artifact_size_bytes")
    if (
        not _SHA256_PATTERN.fullmatch(expected_hash)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
    ):
        return False
    if expected_size < 0:
        return False
    try:
        artifact = large_result_artifact_path(data_dir, task_id, result.id, tool_name)
        supplied_path = Path(str(output.get("path") or "")).expanduser().resolve(strict=False)
        if supplied_path != artifact or artifact.is_symlink():
            return False
        file_state = artifact.lstat()
        if not stat.S_ISREG(file_state.st_mode) or file_state.st_size != expected_size:
            return False
        digest = hashlib.sha256()
        measured_size = 0
        with artifact.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                measured_size += len(chunk)
    except (OSError, ValueError):
        return False
    actual_hash = f"sha256:{digest.hexdigest()}"
    return measured_size == expected_size and hmac.compare_digest(actual_hash, expected_hash)


def large_result_artifact_path(
    data_dir: str | Path,
    task_id: str,
    result_id: str,
    tool_name: str,
) -> Path:
    """Return the sole valid artifact path for one tool result."""

    normalized_task_id = _artifact_identifier(task_id, "task")
    normalized_result_id = _artifact_identifier(result_id, "result")
    root = Path(data_dir).expanduser().resolve(strict=False)
    tasks_root = (root / "tasks").resolve(strict=False)
    directory = (tasks_root / normalized_task_id / "tool-results").resolve(strict=False)
    if not directory.is_relative_to(tasks_root):
        raise ValueError("Large-result artifact directory escapes the data root.")
    return directory / _artifact_filename(normalized_result_id, tool_name)


def discard_large_result_artifact(
    data_dir: str | Path,
    task_id: str,
    result_id: str,
    tool_name: str,
) -> bool:
    """Delete only regular files owned by this exact result identity."""

    try:
        candidates = _large_result_cleanup_candidates(data_dir, task_id, result_id, tool_name)
        existing: list[Path] = []
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            file_state = candidate.lstat()
            if candidate.is_symlink() or not stat.S_ISREG(file_state.st_mode):
                return False
            existing.append(candidate)
        for candidate in existing:
            candidate.unlink()
        return True
    except (OSError, ValueError):
        logger.warning(
            "Could not delete trusted large-result artifact for task=%s result=%s tool=%s",
            task_id,
            result_id,
            tool_name,
        )
        return False


def _artifact_identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized in {".", ".."} or not _ARTIFACT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Large-result {label} id is invalid.")
    return normalized


def _safe_tool_component(tool_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(tool_name or ""))[:80] or "tool"


def _artifact_filename(result_id: str, tool_name: str) -> str:
    identity = _ARTIFACT_FILENAME_DOMAIN + result_id.encode("utf-8") + b"\0" + str(tool_name or "").encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:_ARTIFACT_FILENAME_DIGEST_CHARS]
    return f"r-{digest}.json"


def _large_result_cleanup_candidates(
    data_dir: str | Path,
    task_id: str,
    result_id: str,
    tool_name: str,
) -> tuple[Path, ...]:
    canonical = large_result_artifact_path(data_dir, task_id, result_id, tool_name)
    normalized_result_id = _artifact_identifier(result_id, "result")
    legacy = canonical.parent / f"{normalized_result_id}_{_safe_tool_component(tool_name)}.json"
    if legacy == canonical:
        return (canonical,)
    return canonical, legacy
