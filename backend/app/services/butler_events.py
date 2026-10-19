"""Durable event coalescing and causal deduplication before any model call."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import TERMINAL_MATTER_STATUSES, AutonomyScope, ButlerMatter, MatterStatus
from app.core.local_data_lifecycle import data_generation, local_data_operation
from app.core.schemas import ToolResult
from app.orchestration.resource_state import resource_state
from app.orchestration.tool_execution_journal import runtime_review_allows_result_reuse
from app.services.butler_scopes import application_in_scope, enabled_scopes, origin_of, path_in_scope

COALESCE_SECONDS = 30


def enqueue_event(kind: str, subject: str, details: dict, *, now: datetime | None = None) -> int:
    control = store.control_state()
    if (
        not control["enabled"]
        or control["paused"]
        or kind in {"app_switched", "network_changed", "usb_connected", "session_locked"}
    ):
        return 0
    generation = data_generation()
    count = 0
    timestamp = now or datetime.now(UTC)
    for scope in enabled_scopes():
        allowed = (
            kind == "file_changed"
            and path_in_scope(subject, scope)
            or kind == "browser_changed"
            and origin_of(subject) in scope.origins
            or kind == "screen_changed"
            and application_in_scope(details.get("process_id"), scope)
        )
        if not allowed or "observe" not in scope.allowed_actions:
            continue
        safe_details = {
            key: details[key]
            for key in ("action", "status", "process_id", "title", "version", "summary", "truncated")
            if key in details
        }
        if kind == "file_changed":
            path = Path(subject)
            try:
                stat = path.stat()
                safe_details.update(
                    size=stat.st_size, version=f"{stat.st_mtime_ns}:{stat.st_size}", extension=path.suffix
                )
            except OSError:
                safe_details["version"] = "absent"
        object_key = store.source_key(kind, subject)
        event_key = store.source_key(scope.id, scope.version, object_key, safe_details)
        data = json.dumps({"kind": kind, "subject": subject, "details": safe_details}, ensure_ascii=False)
        with local_data_operation(expected_generation=generation) as valid, db.connect() as conn:
            if not valid:
                return count
            db._begin_immediate_transaction(conn)
            if conn.execute("SELECT 1 FROM butler_events WHERE id=?", (event_key,)).fetchone():
                continue
            pending = conn.execute(
                "SELECT id, due_at FROM butler_events WHERE scope_id=? AND object_key=? AND status='pending'",
                (scope.id, object_key),
            ).fetchone()
            if pending:
                conn.execute("DELETE FROM butler_events WHERE id=?", (pending["id"],))
            due = pending["due_at"] if pending else (timestamp + timedelta(seconds=COALESCE_SECONDS)).isoformat()
            conn.execute(
                "INSERT INTO butler_events(id,scope_id,scope_version,object_key,data,generation,"
                "due_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    event_key,
                    scope.id,
                    scope.version,
                    object_key,
                    data,
                    generation[1],
                    due,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            count += 1
    return count


def _matches_own_effect(subject: str) -> bool:
    try:
        actual = resource_state(subject)
    except OSError:
        return False
    key = str(Path(subject).resolve()).casefold()
    for row in db.fetch_many("tool_results", limit=100):
        result = ToolResult.model_validate(row)
        if not result.ok or not runtime_review_allows_result_reuse(result):
            continue
        for state in result.output.get("_resource_state_after") or []:
            if not isinstance(state, dict) or not state.get("path") or "exists" not in state:
                continue
            if str(Path(state.get("path", "")).resolve()).casefold() != key:
                continue
            if all(
                actual.get(name) == state[name] for name in ("exists", "size", "mtime_ns", "sha256") if name in state
            ):
                return True
    return False


def flush_events(*, now: datetime | None = None) -> list[str]:
    from app.llm.registry import get_effective_settings

    if not store.control_state()["enabled"] or store.control_state()["paused"]:
        return []
    timestamp = (now or datetime.now(UTC)).isoformat()
    created = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM butler_events WHERE status IN ('pending','claimed') AND due_at<=? ORDER BY due_at LIMIT 50",
            (timestamp,),
        ).fetchall()
    for row in rows:
        scope = store.get(AutonomyScope, row["scope_id"])
        current = (
            scope is not None
            and scope.enabled
            and scope.version == row["scope_version"]
            and row["generation"] == data_generation()[1]
        )
        event = json.loads(row["data"])
        subject = event["subject"]
        if current and event["kind"] == "file_changed":
            current = path_in_scope(subject, scope) and not _matches_own_effect(subject)
        with local_data_operation(expected_generation=data_generation()) as generation, db.connect() as conn:
            if not generation:
                break
            db._begin_immediate_transaction(conn)
            # Authority and both writes belong to one transaction. A restart may
            # revisit a legacy claim, but can never lose an event between claim
            # and matter creation or create the same matter twice.
            current_scope = conn.execute("SELECT data FROM autonomy_scopes WHERE id=?", (row["scope_id"],)).fetchone()
            if current_scope:
                db._require_sensitive_record_integrity(conn, "autonomy_scopes", row["scope_id"], current_scope["data"])
                latest_scope = AutonomyScope.model_validate_json(current_scope["data"])
                current = current and latest_scope.enabled and latest_scope.version == row["scope_version"]
            else:
                current = False
            if (
                conn.execute(
                    "UPDATE butler_events SET status=?,updated_at=? WHERE id=? AND status IN ('pending','claimed')",
                    ("handled" if current else "ignored", timestamp, row["id"]),
                ).rowcount
                != 1
            ):
                continue
            if not current:
                continue
            existing = []
            for item in conn.execute("SELECT id,data FROM butler_matters").fetchall():
                db._require_sensitive_record_integrity(conn, "butler_matters", item["id"], item["data"])
                existing.append(ButlerMatter.model_validate_json(item["data"]))
            if any(
                item.source_key == row["id"]
                or (
                    item.status == MatterStatus.WAITING_EVENT
                    and item.waiting
                    and item.waiting.subject == subject
                    and item.scope_versions.get(row["scope_id"]) == row["scope_version"]
                )
                for item in existing
            ):
                # Waits are verified afresh by the coordinator, not by an event.
                continue
            related = next(
                (
                    item
                    for item in existing
                    if item.status not in TERMINAL_MATTER_STATUSES
                    and item.source_evidence.get("subject") == subject
                    and item.scope_versions.get(scope.id) == scope.version
                ),
                None,
            )
            if related:
                related.source_evidence = event
                related.version += 1
                related.updated_at = timestamp
                payload = related.model_dump_json()
                conn.execute(
                    "UPDATE butler_matters SET data=?,updated_at=? WHERE id=?", (payload, timestamp, related.id)
                )
                db._store_sensitive_record_integrity(conn, "butler_matters", related.id, payload)
                continue
            matter = ButlerMatter(
                id="matter_event_" + row["id"],
                goal="评估授权来源的新变化；有具体、可核验的事务时处理，否则忽略。",
                source="event",
                mode=get_effective_settings().mode,
                source_key=row["id"],
                source_evidence=event,
                scope_versions={scope.id: scope.version},
                priority=20,
            )
            payload = matter.model_dump_json()
            conn.execute(
                "INSERT INTO butler_matters(id,data,created_at,updated_at) VALUES(?,?,?,?)",
                (matter.id, payload, matter.created_at, matter.updated_at),
            )
            db._store_sensitive_record_integrity(conn, "butler_matters", matter.id, payload)
            created.append(matter.id)
    return created
