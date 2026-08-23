from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.content_provenance import propagate_content_envelope
from app.core.errors import SecurityError
from app.core.schemas import PlanStep, SafetyReview, ToolResult
from app.orchestration.resource_state import normalize_path_key, resource_state, resource_state_summary
from app.orchestration.result_budget import discard_large_result_artifact, large_result_artifact_path
from app.orchestration.runtime_context import TaskRuntimeContext
from app.policy.redaction import REDACTED, contains_sensitive_key, redact_public_text, redact_value
from app.tools.filesystem_safety import ensure_mutation_path_safe

logger = logging.getLogger(__name__)

_DEFAULT_TOOL_TIMEOUT_SECONDS = 300.0
_MAX_DAEMON_TOOL_THREADS = 32
_TOOL_THREAD_SLOTS = threading.BoundedSemaphore(_MAX_DAEMON_TOOL_THREADS)
_MAX_ROLLBACK_EVIDENCE_BYTES = 64 * 1024
_MAX_ROLLBACK_EVIDENCE_ITEMS = 256
_MAX_ROLLBACK_PATH_BYTES = 4096
_ROLLBACK_PRIMARY_ACTIONS = frozenset(
    {
        "backup",
        "delete_folder_if_empty",
        "move_back",
        "permanent_delete_unrecoverable",
        "rename_back",
        "restore_from_recycle_bin",
        "trash_created_file",
    }
)
_ROLLBACK_SECONDARY_ACTIONS = frozenset({"dst_backup"})
_TOOL_SUPPLIED_STATE_KEYS = frozenset({"_post_resource_state", "_post_state"})
_ROLLBACK_EVIDENCE_KEY = "_rollback_evidence"
_ROLLBACK_EVIDENCE_SCHEMA = "rollback-evidence/v2"
_MANAGED_BACKUP_IDENTITY_SCHEMA = "managed-backup-identity/v1"
_TRUSTED_TOOL_TIERS = frozenset({"builtin", "core", "first_party"})
_RESOURCE_STATE_KEYS = frozenset(
    {
        "kind",
        "path",
        "normalized_path",
        "exists",
        "is_file",
        "is_dir",
        "size",
        "mtime_ns",
        "inode",
        "sha256",
        "is_reparse_point",
        "auto_rollback_safe",
    }
)
_INVALID_ROLLBACK_EVIDENCE = {"_runtime_evidence_status": "invalid"}


# Failure strings that give the reflection layer nothing to reason about
# (see os_reflection._is_low_information_failure). Tool failures passing
# through the runtime are enriched so automated recovery stays possible
# instead of degrading to ask_user.
_LOW_INFORMATION_ERRORS = {"", "planned failure", "tool failed.", "failed", "unknown error"}


def _actionable_error_text(raw_error: str, step: PlanStep) -> str:
    """Ensure a tool-declared error string carries actionable context."""
    text = str(raw_error or "").strip()
    if text.casefold() not in _LOW_INFORMATION_ERRORS:
        return text
    args_hint = ", ".join(sorted((step.args or {}).keys())) or "none"
    base = text or "Tool reported a failure without details"
    return f"{base} (tool={step.tool_name}, args keys: {args_hint}). Verify the arguments or choose another tool."


def _exception_error_text(exc: BaseException, step: PlanStep) -> str:
    """Build a non-empty, typed error string for unexpected tool exceptions."""
    detail = _safe_runtime_error_text(exc) or "no exception message"
    return f"{type(exc).__name__}: {detail} (tool={step.tool_name})"


def _safe_runtime_error_text(value: Any) -> str:
    return _message_safe_text(str(redact_value(str(value or "")) or ""))


@dataclass(slots=True)
class RuntimeExecutionResult:
    kind: str
    result: ToolResult | None = None


def _withheld_tool_result(
    result: ToolResult,
    review: SafetyReview,
    runtime: TaskRuntimeContext,
    *,
    tool_name: str,
) -> ToolResult:
    reason = review.safe_alternative or "Tool result was withheld by SafetyReviewAgent."
    cleanup_complete = _discard_persisted_result(result, runtime, tool_name=tool_name)
    withheld = _withheld_result_stub(
        result,
        reason=reason,
        review_id=review.id,
        review_verdict=review.verdict.value,
    )
    if cleanup_complete:
        return withheld
    return withheld.model_copy(
        update={
            "output": {
                **withheld.output,
                "outcome_unknown": True,
                "automatic_replay_blocked": True,
                "artifact_cleanup_required": True,
            }
        },
        deep=True,
    )


