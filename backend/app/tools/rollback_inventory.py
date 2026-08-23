"""Complete, deterministic rollback evidence snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.errors import SecurityError
from app.core.schemas import ToolCall, ToolResult
from app.orchestration.resource_state import normalize_path_key, resource_state_summary
from app.orchestration.tool_execution_identity import (
    ToolExecutionJournalError,
    execution_key_for_intent,
    tool_call_risk_binding,
)
from app.orchestration.tool_execution_journal import runtime_review_allows_result_reuse
from app.policy.risk import is_modifying_or_higher
from app.tools.filesystem_safety import ensure_mutation_path_safe, path_exists_or_reparse_point
from app.tools.managed_backups import managed_backup_root, resolve_managed_backup_path

_ROLLBACK_CALL_STATES = frozenset({"committed", "created", "executing", "outcome_unknown"})
_EXPLICIT_NO_SIDE_EFFECT_TOOLS = frozenset({"file.create_folder"})
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
_ROLLBACK_EVIDENCE_KEY = "_rollback_evidence"
_ROLLBACK_EVIDENCE_SCHEMA = "rollback-evidence/v2"
_MANAGED_BACKUP_IDENTITY_SCHEMA = "managed-backup-identity/v2"
_ROLLBACK_METADATA_KEYS = frozenset({"_post_resource_state", _ROLLBACK_EVIDENCE_KEY})
_MAX_ROLLBACK_EVIDENCE_BYTES = 64 * 1024
_MAX_ROLLBACK_EVIDENCE_ITEMS = 256
_MAX_ROLLBACK_PATH_BYTES = 4096
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


@dataclass(frozen=True)
class RollbackSnapshotEntry:
    tool_call_id: str
    effect_at: str
    stable_id: str
    result: ToolResult | None = None
    blocker: str = ""
    detail: str = ""


@dataclass(frozen=True)
class RollbackSnapshot:
    task_id: str
    entries: tuple[RollbackSnapshotEntry, ...]
    journal_hmac: str = ""


def load_rollback_snapshot(task_id: str) -> RollbackSnapshot:
    """Load the complete rollback inventory with one SQLite read snapshot."""

    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return RollbackSnapshot(task_id="", entries=(), journal_hmac=_journal_hmac_for_rows("", []))
    rows = _load_journal_rows(normalized_task_id)

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        call_id = str(row["call_id"] or "")
        group = grouped.setdefault(
            call_id,
            {
                "status": str(row["call_status"] or ""),
                "task_id": str(row["call_task_id"] or ""),
                "step_id": str(row["call_step_id"] or ""),
                "execution_key": str(row["call_execution_key"] or ""),
                "data": row["call_data"],
                "created_at": str(row["call_created_at"] or ""),
                "results": [],
            },
        )
        if row["result_id"] is not None:
            group["results"].append(
                {
                    "id": str(row["result_id"] or ""),
                    "tool_call_id": str(row["result_tool_call_id"] or ""),
                    "data": row["result_data"],
                    "created_at": str(row["result_created_at"] or ""),
                }
            )

    entries: list[RollbackSnapshotEntry] = []
    for call_id, group in grouped.items():
        _append_call_entry(entries, call_id, group)

    entries.sort(key=_entry_order_key, reverse=True)
    return RollbackSnapshot(
        task_id=normalized_task_id,
        entries=tuple(entries),
        journal_hmac=_journal_hmac_for_rows(normalized_task_id, rows),
    )


def rollback_journal_hmac(task_id: str) -> str:
    """Hash only durable tool journal rows, independent of live rollback artifacts."""

    normalized_task_id = str(task_id or "").strip()
    rows = _load_journal_rows(normalized_task_id) if normalized_task_id else []
    return _journal_hmac_for_rows(normalized_task_id, rows)


def plan_for_snapshot(snapshot: RollbackSnapshot) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for entry in snapshot.entries:
        if entry.blocker:
            steps.append(
                {
                    "tool_call_id": entry.tool_call_id,
                    "actions": ["manual_review"],
                    "detail": {"reason": entry.blocker, "message": entry.detail},
                }
            )
            continue
        evidence_error = _result_evidence_error(entry.result)
        if evidence_error:
            steps.append(
                {
                    "tool_call_id": entry.tool_call_id,
                    "actions": ["manual_review"],
                    "detail": {"reason": evidence_error[0], "message": evidence_error[1]},
                }
            )
            continue
        info = dict(entry.result.rollback_info if entry.result is not None else {})
        steps.append(
            {
                "tool_call_id": entry.tool_call_id,
                "actions": [key for key in info if not str(key).startswith("_")],
                "detail": info,
            }
        )
    blocker_count = sum(1 for entry in snapshot.entries if entry.blocker or _result_evidence_error(entry.result))
    return {
        "task_id": snapshot.task_id,
        "steps": steps,
        "count": len(steps),
        "blocker_count": blocker_count,
        "complete": blocker_count == 0,
    }


def rollback_snapshot_hmac(snapshot: RollbackSnapshot) -> str:
    canonical_snapshot = {
        "version": "rollback-snapshot/v3",
        "task_id": snapshot.task_id,
        "journal_hmac": snapshot.journal_hmac,
        "entries": [
            {
                "tool_call_id": entry.tool_call_id,
                "effect_at": entry.effect_at,
                "stable_id": entry.stable_id,
                "blocker": entry.blocker,
                "detail": entry.detail,
                "result": entry.result.model_dump(mode="json") if entry.result is not None else None,
            }
            for entry in snapshot.entries
        ],
    }
    payload = json.dumps(
        canonical_snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_snapshot_task(snapshot: RollbackSnapshot, task_id: str) -> None:
    if snapshot.task_id != str(task_id or "").strip():
        raise ValueError("Rollback snapshot is bound to a different task.")


def _append_call_entry(entries: list[RollbackSnapshotEntry], call_id: str, group: dict[str, Any]) -> None:
    physical_status = str(group["status"] or "").strip()
    if physical_status not in _ROLLBACK_CALL_STATES:
        return
    call_payload = _json_object(group["data"])
    call = _validated_tool_call(call_id, group, call_payload)
    committed_at = str(call.committed_at if call is not None else "").strip()
    call_effect_at = str(
        (call.outcome_unknown_at if call is not None else "")
        or committed_at
        or (call.started_at if call is not None else "")
        or group["created_at"]
        or ""
    )
    if call is None:
        entries.append(
            _blocker(
                call_id,
                call_effect_at,
                "corrupt_tool_call",
                "The committed tool-call record is malformed; inspect its side effects manually.",
            )
        )
        return
    result_rows = list(group["results"])
    if physical_status == "created":
        if result_rows or _call_may_modify(call):
            entries.append(
                _blocker(
                    call_id,
                    str((result_rows[0].get("created_at") if result_rows else "") or call_effect_at),
                    "legacy_created_execution",
                    "A legacy created tool call has execution indicators but no committed v2 journal state; "
                    "inspect its side effects manually.",
                )
            )
        return
    if physical_status == "outcome_unknown":
        entries.append(
            _blocker(
                call_id,
                call_effect_at,
                "outcome_unknown",
                "The tool outcome is unknown, so automatic rollback cannot prove which side effects occurred.",
            )
        )
        return
    if physical_status == "executing":
        entries.append(
            _blocker(
                call_id,
                call_effect_at,
                "outcome_in_progress",
                "The tool call was still executing when the task stopped, so its side effects are not yet known.",
            )
        )
        return

    if not result_rows:
        entries.append(
            _blocker(
                call_id,
                call_effect_at,
                "missing_tool_result",
                "A committed tool call has no durable result or rollback evidence.",
            )
        )
        return
    if len(result_rows) != 1:
        entries.append(
            _blocker(
                call_id,
                call_effect_at,
                "ambiguous_tool_results",
                "A committed tool call has multiple durable results; choose the correct repair manually.",
            )
        )
        return

    result_row = result_rows[0]
    result_payload = _json_object(result_row["data"])
    if (
        result_payload is None
        or str(result_payload.get("id") or "") != result_row["id"]
        or str(result_payload.get("tool_call_id") or "") != call_id
        or result_row["tool_call_id"] != call_id
    ):
        entries.append(
            _blocker(
                call_id,
                str(result_row["created_at"] or call_effect_at),
                "corrupt_tool_result",
                "The durable tool result is malformed or is bound to a different tool call.",
            )
        )
        return
    result_payload = {
        **result_payload,
        "id": result_row["id"],
        "tool_call_id": call_id,
        "created_at": result_row["created_at"],
    }
    try:
        result = ToolResult.model_validate(result_payload)
    except ValidationError:
        entries.append(
            _blocker(
                call_id,
                str(result_row["created_at"] or call_effect_at),
                "corrupt_tool_result",
                "The durable tool result failed schema validation.",
            )
        )
        return
    result_output = dict(result.output or {})
    if result_output.get("review_pending"):
        entries.append(
            _blocker(
                call_id,
                result.created_at,
                "review_pending",
                "The durable tool result is still pending safety review and cannot be rolled back automatically.",
            )
        )
        return
    if result_output.get("outcome_unknown") or result_output.get("automatic_replay_blocked"):
        entries.append(
            _blocker(
                call_id,
                result.created_at,
                "outcome_unknown",
                "The durable tool result does not prove which side effects completed.",
            )
        )
        return
    if _parse_effect_timestamp(result.created_at) is None:
        entries.append(
            _blocker(
                call_id,
                result.created_at,
                "invalid_effect_timestamp",
                "The durable tool result has no valid completion timestamp, so rollback order cannot be proven.",
            )
        )
        return
    if result.rollback_info:
        evidence_error = _result_evidence_error(result)
        if evidence_error:
            entries.append(
                _blocker(
                    call_id,
                    result.created_at,
                    evidence_error[0],
                    evidence_error[1],
                )
            )
            return
        entries.append(
            RollbackSnapshotEntry(
                tool_call_id=call_id,
                effect_at=result.created_at,
                stable_id=result.id,
                result=result,
            )
        )
    elif result.changed_paths:
        entries.append(
            _blocker(
                call_id,
                result.created_at,
                "missing_rollback_evidence",
                "The tool changed filesystem paths without recording rollback evidence.",
            )
        )
    elif _trusted_no_side_effect(call, result):
        return
    elif _call_may_modify(call):
        entries.append(
            _blocker(
                call_id,
                result.created_at,
                "missing_rollback_evidence",
                "A modifying tool call committed without evidence that its side effects can be reversed.",
            )
        )


def _load_journal_rows(task_id: str) -> list[Any]:
    with db.connect() as conn:
        return conn.execute(
            """
            SELECT
                tc.id AS call_id,
                tc.task_id AS call_task_id,
                tc.step_id AS call_step_id,
                tc.execution_key AS call_execution_key,
                tc.status AS call_status,
                tc.data AS call_data,
                tc.created_at AS call_created_at,
                tr.id AS result_id,
                tr.tool_call_id AS result_tool_call_id,
                tr.data AS result_data,
                tr.created_at AS result_created_at
            FROM tool_calls AS tc
            LEFT JOIN tool_results AS tr ON tr.tool_call_id = tc.id
            WHERE tc.task_id = ?
            ORDER BY tc.id, tr.id
            """,
            (task_id,),
        ).fetchall()


def _journal_hmac_for_rows(task_id: str, rows: list[Any]) -> str:
    columns = (
        "call_id",
        "call_task_id",
        "call_step_id",
        "call_execution_key",
        "call_status",
        "call_data",
        "call_created_at",
        "result_id",
        "result_tool_call_id",
        "result_data",
        "result_created_at",
    )
    canonical = {
        "version": "rollback-journal/v1",
        "task_id": task_id,
        "rows": [{column: row[column] for column in columns} for row in rows],
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocker(tool_call_id: str, effect_at: str, reason: str, detail: str) -> RollbackSnapshotEntry:
    return RollbackSnapshotEntry(
        tool_call_id=tool_call_id,
        effect_at=str(effect_at or ""),
        stable_id=f"blocker-{tool_call_id}-{reason}",
        blocker=reason,
        detail=detail,
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _validated_tool_call(
    call_id: str,
    group: dict[str, Any],
    payload: dict[str, Any] | None,
) -> ToolCall | None:
    if payload is None:
        return None
    physical = {
        "id": call_id,
        "task_id": str(group.get("task_id") or ""),
        "step_id": str(group.get("step_id") or ""),
        "execution_key": str(group.get("execution_key") or ""),
        "status": str(group.get("status") or ""),
        "created_at": str(group.get("created_at") or ""),
    }
    if any(str(payload.get(field) or "") != value for field, value in physical.items()):
        return None
    try:
        call = ToolCall.model_validate(payload)
    except ValidationError:
        return None
    has_current_binding = any(
        (
            call.execution_intent_key,
            call.risk_binding_version,
            call.risk_review_id,
            call.declared_risk_level,
        )
    )
    if has_current_binding:
        try:
            expected_key = execution_key_for_intent(call.execution_intent_key, tool_call_risk_binding(call))
        except (ToolExecutionJournalError, TypeError, ValueError):
            return None
        if call.execution_key != expected_key:
            return None
    return call


def _entry_order_key(entry: RollbackSnapshotEntry) -> tuple[int, float, str, str, str]:
    parsed = _parse_effect_timestamp(entry.effect_at)
    if parsed is None:
        return (0, 0.0, entry.effect_at, entry.stable_id, entry.tool_call_id)
    return (1, parsed.timestamp(), "", entry.stable_id, entry.tool_call_id)


def _parse_effect_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _trusted_no_side_effect(call: ToolCall, result: ToolResult) -> bool:
    tool_name = str(call.tool_name or "")
    return bool(
        result.ok and tool_name in _EXPLICIT_NO_SIDE_EFFECT_TOOLS and result.output.get("no_side_effect") is True
    )


def _call_may_modify(call: ToolCall) -> bool:
    if str(call.approval_id or "").strip():
        return True
    return is_modifying_or_higher(call.risk_level)


def _result_evidence_error(result: ToolResult | None) -> tuple[str, str] | None:
    if result is None:
        return ("missing_tool_result", "The committed tool call has no durable result.")
    schema_error = _rollback_schema_error(result.rollback_info)
    if schema_error:
        return ("invalid_rollback_evidence", schema_error)
    if not result.changed_paths:
        return ("invalid_rollback_evidence", "Rollback evidence has no bounded changed-path summary.")
    if not runtime_review_allows_result_reuse(result):
        return (
            "untrusted_rollback_evidence",
            "Rollback evidence did not pass through the reviewed runtime execution path.",
        )
    return None


def _rollback_schema_error(rollback_info: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(rollback_info, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, UnicodeError, ValueError):
        return "Rollback evidence is not valid JSON data."
    if len(encoded) > _MAX_ROLLBACK_EVIDENCE_BYTES:
        return "Rollback evidence exceeds the runtime evidence budget."
    keys = {str(key) for key in rollback_info}
    primary = keys & _ROLLBACK_PRIMARY_ACTIONS
    unknown = keys - _ROLLBACK_PRIMARY_ACTIONS - _ROLLBACK_SECONDARY_ACTIONS - _ROLLBACK_METADATA_KEYS
    if unknown:
        return "Rollback evidence contains unsupported actions and requires manual repair."
    if len(primary) != 1:
        return "Rollback evidence must contain exactly one primary action."
    if "dst_backup" in keys and not primary <= {"move_back", "rename_back"}:
        return "Destination-backup evidence is only valid for move or rename rollback."
    marker = rollback_info.get(_ROLLBACK_EVIDENCE_KEY)
    if not isinstance(marker, dict):
        return "Rollback evidence is legacy or lacks the runtime-owned v2 marker."
    marker_error = _rollback_marker_error(marker, rollback_info, next(iter(primary)))
    if marker_error:
        return marker_error
    return ""


def _rollback_marker_error(marker: dict[str, Any], rollback_info: dict[str, Any], action: str) -> str:
    expected_keys = {
        "schema",
        "action",
        "tool",
        "pre_resource_state",
        "post_resource_state",
        "pre_state_summary",
        "post_state_summary",
    }
    if set(marker) != expected_keys or marker.get("schema") != _ROLLBACK_EVIDENCE_SCHEMA:
        return "Rollback evidence has an unknown or incomplete runtime marker."
    if marker.get("action") != action:
        return "Rollback evidence action does not match its runtime marker."
    tool = marker.get("tool")
    if not isinstance(tool, dict) or set(tool) != {"origin", "trust_tier", "builtin"}:
        return "Rollback evidence lacks complete tool trust metadata."
    if not _short_text(tool.get("origin")) or not _short_text(tool.get("trust_tier")):
        return "Rollback evidence contains invalid tool trust metadata."
    if type(tool.get("builtin")) is not bool:
        return "Rollback evidence contains invalid tool trust metadata."
    expected_builtin = tool["origin"] == "builtin" and tool["trust_tier"] in {"builtin", "core", "first_party"}
    if tool["builtin"] is not expected_builtin:
        return "Rollback evidence contains inconsistent tool trust metadata."

    before, before_error = _strict_resource_states(marker.get("pre_resource_state"))
    after, after_error = _strict_resource_states(marker.get("post_resource_state"))
    if before_error or after_error:
        return "Rollback evidence has incomplete pre/post resource state."
    before_paths = {state["normalized_path"] for state in before}
    after_paths = {state["normalized_path"] for state in after}
    if before_paths != after_paths:
        return "Rollback evidence post-state expands or omits the pre-authorized path set."
    if marker.get("pre_state_summary") != resource_state_summary(before):
        return "Rollback evidence pre-state summary is missing or does not match."
    if marker.get("post_state_summary") != resource_state_summary(after):
        return "Rollback evidence post-state summary is missing or does not match."
    if rollback_info.get("_post_resource_state") != after:
        return "Rollback executor state is not bound to the v2 evidence marker."
    before_by_path = {state["normalized_path"]: state for state in before}
    after_by_path = {state["normalized_path"]: state for state in after}
    return _rollback_action_state_error(action, rollback_info, before_by_path, after_by_path)


def _strict_resource_states(value: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(value, list) or not value or len(value) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
        return [], "missing"
    states: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - _RESOURCE_STATE_KEYS:
            return [], "malformed"
        path = item.get("path")
        if not _valid_path(path) or item.get("kind") != "file_resource_state" or type(item.get("exists")) is not bool:
            return [], "identity"
        normalized = normalize_path_key(path)
        if item.get("normalized_path") != normalized or normalized in seen:
            return [], "path"
        if item["exists"]:
            if type(item.get("is_file")) is not bool or type(item.get("is_dir")) is not bool:
                return [], "kind"
            if item["is_file"] == item["is_dir"]:
                return [], "kind"
            if item.get("is_reparse_point") is True or item.get("auto_rollback_safe") is False:
                return [], "unsafe"
            if any(type(item.get(key)) is not int or item[key] < 0 for key in ("size", "mtime_ns", "inode")):
                return [], "numbers"
            digest = item.get("sha256")
            if item["is_file"] and not _valid_digest(digest):
                return [], "digest"
            if item["is_dir"] and digest not in (None, ""):
                return [], "digest"
        elif set(item) != {"kind", "path", "normalized_path", "exists"}:
            return [], "absent"
        states.append(dict(item))
        seen.add(normalized)
    states.sort(key=lambda state: state["normalized_path"])
    return states, ""


def _rollback_action_state_error(
    action: str,
    info: dict[str, Any],
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> str:
    try:
        if action in {"trash_created_file", "delete_folder_if_empty"}:
            path = _checked_action_path(info[action], before)
            pre = before[normalize_path_key(path)]
            post = after[normalize_path_key(path)]
            if pre["exists"] or not post["exists"]:
                raise ValueError
            expected_key = "is_file" if action == "trash_created_file" else "is_dir"
            if post.get(expected_key) is not True:
                raise ValueError
        elif action in {"move_back", "rename_back"}:
            spec = info[action]
            if not isinstance(spec, dict) or set(spec) != {"from", "to"}:
                raise ValueError
            destination = _checked_action_path(spec["from"], before)
            source = _checked_action_path(spec["to"], before)
            if normalize_path_key(destination) == normalize_path_key(source):
                raise ValueError
            source_pre = before[normalize_path_key(source)]
            source_post = after[normalize_path_key(source)]
            destination_pre = before[normalize_path_key(destination)]
            destination_post = after[normalize_path_key(destination)]
            if not source_pre["exists"] or source_post["exists"] or not destination_post["exists"]:
                raise ValueError
            if source_pre["is_file"] != destination_post["is_file"]:
                raise ValueError
            if source_pre["is_file"] and (
                source_pre.get("sha256") != destination_post.get("sha256")
                or source_pre.get("size") != destination_post.get("size")
            ):
                raise ValueError
            if source_pre["is_dir"] and source_pre.get("inode") != destination_post.get("inode"):
                raise ValueError
            if destination_pre["exists"] != ("dst_backup" in info):
                raise ValueError
            if "dst_backup" in info:
                _validate_managed_backup(info["dst_backup"], destination)
        elif action == "backup":
            backup = info[action]
            if not isinstance(backup, dict):
                raise ValueError
            original = _checked_action_path(backup.get("original_path"), before)
            if not before[normalize_path_key(original)]["exists"] or not after[normalize_path_key(original)]["exists"]:
                raise ValueError
            _validate_managed_backup(backup, original)
        elif action == "restore_from_recycle_bin":
            paths = info[action] if isinstance(info[action], list) else [info[action]]
            if not paths or len(paths) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
                raise ValueError
            for raw_path in paths:
                path = _checked_action_path(raw_path, before)
                if not before[normalize_path_key(path)]["exists"] or after[normalize_path_key(path)]["exists"]:
                    raise ValueError
        elif action == "permanent_delete_unrecoverable":
            items = info[action]
            if not isinstance(items, list) or not items or len(items) > _MAX_ROLLBACK_EVIDENCE_ITEMS:
                raise ValueError
            for item in items:
                if not isinstance(item, dict) or set(item) != {"path"}:
                    raise ValueError
                path = _checked_action_path(item["path"], before)
                if not before[normalize_path_key(path)]["exists"] or after[normalize_path_key(path)]["exists"]:
                    raise ValueError
        else:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return "Rollback action is not fully bound to its pre/post resource state."
    return ""


def _validate_managed_backup(value: Any, expected_original: str) -> None:
    if not isinstance(value, dict) or set(value) != {"managed", "schema", "path", "original_path", "identity"}:
        raise ValueError
    if value.get("managed") is not True or type(value.get("schema")) is not int or value["schema"] != 1:
        raise ValueError
    if not _valid_path(value.get("path")) or not _valid_path(value.get("original_path")):
        raise ValueError
    if normalize_path_key(value["original_path"]) != normalize_path_key(expected_original):
        raise ValueError
    identity = value.get("identity")
    identity_keys = {"schema", "sha256", "size", "inode", "device", "ctime_ns"}
    if not isinstance(identity, dict) or set(identity) != identity_keys:
        raise ValueError
    if identity.get("schema") != _MANAGED_BACKUP_IDENTITY_SCHEMA or not _valid_digest(identity.get("sha256")):
        raise ValueError
    if any(type(identity.get(key)) is not int or identity[key] < 0 for key in ("size", "inode", "device", "ctime_ns")):
        raise ValueError
    raw_path = Path(value["path"]).expanduser()
    try:
        root = managed_backup_root()
        ensure_mutation_path_safe(raw_path, [str(root)], include_self=True)
        backup = resolve_managed_backup_path(raw_path)
        if not path_exists_or_reparse_point(backup):
            raise ValueError
        ensure_mutation_path_safe(backup, [str(root)], include_self=True)
        backup_stat = backup.stat(follow_symlinks=False)
        if not backup.is_file():
            raise ValueError
        if (
            int(backup_stat.st_size) != identity["size"]
            or int(getattr(backup_stat, "st_ino", 0) or 0) != identity["inode"]
            or int(getattr(backup_stat, "st_dev", 0) or 0) != identity["device"]
            or int(getattr(backup_stat, "st_ctime_ns", 0) or 0) != identity["ctime_ns"]
        ):
            raise ValueError
        if _sha256_file(backup) != identity["sha256"]:
            raise ValueError
    except (OSError, SecurityError):
        raise ValueError from None


def _checked_action_path(value: Any, states: dict[str, dict[str, Any]]) -> str:
    if not _valid_path(value) or normalize_path_key(value) not in states:
        raise ValueError
    return str(value)


def _valid_path(value: Any) -> bool:
    try:
        return (
            isinstance(value, str)
            and bool(value)
            and "\x00" not in value
            and len(value.encode("utf-8")) <= _MAX_ROLLBACK_PATH_BYTES
        )
    except UnicodeError:
        return False


def _short_text(value: Any) -> bool:
    try:
        return isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= 256
    except UnicodeError:
        return False


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value.casefold())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
