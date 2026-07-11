from __future__ import annotations

import json
from typing import Any

from app.core import db
from app.core.memory_namespace import normalize_memory_namespace

_MEMORY_STATES = {"quarantined", "active", "revoked"}
_MEMORY_CONFLICT_STATUSES = {"none", "conflicting", "resolved", "superseded"}
_MEMORY_SELECT = """
    SELECT
        memory.id,
        memory.kind,
        memory.content,
        memory.tags,
        memory.task_id,
        memory.data,
        memory.created_at,
        memory.last_used_at,
        scope.memory_id AS namespace_memory_id,
        scope.principal_id AS namespace_principal_id,
        scope.workspace_id AS namespace_workspace_id,
        scope.domain_scope AS namespace_domain_scope,
        scope.version AS namespace_version,
        scope.supersedes AS namespace_supersedes,
        scope.conflict_status AS namespace_conflict_status,
        quarantine.memory_id AS quarantine_memory_id,
        quarantine.state AS quarantine_state,
        quarantine.source AS quarantine_source,
        quarantine.user_confirmed AS quarantine_user_confirmed,
        quarantine.expires_at AS quarantine_expires_at,
        quarantine.reviewed_at AS quarantine_reviewed_at,
        quarantine.reviewed_by AS quarantine_reviewed_by,
        quarantine.provenance_source_kind,
        quarantine.provenance_source_id,
        quarantine.provenance_origin,
        quarantine.provenance_content_hash,
        quarantine.provenance_trust_level,
        quarantine.provenance_taint_flags,
        quarantine.provenance_observed_at,
        quarantine.provenance_task_scope,
        quarantine.provenance_user_confirmed,
        quarantine.provenance_sanitizers_applied,
        quarantine.provenance_integrity_hmac
    FROM memories AS memory
    JOIN memory_namespace AS scope ON scope.memory_id = memory.id
    LEFT JOIN memory_quarantine AS quarantine ON quarantine.memory_id = memory.id
"""