def _pending_review_result_stub(result: ToolResult) -> ToolResult:
    """Persist only safe rollback metadata while a raw result is under review."""

    output: dict[str, Any] = {
        "review_pending": True,
        "outcome_unknown": True,
        "automatic_replay_blocked": True,
        "artifact_cleanup_pending": True,
    }
    return ToolResult(
        id=result.id,
        tool_call_id=result.tool_call_id,
        ok=False,
        output=output,
        changed_paths=list(result.changed_paths),
        rollback_info=dict(result.rollback_info),
        observation="Tool result is pending post-tool safety review.",
        content_envelope=None,
    )


def _sanitize_tool_rollback_evidence(
    output: Mapping[str, Any],
    *,
    pre_resource_state: Any,
    post_resource_state: Any,
    tool_origin: str,
    tool_trust_tier: str,
    data_dir: Path,
) -> tuple[list[str], dict[str, Any]]:
    """Return bounded runtime-owned evidence or a fixed fail-closed blocker."""

    if "changed_paths" not in output and "rollback_info" not in output:
        return [], {}
    try:
        changed_paths = _bounded_path_list(output.get("changed_paths", []))
        raw_info = output.get("rollback_info", {})
        if not isinstance(raw_info, Mapping):
            raise ValueError("rollback info is not an object")
        supplied_info = dict(raw_info)
        allowed = _ROLLBACK_PRIMARY_ACTIONS | _ROLLBACK_SECONDARY_ACTIONS | _TOOL_SUPPLIED_STATE_KEYS
        if set(supplied_info) - allowed:
            raise ValueError("rollback info has unknown fields")
        supplied_info = {key: value for key, value in supplied_info.items() if key not in _TOOL_SUPPLIED_STATE_KEYS}
        if not supplied_info:
            if changed_paths:
                before, before_paths = _validated_resource_states(pre_resource_state)
                after, after_paths = _validated_resource_states(post_resource_state)
                if before_paths != after_paths:
                    raise ValueError("post-execution state expanded the trusted path set")
                _require_paths_in_state(changed_paths, before_paths)
                _require_evidence_budget({"before": before, "after": after})
            return changed_paths, {}

        primary = set(supplied_info) & _ROLLBACK_PRIMARY_ACTIONS
        if len(primary) != 1:
            raise ValueError("rollback info must have one primary action")
        if not changed_paths:
            raise ValueError("rollback action lacks changed paths")
        action = next(iter(primary))
        if "dst_backup" in supplied_info and action not in {"move_back", "rename_back"}:
            raise ValueError("destination backup is not valid for this action")

        before_states, before_paths = _validated_resource_states(pre_resource_state)
        after_states, after_paths = _validated_resource_states(post_resource_state)
        if before_paths != after_paths:
            raise ValueError("post-execution state expanded the trusted path set")
        _require_paths_in_state(changed_paths, before_paths)
        before_by_path = _states_by_path(before_states)
        after_by_path = _states_by_path(after_states)
        safe_info, affected_paths = _sanitize_rollback_action(
            action,
            supplied_info[action],
            before_by_path,
            after_by_path,
            data_dir,
        )
        if any(normalize_path_key(path) not in affected_paths for path in changed_paths):
            raise ValueError("changed paths are not bound to the rollback action")
        if "dst_backup" in supplied_info:
            move_spec = safe_info[action]
            destination = move_spec["from"]
            destination_before = before_by_path[normalize_path_key(destination)]
            safe_info["dst_backup"] = _managed_backup_spec(
                supplied_info["dst_backup"],
                before_paths,
                data_dir,
                expected_original=destination,
                original_before=destination_before,
            )
        _validate_move_destination_backup(action, safe_info, before_by_path)
        origin = _bounded_tool_metadata(tool_origin)
        trust_tier = _bounded_tool_metadata(tool_trust_tier)
        marker = {
            "schema": _ROLLBACK_EVIDENCE_SCHEMA,
            "action": action,
            "tool": {
                "origin": origin,
                "trust_tier": trust_tier,
                "builtin": origin == "builtin" and trust_tier in _TRUSTED_TOOL_TIERS,
            },
            "pre_resource_state": before_states,
            "post_resource_state": after_states,
            "pre_state_summary": resource_state_summary(before_states),
            "post_state_summary": resource_state_summary(after_states),
        }
        safe_info["_post_resource_state"] = after_states
        safe_info[_ROLLBACK_EVIDENCE_KEY] = marker
        _require_evidence_budget({"changed_paths": changed_paths, "rollback_info": safe_info})
        return changed_paths, safe_info
    except (OSError, SecurityError, TypeError, UnicodeError, ValueError):
        return [], dict(_INVALID_ROLLBACK_EVIDENCE)


def _persistable_tool_result(result: ToolResult) -> ToolResult:
    """Replace duplicated raw rollback fields only after full-result review."""

    output = dict(result.output)
    if "changed_paths" in output:
        output["changed_paths"] = list(result.changed_paths)
    if "rollback_info" in output:
        output["rollback_info"] = copy.deepcopy(result.rollback_info)
    if output == result.output:
        return result
    return result.model_copy(update={"output": output}, deep=True)


def _sanitize_rollback_action(
    action: str,
    value: Any,
    before_by_path: dict[str, dict[str, Any]],
    after_by_path: dict[str, dict[str, Any]],
    data_dir: Path,
) -> tuple[dict[str, Any], set[str]]:
    state_paths = set(before_by_path)
    if action == "trash_created_file":
        path = _bounded_path(value)
        _require_paths_in_state([path], state_paths)
        before = before_by_path[normalize_path_key(path)]
        after = after_by_path[normalize_path_key(path)]
        _require_absent(before)
        _require_existing_kind(after, is_file=True)
        return {action: path}, {normalize_path_key(path)}
    if action == "delete_folder_if_empty":
        path = _bounded_path(value)
        _require_paths_in_state([path], state_paths)
        before = before_by_path[normalize_path_key(path)]
        after = after_by_path[normalize_path_key(path)]
        _require_absent(before)
        _require_existing_kind(after, is_dir=True)
        return {action: path}, {normalize_path_key(path)}
    if action in {"move_back", "rename_back"}:
        if not isinstance(value, Mapping) or set(value) != {"from", "to"}:
            raise ValueError("move evidence is malformed")
        spec = {key: _bounded_path(value[key]) for key in ("from", "to")}
        _require_paths_in_state(list(spec.values()), state_paths)
        source_key = normalize_path_key(spec["to"])
        destination_key = normalize_path_key(spec["from"])
        if source_key == destination_key:
            raise ValueError("move evidence uses the same source and destination")
        source_before = before_by_path[source_key]
        source_after = after_by_path[source_key]
        destination_after = after_by_path[destination_key]
        _require_existing_kind(source_before)
        _require_absent(source_after)
        _require_same_resource_identity(source_before, destination_after)
        return {action: spec}, {source_key, destination_key}
    if action == "backup":
        if not isinstance(value, Mapping):
            raise ValueError("managed backup evidence is malformed")
        original_path = _bounded_path(value.get("original_path"))
        _require_paths_in_state([original_path], state_paths)
        key = normalize_path_key(original_path)
        before = before_by_path[key]
        after = after_by_path[key]
        _require_existing_kind(before, is_file=True)
        _require_existing_kind(after, is_file=True)
        backup = _managed_backup_spec(
            value,
            state_paths,
            data_dir,
            expected_original=original_path,
            original_before=before,
        )
        return {action: backup}, {key}
    if action == "restore_from_recycle_bin":
        paths, was_list = _bounded_path_value_or_list(value)
        _require_paths_in_state(paths, state_paths)
        for path in paths:
            key = normalize_path_key(path)
            _require_existing_kind(before_by_path[key])
            _require_absent(after_by_path[key])
        return {action: paths if was_list else paths[0]}, {normalize_path_key(path) for path in paths}
    if action == "permanent_delete_unrecoverable":
        items = value if isinstance(value, list) else [value]
        if not items or len(items) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
            raise ValueError("unrecoverable path list is invalid")
        paths: list[str] = []
        for item in items:
            if isinstance(item, Mapping):
                if set(item) - {"path", "reason"} or "path" not in item:
                    raise ValueError("unrecoverable path entry is malformed")
                reason = item.get("reason")
                if reason is not None and (not isinstance(reason, str) or len(reason.encode("utf-8")) > 512):
                    raise ValueError("unrecoverable reason is invalid")
                paths.append(_bounded_path(item["path"]))
            else:
                paths.append(_bounded_path(item))
        _require_paths_in_state(paths, state_paths)
        for path in paths:
            key = normalize_path_key(path)
            _require_existing_kind(before_by_path[key])
            _require_absent(after_by_path[key])
        return {action: [{"path": path} for path in paths]}, {normalize_path_key(path) for path in paths}
    raise ValueError("unsupported rollback action")