def upsert_memory(payload: dict[str, Any]) -> None:
    """Persist memory content plus authoritative lifecycle and namespace state."""
    record_id = str(payload.get("id") or "")
    content = str(payload.get("content", ""))
    kind = str(payload.get("kind") or "fact").strip() or "fact"
    tags = payload.get("tags") or []
    embedding = payload.get("embedding") or []
    namespace = normalize_memory_namespace(
        principal_id=payload.get("principal_id"),
        workspace_id=payload.get("workspace_id"),
        domain_scope=payload.get("domain_scope"),
    )
    try:
        version = int(payload.get("version") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("memory version must be a positive integer") from exc
    if version < 1:
        raise ValueError("memory version must be a positive integer")
    supersedes = str(payload.get("supersedes") or "").strip()
    raw_conflict_status = payload.get("conflict_status")
    conflict_status = str(
        getattr(raw_conflict_status, "value", raw_conflict_status) or "none"
    ).strip().casefold()
    if conflict_status not in _MEMORY_CONFLICT_STATUSES:
        raise ValueError(f"unsupported memory conflict status: {conflict_status}")
    source = str(payload.get("source") or "user")
    raw_state = payload.get("state")
    state = str(getattr(raw_state, "value", raw_state) or "active").strip().casefold()
    if state not in _MEMORY_STATES:
        state = "quarantined"
    content_envelope = _mapping_value(payload.get("content_envelope"))
    body = {
        "id": record_id,
        "principal_id": namespace.principal_id,
        "workspace_id": namespace.workspace_id,
        "domain_scope": namespace.domain_scope,
        "kind": kind,
        "version": version,
        "supersedes": supersedes,
        "conflict_status": conflict_status,
        "content": content,
        "tags": list(tags),
        "task_id": payload.get("task_id", ""),
        "source": source,
        "state": state,
        "user_confirmed": bool(payload.get("user_confirmed", False)),
        "expires_at": payload.get("expires_at") or "",
        "reviewed_at": payload.get("reviewed_at") or "",
        "reviewed_by": payload.get("reviewed_by") or "",
        "content_envelope": content_envelope,
        "use_count": int(payload.get("use_count") or 0),
        "last_used_at": payload.get("last_used_at") or "",
        "embedding_dim": int(payload.get("embedding_dim") or len(embedding)),
        "created_at": payload.get("created_at") or db._now_iso(),
        "embedding": list(embedding),
    }
    with db.connect() as conn:
        conn.execute("SAVEPOINT memory_record_upsert")
        try:
            # Acquire the write lock before lineage reads so SQLite never has to
            # upgrade a stale deferred read transaction under concurrent audit writes.
            conn.execute(
                "UPDATE memory_namespace SET updated_at = updated_at WHERE memory_id = ?",
                (body["id"],),
            )
            _validate_namespace_write(conn, body)
            conn.execute(
                """
                INSERT INTO memories (
                    id, kind, content, tags, task_id, embedding, data, created_at, last_used_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    content = excluded.content,
                    tags = excluded.tags,
                    task_id = excluded.task_id,
                    embedding = excluded.embedding,
                    data = excluded.data,
                    created_at = excluded.created_at,
                    last_used_at = excluded.last_used_at
                """,
                (
                    body["id"],
                    kind,
                    content,
                    ",".join(str(tag) for tag in tags) if tags else "",
                    body["task_id"],
                    None,
                    db._json(body),
                    body["created_at"],
                    body["last_used_at"] or None,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_quarantine (
                    memory_id, state, source, user_confirmed, expires_at, reviewed_at, reviewed_by,
                    provenance_source_kind, provenance_source_id, provenance_origin,
                    provenance_content_hash, provenance_trust_level, provenance_taint_flags,
                    provenance_observed_at, provenance_task_scope, provenance_user_confirmed,
                    provenance_sanitizers_applied, provenance_integrity_hmac, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    state = excluded.state,
                    source = excluded.source,
                    user_confirmed = excluded.user_confirmed,
                    expires_at = excluded.expires_at,
                    reviewed_at = excluded.reviewed_at,
                    reviewed_by = excluded.reviewed_by,
                    provenance_source_kind = excluded.provenance_source_kind,
                    provenance_source_id = excluded.provenance_source_id,
                    provenance_origin = excluded.provenance_origin,
                    provenance_content_hash = excluded.provenance_content_hash,
                    provenance_trust_level = excluded.provenance_trust_level,
                    provenance_taint_flags = excluded.provenance_taint_flags,
                    provenance_observed_at = excluded.provenance_observed_at,
                    provenance_task_scope = excluded.provenance_task_scope,
                    provenance_user_confirmed = excluded.provenance_user_confirmed,
                    provenance_sanitizers_applied = excluded.provenance_sanitizers_applied,
                    provenance_integrity_hmac = excluded.provenance_integrity_hmac,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                _quarantine_values(body, content_envelope),
            )
            conn.execute(
                """
                INSERT INTO memory_namespace (
                    memory_id, principal_id, workspace_id, domain_scope, version, supersedes,
                    conflict_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    principal_id = excluded.principal_id,
                    workspace_id = excluded.workspace_id,
                    domain_scope = excluded.domain_scope,
                    version = excluded.version,
                    supersedes = excluded.supersedes,
                    conflict_status = excluded.conflict_status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                _namespace_values(body),
            )
            if (
                body["supersedes"]
                and body["state"] == "active"
                and body["conflict_status"] in {"none", "resolved"}
            ):
                conn.execute(
                    """
                    UPDATE memory_namespace
                    SET conflict_status = 'superseded', updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (db._now_iso(), body["supersedes"]),
                )
        except BaseException:
            conn.execute("ROLLBACK TO SAVEPOINT memory_record_upsert")
            conn.execute("RELEASE SAVEPOINT memory_record_upsert")
            raise
        else:
            conn.execute("RELEASE SAVEPOINT memory_record_upsert")


def list_memories(
    *,
    tags: list[str] | None = None,
    kind: str | None = None,
    principal_id: str | None = None,
    workspace_id: str | None = None,
    domain_scope: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    namespace = normalize_memory_namespace(
        principal_id=principal_id,
        workspace_id=workspace_id,
        domain_scope=domain_scope,
    )
    clauses = [
        "scope.principal_id = ?",
        "scope.workspace_id = ?",
        "scope.domain_scope = ?",
    ]
    params: list[Any] = [namespace.principal_id, namespace.workspace_id, namespace.domain_scope]
    normalized_kind = str(kind or "").strip()
    if normalized_kind:
        clauses.append("memory.kind = ?")
        params.append(normalized_kind)
    params.append(limit)
    with db.connect() as conn:
        _ensure_memory_metadata(conn)
        rows = conn.execute(
            f"{_MEMORY_SELECT} WHERE {' AND '.join(clauses)} "
            "ORDER BY memory.created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        body = _memory_from_row(row)
        if tags:
            row_tags = set(str(row["tags"] or "").split(",")) - {""}
            wanted = set(tags)
            if not wanted.issubset(row_tags):
                continue
        results.append(body)
    return results


def get_memory(
    memory_id: str,
    *,
    principal_id: str | None = None,
    workspace_id: str | None = None,
    domain_scope: str | None = None,
) -> dict[str, Any] | None:
    namespace = normalize_memory_namespace(
        principal_id=principal_id,
        workspace_id=workspace_id,
        domain_scope=domain_scope,
    )
    with db.connect() as conn:
        _ensure_memory_metadata(conn)
        row = conn.execute(
            f"{_MEMORY_SELECT} WHERE memory.id = ? AND scope.principal_id = ? "
            "AND scope.workspace_id = ? AND scope.domain_scope = ?",
            (
                memory_id,
                namespace.principal_id,
                namespace.workspace_id,
                namespace.domain_scope,
            ),
        ).fetchone()
    if row is None:
        return None
    return _memory_from_row(row)


def delete_memory(
    memory_id: str,
    *,
    principal_id: str | None = None,
    workspace_id: str | None = None,
    domain_scope: str | None = None,
) -> bool:
    namespace = normalize_memory_namespace(
        principal_id=principal_id,
        workspace_id=workspace_id,
        domain_scope=domain_scope,
    )
    with db.connect() as conn:
        _ensure_memory_metadata(conn)
        conn.execute("SAVEPOINT memory_record_delete")
        try:
            owned = conn.execute(
                """
                SELECT 1
                FROM memory_namespace
                WHERE memory_id = ? AND principal_id = ? AND workspace_id = ? AND domain_scope = ?
                """,
                (
                    memory_id,
                    namespace.principal_id,
                    namespace.workspace_id,
                    namespace.domain_scope,
                ),
            ).fetchone()
            if owned is None:
                conn.execute("RELEASE SAVEPOINT memory_record_delete")
                return False
            conn.execute("DELETE FROM memory_quarantine WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_namespace WHERE memory_id = ?", (memory_id,))
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        except BaseException:
            conn.execute("ROLLBACK TO SAVEPOINT memory_record_delete")
            conn.execute("RELEASE SAVEPOINT memory_record_delete")
            raise
        else:
            conn.execute("RELEASE SAVEPOINT memory_record_delete")
    return cursor.rowcount > 0


def _validate_namespace_write(conn: Any, body: dict[str, Any]) -> None:
    existing = conn.execute(
        """
        SELECT scope.principal_id, scope.workspace_id, scope.domain_scope, scope.version,
               scope.supersedes, memory.kind
        FROM memory_namespace AS scope
        JOIN memories AS memory ON memory.id = scope.memory_id
        WHERE scope.memory_id = ?
        """,
        (body["id"],),
    ).fetchone()
    if existing is not None:
        current = (
            str(existing["principal_id"]),
            str(existing["workspace_id"]),
            str(existing["domain_scope"]),
            int(existing["version"]),
            str(existing["supersedes"] or ""),
            str(existing["kind"]),
        )
        requested = (
            body["principal_id"],
            body["workspace_id"],
            body["domain_scope"],
            body["version"],
            body["supersedes"],
            body["kind"],
        )
        if current != requested:
            raise ValueError("memory namespace, kind, and lineage are immutable")

    supersedes = body["supersedes"]
    if not supersedes:
        return
    if supersedes == body["id"]:
        raise ValueError("a memory cannot supersede itself")
    parent = conn.execute(
        """
        SELECT scope.principal_id, scope.workspace_id, scope.domain_scope, scope.version, memory.kind
        FROM memory_namespace AS scope
        JOIN memories AS memory ON memory.id = scope.memory_id
        WHERE scope.memory_id = ?
        """,
        (supersedes,),
    ).fetchone()
    if parent is None:
        raise ValueError("superseded memory was not found")
    parent_namespace = (
        str(parent["principal_id"]),
        str(parent["workspace_id"]),
        str(parent["domain_scope"]),
    )
    requested_namespace = (body["principal_id"], body["workspace_id"], body["domain_scope"])
    if parent_namespace != requested_namespace:
        raise ValueError("a memory can only supersede a record in the same namespace")
    if str(parent["kind"]) != body["kind"]:
        raise ValueError("a memory can only supersede a record of the same kind")
    if body["version"] <= int(parent["version"]):
        raise ValueError("a superseding memory must have a higher version")
    if body["state"] == "active" and body["conflict_status"] in {"none", "resolved"}:
        active_successor = conn.execute(
            """
            SELECT scope.memory_id
            FROM memory_namespace AS scope
            JOIN memory_quarantine AS quarantine ON quarantine.memory_id = scope.memory_id
            WHERE scope.supersedes = ?
              AND scope.memory_id != ?
              AND scope.conflict_status IN ('none', 'resolved')
              AND quarantine.state = 'active'
            LIMIT 1
            """,
            (supersedes, body["id"]),
        ).fetchone()
        if active_successor is not None:
            raise ValueError("superseded memory already has an active successor")


def _ensure_memory_metadata(conn: Any) -> None:
    missing = conn.execute(
        """
        SELECT 1
        FROM memories AS memory
        LEFT JOIN memory_quarantine AS quarantine ON quarantine.memory_id = memory.id
        LEFT JOIN memory_namespace AS scope ON scope.memory_id = memory.id
        WHERE quarantine.memory_id IS NULL OR scope.memory_id IS NULL
        LIMIT 1
        """
    ).fetchone()
    if missing is None:
        return
    from app.core.db_migrations import backfill_missing_memory_metadata

    backfill_missing_memory_metadata(conn)


def _namespace_values(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body["id"],
        body["principal_id"],
        body["workspace_id"],
        body["domain_scope"],
        body["version"],
        _optional_text(body.get("supersedes")),
        body["conflict_status"],
        body["created_at"],
        db._now_iso(),
    )


def _quarantine_values(body: dict[str, Any], envelope: dict[str, Any] | None) -> tuple[Any, ...]:
    provenance_confirmation = envelope.get("user_confirmed") if envelope is not None else None
    return (
        body["id"],
        body["state"],
        body["source"],
        int(bool(body["user_confirmed"])),
        _optional_text(body.get("expires_at")),
        _optional_text(body.get("reviewed_at")),
        _optional_text(body.get("reviewed_by")),
        _optional_envelope_text(envelope, "source_kind"),
        _optional_envelope_text(envelope, "source_id"),
        _optional_envelope_text(envelope, "origin"),
        _optional_envelope_text(envelope, "content_hash"),
        _optional_envelope_text(envelope, "trust_level"),
        _json_list(envelope.get("taint_flags")) if envelope is not None else None,
        _optional_envelope_text(envelope, "observed_at"),
        _optional_envelope_text(envelope, "task_scope"),
        int(provenance_confirmation) if isinstance(provenance_confirmation, bool) else None,
        _json_list(envelope.get("sanitizers_applied")) if envelope is not None else None,
        _optional_envelope_text(envelope, "integrity_hmac"),
        body["created_at"],
        db._now_iso(),
    )


def _memory_from_row(row: Any) -> dict[str, Any]:
    body = _safe_json_mapping(row["data"])
    body.update(
        {
            "id": str(row["id"] or ""),
            "principal_id": str(row["namespace_principal_id"]),
            "workspace_id": str(row["namespace_workspace_id"]),
            "domain_scope": str(row["namespace_domain_scope"]),
            "kind": str(row["kind"] or "fact"),
            "version": int(row["namespace_version"]),
            "supersedes": str(row["namespace_supersedes"] or ""),
            "conflict_status": str(row["namespace_conflict_status"]),
            "content": str(row["content"] or ""),
            "tags": [tag for tag in str(row["tags"] or "").split(",") if tag],
            "task_id": str(row["task_id"] or ""),
            "created_at": str(row["created_at"] or ""),
            "last_used_at": str(row["last_used_at"] or ""),
        }
    )
    if row["quarantine_memory_id"] is None:
        return body

    body.update(
        {
            "state": str(row["quarantine_state"]),
            "source": str(row["quarantine_source"]),
            "user_confirmed": bool(row["quarantine_user_confirmed"]),
            "expires_at": str(row["quarantine_expires_at"] or ""),
            "reviewed_at": str(row["quarantine_reviewed_at"] or ""),
            "reviewed_by": str(row["quarantine_reviewed_by"] or ""),
            "content_envelope": _content_envelope_from_row(row),
        }
    )
    return body


def _content_envelope_from_row(row: Any) -> dict[str, Any] | None:
    source_kind = str(row["provenance_source_kind"] or "")
    content_hash = str(row["provenance_content_hash"] or "")
    if not source_kind or not content_hash:
        return None
    return {
        "source_kind": source_kind,
        "source_id": str(row["provenance_source_id"] or ""),
        "origin": str(row["provenance_origin"] or ""),
        "content_hash": content_hash,
        "trust_level": str(row["provenance_trust_level"] or "unknown"),
        "taint_flags": _safe_json_list(row["provenance_taint_flags"]),
        "observed_at": str(row["provenance_observed_at"] or ""),
        "task_scope": str(row["provenance_task_scope"] or ""),
        "user_confirmed": bool(row["provenance_user_confirmed"]),
        "sanitizers_applied": _safe_json_list(row["provenance_sanitizers_applied"]),
        "integrity_hmac": str(row["provenance_integrity_hmac"] or ""),
    }


def _mapping_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else None
    return None


def _safe_json_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: Any) -> str:
    items = value if isinstance(value, list) else []
    return json.dumps([str(item) for item in items], ensure_ascii=False, separators=(",", ":"))


def _safe_json_list(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in decoded] if isinstance(decoded, list) else []


def _optional_envelope_text(envelope: dict[str, Any] | None, key: str) -> str | None:
    return _optional_text(envelope.get(key)) if envelope is not None else None


def _optional_text(value: Any) -> str | None:
    normalized = str(value).strip() if isinstance(value, str) else ""
    return normalized or None