def _managed_backup_spec(
    value: Any,
    state_paths: set[str],
    data_dir: Path,
    *,
    expected_original: str,
    original_before: dict[str, Any],
) -> dict[str, Any]:
    required = {"managed", "schema", "path", "original_path"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("managed backup evidence is malformed")
    if value.get("managed") is not True or type(value.get("schema")) is not int or value.get("schema") != 1:
        raise ValueError("managed backup identity is invalid")
    backup_path = _bounded_path(value["path"])
    original_path = _bounded_path(value["original_path"])
    _require_paths_in_state([original_path], state_paths)
    if normalize_path_key(original_path) != normalize_path_key(expected_original):
        raise ValueError("managed backup is bound to a different original path")
    root = (Path(data_dir).expanduser().resolve(strict=False) / "file-tool-backups").resolve(strict=False)
    raw_candidate = Path(backup_path).expanduser()
    candidate = raw_candidate.resolve(strict=False)
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("managed backup path escapes its runtime directory")
    ensure_mutation_path_safe(raw_candidate, [str(root)], include_self=True)
    backup_state = resource_state(candidate)
    _require_existing_kind(backup_state, is_file=True)
    if (
        not original_before.get("sha256")
        or backup_state.get("sha256") != original_before.get("sha256")
        or backup_state.get("size") != original_before.get("size")
    ):
        raise ValueError("managed backup content does not match the original pre-state")
    return {
        "managed": True,
        "schema": 1,
        "path": str(candidate),
        "original_path": original_path,
        "identity": {
            "schema": _MANAGED_BACKUP_IDENTITY_SCHEMA,
            "sha256": backup_state["sha256"],
            "size": backup_state["size"],
            "inode": backup_state["inode"],
        },
    }


def _validate_move_destination_backup(
    action: str,
    info: dict[str, Any],
    before_by_path: dict[str, dict[str, Any]],
) -> None:
    if action not in {"move_back", "rename_back"}:
        return
    destination = info[action]["from"]
    destination_before = before_by_path[normalize_path_key(destination)]
    if destination_before.get("exists") is True and "dst_backup" not in info:
        raise ValueError("overwritten destination has no managed backup")
    if destination_before.get("exists") is False and "dst_backup" in info:
        raise ValueError("destination backup does not correspond to a pre-existing destination")


def _require_absent(state: Mapping[str, Any]) -> None:
    if state.get("exists") is not False:
        raise ValueError("resource was not absent in the required state")


def _require_existing_kind(
    state: Mapping[str, Any],
    *,
    is_file: bool | None = None,
    is_dir: bool | None = None,
) -> None:
    if state.get("exists") is not True:
        raise ValueError("resource does not exist in the required state")
    if state.get("is_reparse_point") is True or state.get("auto_rollback_safe") is False:
        raise ValueError("filesystem-link resource cannot be rolled back automatically")
    if type(state.get("is_file")) is not bool or type(state.get("is_dir")) is not bool:
        raise ValueError("resource kind is incomplete")
    if state["is_file"] == state["is_dir"]:
        raise ValueError("resource kind is ambiguous")
    if is_file is not None and state["is_file"] is not is_file:
        raise ValueError("resource is not the required file kind")
    if is_dir is not None and state["is_dir"] is not is_dir:
        raise ValueError("resource is not the required directory kind")
    for key in ("size", "mtime_ns", "inode"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise ValueError("existing resource state is incomplete")
    if state["is_file"] and not state.get("sha256"):
        raise ValueError("file resource state lacks a content digest")


def _require_same_resource_identity(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    _require_existing_kind(before)
    _require_existing_kind(after)
    if before["is_file"] != after["is_file"] or before["is_dir"] != after["is_dir"]:
        raise ValueError("moved resource changed kind")
    if before["is_file"]:
        if before.get("sha256") != after.get("sha256") or before.get("size") != after.get("size"):
            raise ValueError("moved file identity does not match")
    elif not before.get("inode") or before.get("inode") != after.get("inode"):
        raise ValueError("moved directory identity does not match")


def _validated_resource_states(
    value: Any,
    *,
    required_paths: list[str] | None = None,
) -> tuple[list[dict[str, Any]], set[str]]:
    if not isinstance(value, list) or not value or len(value) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
        raise ValueError("runtime resource states are missing or oversized")
    states: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) - _RESOURCE_STATE_KEYS:
            raise ValueError("runtime resource state is malformed")
        path = _bounded_path(item.get("path"))
        if item.get("kind") != "file_resource_state" or type(item.get("exists")) is not bool:
            raise ValueError("runtime resource state identity is invalid")
        safe: dict[str, Any] = {
            "kind": "file_resource_state",
            "path": path,
            "normalized_path": normalize_path_key(path),
            "exists": item["exists"],
        }
        if safe["normalized_path"] in paths:
            raise ValueError("runtime resource state contains duplicate paths")
        for key in ("is_file", "is_dir", "is_reparse_point", "auto_rollback_safe"):
            if key in item:
                if type(item[key]) is not bool:
                    raise ValueError("runtime resource state flag is invalid")
                safe[key] = item[key]
        for key in ("size", "mtime_ns", "inode"):
            if key in item:
                if type(item[key]) is not int or item[key] < 0:
                    raise ValueError("runtime resource state number is invalid")
                safe[key] = item[key]
        if "sha256" in item:
            digest = item["sha256"]
            if not isinstance(digest, str) or (
                digest and (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.casefold()))
            ):
                raise ValueError("runtime resource state digest is invalid")
            safe["sha256"] = digest.casefold()
        if safe["exists"] is False and set(safe) - {"kind", "path", "normalized_path", "exists"}:
            raise ValueError("absent resource state contains inapplicable fields")
        states.append(safe)
        paths.add(safe["normalized_path"])
    if required_paths:
        _require_paths_in_state(required_paths, paths)
    states.sort(key=lambda state: state["normalized_path"])
    _require_evidence_budget(states)
    return states, paths


def _states_by_path(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(state["normalized_path"]): state for state in states}


def _bounded_path_list(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
        raise ValueError("changed paths are malformed or oversized")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        path = _bounded_path(item)
        key = normalize_path_key(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    _require_evidence_budget(result)
    return result


def _bounded_path_value_or_list(value: Any) -> tuple[list[str], bool]:
    if isinstance(value, list):
        if not value or len(value) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
            raise ValueError("rollback path list is invalid")
        return [_bounded_path(item) for item in value], True
    return [_bounded_path(value)], False


def _bounded_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("rollback path is invalid")
    if len(value.encode("utf-8")) > _MAX_ROLLBACK_PATH_BYTES:
        raise ValueError("rollback path is oversized")
    return value


def _bounded_tool_metadata(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("tool trust metadata is malformed")
    text = value.strip()
    if not text or len(text.encode("utf-8")) > 256:
        raise ValueError("tool trust metadata is missing or oversized")
    return text


def _require_paths_in_state(paths: list[str], state_paths: set[str]) -> None:
    if any(normalize_path_key(path) not in state_paths for path in paths):
        raise ValueError("rollback path is outside runtime-captured resources")


def _require_evidence_budget(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_ROLLBACK_EVIDENCE_BYTES:
        raise ValueError("rollback evidence is oversized")


def _quarantine_unreviewed_persisted_result(
    result: ToolResult,
    runtime: TaskRuntimeContext,
    *,
    tool_name: str,
) -> ToolResult:
    cleanup_complete = _discard_persisted_result(result, runtime, tool_name=tool_name)
    output: dict[str, Any] = {
        "outcome_unknown": True,
        "automatic_replay_blocked": True,
        "unreviewed_persisted_result_quarantined": True,
    }
    if not cleanup_complete:
        output["artifact_cleanup_required"] = True
    reason = "Persisted tool result lacks a valid full-result safety-review binding."
    return ToolResult(
        id=result.id,
        tool_call_id=result.tool_call_id,
        ok=False,
        output=output,
        error=reason,
        changed_paths=list(result.changed_paths),
        rollback_info=dict(result.rollback_info),
        observation=reason,
    )


def _withheld_result_stub(
    result: ToolResult,
    *,
    reason: str,
    review_id: str = "",
    review_verdict: str = "",
) -> ToolResult:
    output: dict[str, Any] = {
        "ok": False,
        "withheld": True,
        "reason": reason,
    }
    if review_id:
        output["post_tool_review_id"] = review_id
    if review_verdict:
        output["post_tool_review_verdict"] = review_verdict
    content_envelope = (
        propagate_content_envelope(result.content_envelope, output, sanitizer="safety_review_withhold")
        if result.content_envelope is not None
        else None
    )
    return ToolResult(
        id=result.id,
        tool_call_id=result.tool_call_id,
        ok=False,
        output=output,
        error=reason,
        changed_paths=list(result.changed_paths),
        rollback_info=dict(result.rollback_info),
        observation="Tool result was withheld by SafetyReviewAgent.",
        content_envelope=content_envelope,
        runtime_review_id=review_id,
        runtime_review_verdict=review_verdict,
        runtime_review_completed=bool(review_id and review_verdict),
    )


def _discard_persisted_result(
    result: ToolResult,
    runtime: TaskRuntimeContext,
    *,
    tool_name: str,
) -> bool:
    reference = runtime.large_results.get(result.id)
    try:
        expected = large_result_artifact_path(
            runtime.settings.data_dir,
            runtime.task.id,
            result.id,
            tool_name,
        )
        if reference is not None and Path(reference.path).expanduser().resolve(strict=False) != expected:
            logger.warning("Large-result runtime reference did not match its trusted artifact identity.")
            return False
    except (OSError, ValueError):
        logger.warning("Could not validate withheld large-result artifact identity.")
        return False
    cleanup_complete = discard_large_result_artifact(
        runtime.settings.data_dir,
        runtime.task.id,
        result.id,
        tool_name,
    )
    if cleanup_complete:
        runtime.large_results.pop(result.id, None)
    return cleanup_complete


_MESSAGE_SAFE_URL_KEYS = {"url", "final_url", "source_url", "target_url", "href"}
_MESSAGE_SAFE_ARTIFACT_KEYS = {"screenshot_url", "artifact_url"}
_MESSAGE_SAFE_IDENTIFIER_KEYS = {"id", "task_id", "step_id", "tool_call_id", "run_id"}


def _message_safe_tool_result(result: ToolResult) -> ToolResult:
    original_output = result.output if isinstance(result.output, dict) else {}
    output = _message_safe_value(copy.deepcopy(result.output))
    if isinstance(output, dict) and output.get("persisted_result") and original_output.get("path"):
        output["path"] = Path(str(original_output.get("path") or "")).name
    return result.model_copy(
        update={
            "output": output,
            "error": _message_safe_text(result.error),
            "observation": _message_safe_text(result.observation),
        },
        deep=True,
    )


def _message_safe_value(value: Any, *, key: str = "") -> Any:
    if key and contains_sensitive_key(key):
        return REDACTED if value is not None else value
    if isinstance(value, dict):
        return {str(item_key): _message_safe_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_message_safe_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_message_safe_value(item, key=key) for item in value]
    if isinstance(value, set):
        return [_message_safe_value(item, key=key) for item in sorted(value, key=str)]
    if isinstance(value, str):
        normalized_key = key.replace("-", "_").casefold()
        if normalized_key in _MESSAGE_SAFE_ARTIFACT_KEYS:
            return _message_safe_artifact_ref(value)
        if normalized_key in _MESSAGE_SAFE_URL_KEYS:
            return _message_safe_url(value)
        return _message_safe_text(value, preserve_generic_tokens=normalized_key in _MESSAGE_SAFE_IDENTIFIER_KEYS)
    return value


def _message_safe_text(text: str, *, preserve_generic_tokens: bool = False) -> str:
    return redact_public_text(str(text or ""), redact_generic_tokens=not preserve_generic_tokens)


def _message_safe_url(value: str) -> str:
    text = _message_safe_text(value)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and (parsed.query or parsed.fragment):
        query = "***" if parsed.query else ""
        return parsed._replace(query=query, fragment="").geturl()
    return text


def _message_safe_artifact_ref(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    candidate = parsed.path if parsed.scheme else text.split("?", 1)[0].split("#", 1)[0]
    return _message_safe_text(Path(candidate.replace("\\", "/")).name)


@dataclass(slots=True)
class _ToolWorkerHandle:
    future: asyncio.Future[Any]
    abort_event: threading.Event
    abandoned: bool = False
