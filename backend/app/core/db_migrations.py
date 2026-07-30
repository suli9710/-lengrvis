from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core import db_migration_sql as migration_sql
from app.core.db_migration_validation import (
    IndexShape,
)
from app.core.db_migration_validation import (
    require_foreign_key as _require_foreign_key,
)
from app.core.db_migration_validation import (
    require_foreign_key_sets as _require_foreign_key_sets,
)
from app.core.db_migration_validation import (
    require_index_columns as _require_index_columns,
)
from app.core.db_migration_validation import (
    require_named_index_shapes as _require_named_index_shapes,
)
from app.core.db_migration_validation import (
    require_not_null_columns as _require_not_null_columns,
)
from app.core.db_migration_validation import (
    require_primary_key_columns as _require_primary_key_columns,
)
from app.core.db_migration_validation import (
    require_table_columns as _require_table_columns,
)
from app.core.db_migration_validation import (
    require_unique_index_columns as _require_unique_index_columns,
)


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]
    validate: Callable[[sqlite3.Connection], None] | None = None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _automation_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(conn, migration_sql.AUTOMATION_FOUNDATION)


def _validate_automation_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "automation_templates": {"id", "name", "enabled", "current_version", "data", "created_at", "updated_at"},
            "automation_template_versions": {
                "id",
                "template_id",
                "version",
                "content_hash",
                "data",
                "created_at",
            },
            "automation_triggers": {"id", "template_id", "kind", "enabled", "data", "created_at", "updated_at"},
            "application_grants": {"id", "app_id", "status", "expires_at", "data", "created_at", "updated_at"},
            "automation_runs": {
                "id",
                "template_id",
                "template_version",
                "task_id",
                "status",
                "idempotency_key",
                "data",
                "created_at",
                "updated_at",
            },
            "automation_run_items": {"id", "run_id", "item_key", "status", "data", "created_at", "updated_at"},
            "execution_exceptions": {
                "id",
                "run_id",
                "item_id",
                "category",
                "status",
                "data",
                "created_at",
                "updated_at",
            },
            "intent_capsules": {
                "id",
                "task_id",
                "plan_revision",
                "status",
                "expires_at",
                "data",
                "created_at",
                "updated_at",
            },
            "run_budget_ledgers": {"id", "run_id", "status", "version", "data", "created_at", "updated_at"},
        },
    )
    _require_named_index_shapes(
        conn,
        {
            "automation_templates": {
                "idx_automation_templates_updated": IndexShape(
                    ("updated_at", "id"),
                    descending=(True, True),
                )
            },
            "automation_template_versions": {
                "idx_automation_template_versions_template": IndexShape(
                    ("template_id", "version"),
                    descending=(False, True),
                )
            },
            "automation_triggers": {
                "idx_automation_triggers_template_enabled": IndexShape(("template_id", "enabled", "updated_at"))
            },
            "application_grants": {
                "idx_application_grants_app_status_expiry": IndexShape(("app_id", "status", "expires_at"))
            },
            "automation_runs": {
                "idx_automation_runs_template_status": IndexShape(("template_id", "status", "updated_at"))
            },
            "automation_run_items": {
                "idx_automation_run_items_run_status": IndexShape(("run_id", "status", "updated_at"))
            },
            "execution_exceptions": {
                "idx_execution_exceptions_run_status": IndexShape(("run_id", "status", "updated_at"))
            },
            "intent_capsules": {
                "idx_intent_capsules_task_status_expiry": IndexShape(("task_id", "status", "expires_at"))
            },
            "run_budget_ledgers": {"idx_run_budget_ledgers_status_updated": IndexShape(("status", "updated_at"))},
        },
    )
    _require_unique_index_columns(
        conn,
        {
            "automation_template_versions": {("template_id", "version")},
            "automation_runs": {("idempotency_key",)},
            "automation_run_items": {("run_id", "item_key")},
            "run_budget_ledgers": {("run_id",)},
        },
    )
    _require_foreign_key_sets(
        conn,
        {
            "automation_template_versions": {("template_id", "automation_templates", "id", "CASCADE")},
            "automation_triggers": {("template_id", "automation_templates", "id", "CASCADE")},
            "automation_run_items": {("run_id", "automation_runs", "id", "CASCADE")},
            "execution_exceptions": {("run_id", "automation_runs", "id", "CASCADE")},
        },
    )


def _mobile_identity_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(conn, migration_sql.MOBILE_IDENTITY_FOUNDATION)


def _validate_mobile_identity_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "device_credentials": {
                "id",
                "device_id",
                "credential_type",
                "status",
                "data",
                "created_at",
                "updated_at",
                "revoked_at",
            },
            "token_families": {
                "id",
                "device_id",
                "credential_id",
                "status",
                "current_generation",
                "expires_at",
                "data",
                "created_at",
                "updated_at",
                "revoked_at",
                "reuse_detected_at",
            },
            "mobile_refresh_tokens": {
                "id",
                "family_id",
                "device_id",
                "generation",
                "secret_hash",
                "status",
                "expires_at",
                "data",
                "created_at",
                "updated_at",
                "used_at",
                "replaced_by_id",
            },
        },
    )
    _require_named_index_shapes(
        conn,
        {
            "device_credentials": {
                "idx_device_credentials_device_status": IndexShape(("device_id", "status", "updated_at"))
            },
            "token_families": {
                "idx_token_families_device_status_expiry": IndexShape(("device_id", "status", "expires_at"))
            },
            "mobile_refresh_tokens": {
                "idx_mobile_refresh_tokens_family_generation": IndexShape(
                    ("family_id", "generation"),
                    unique=True,
                ),
                "idx_mobile_refresh_tokens_device_status": IndexShape(("device_id", "status", "updated_at")),
            },
        },
    )
    _require_unique_index_columns(conn, {"mobile_refresh_tokens": {("family_id", "generation")}})
    _require_foreign_key_sets(
        conn,
        {
            "device_credentials": {("device_id", "mobile_devices", "id", "CASCADE")},
            "token_families": {
                ("device_id", "mobile_devices", "id", "CASCADE"),
                ("credential_id", "device_credentials", "id", "CASCADE"),
            },
            "mobile_refresh_tokens": {
                ("family_id", "token_families", "id", "CASCADE"),
                ("device_id", "mobile_devices", "id", "CASCADE"),
            },
        },
    )


def _automation_file_trigger_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(conn, migration_sql.AUTOMATION_FILE_TRIGGER_FOUNDATION)


def _validate_automation_file_trigger_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "automation_trigger_events": {
                "id",
                "trigger_id",
                "event_key",
                "status",
                "run_id",
                "data",
                "created_at",
                "updated_at",
            }
        },
    )
    _require_named_index_shapes(
        conn,
        {
            "automation_trigger_events": {
                "idx_automation_trigger_events_trigger_status": IndexShape(("trigger_id", "status", "updated_at")),
                "idx_automation_trigger_events_run": IndexShape(
                    ("run_id",),
                    partial=True,
                    where_sql="WHERE run_id IS NOT NULL",
                ),
            }
        },
    )
    _require_unique_index_columns(conn, {"automation_trigger_events": {("event_key",)}})
    _require_foreign_key_sets(
        conn,
        {
            "automation_trigger_events": {
                ("trigger_id", "automation_triggers", "id", "CASCADE"),
                ("run_id", "automation_runs", "id", "SET NULL"),
            }
        },
    )


def _memory_quarantine_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(conn, migration_sql.MEMORY_QUARANTINE_FOUNDATION)
    _backfill_memory_quarantine(conn)


def _backfill_memory_quarantine(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, data, created_at FROM memories ORDER BY id").fetchall()
    for row in rows:
        payload = _safe_memory_payload(row[1])
        source = _nonempty_text(payload.get("source")) or "unknown"
        state_value = _nonempty_text(payload.get("state")).casefold()
        if state_value not in {"quarantined", "active", "revoked"}:
            state_value = "active" if source.casefold() == "user" else "quarantined"
        raw_confirmation = payload.get("user_confirmed")
        user_confirmed = raw_confirmation if isinstance(raw_confirmation, bool) else source.casefold() == "user"
        envelope = payload.get("content_envelope")
        provenance = envelope if isinstance(envelope, dict) else {}
        provenance_confirmation = provenance.get("user_confirmed")
        created_at = _nonempty_text(row[2]) or _now_iso()
        reviewed_at = _optional_text(payload.get("reviewed_at"))
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
            ON CONFLICT(memory_id) DO NOTHING
            """,
            (
                str(row[0]),
                state_value,
                source,
                int(user_confirmed),
                _optional_text(payload.get("expires_at")),
                reviewed_at,
                _optional_text(payload.get("reviewed_by")),
                _optional_text(provenance.get("source_kind")),
                _optional_text(provenance.get("source_id")),
                _optional_text(provenance.get("origin")),
                _optional_text(provenance.get("content_hash")),
                _optional_text(provenance.get("trust_level")),
                _json_string_list(provenance.get("taint_flags")),
                _optional_text(provenance.get("observed_at")),
                _optional_text(provenance.get("task_scope")),
                int(provenance_confirmation) if isinstance(provenance_confirmation, bool) else None,
                _json_string_list(provenance.get("sanitizers_applied")),
                _optional_text(provenance.get("integrity_hmac")),
                created_at,
                reviewed_at or created_at,
            ),
        )


def _safe_memory_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _nonempty_text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    return _nonempty_text(value) or None


def _json_string_list(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    normalized = [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _validate_memory_quarantine_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "memory_quarantine": {
                "memory_id",
                "state",
                "source",
                "user_confirmed",
                "expires_at",
                "reviewed_at",
                "reviewed_by",
                "provenance_source_kind",
                "provenance_source_id",
                "provenance_origin",
                "provenance_content_hash",
                "provenance_trust_level",
                "provenance_taint_flags",
                "provenance_observed_at",
                "provenance_task_scope",
                "provenance_user_confirmed",
                "provenance_sanitizers_applied",
                "provenance_integrity_hmac",
                "created_at",
                "updated_at",
            }
        },
    )
    _require_primary_key_columns(conn, {"memory_quarantine": ("memory_id",)})
    _require_not_null_columns(
        conn,
        {"memory_quarantine": {"state", "source", "user_confirmed", "created_at", "updated_at"}},
    )
    _require_index_columns(
        conn,
        {
            "idx_memory_quarantine_state_expiry": ("state", "expires_at", "memory_id"),
            "idx_memory_quarantine_source_confirmation": ("source", "user_confirmed", "memory_id"),
        },
    )
    _require_foreign_key(
        conn,
        table="memory_quarantine",
        from_column="memory_id",
        target_table="memories",
        target_column="id",
        on_delete="CASCADE",
    )
    missing = conn.execute(
        """
        SELECT COUNT(*)
        FROM memories AS memory
        LEFT JOIN memory_quarantine AS quarantine ON quarantine.memory_id = memory.id
        WHERE quarantine.memory_id IS NULL
        """
    ).fetchone()
    if missing is not None and int(missing[0]) != 0:
        raise RuntimeError("memory quarantine migration left memories without normalized lifecycle rows")


def _memory_namespace_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(conn, migration_sql.MEMORY_NAMESPACE_FOUNDATION)
    _backfill_memory_namespace(conn)


def _backfill_memory_namespace(conn: sqlite3.Connection) -> None:
    memory_ids = {str(row[0]) for row in conn.execute("SELECT id FROM memories").fetchall()}
    rows = conn.execute("SELECT id, data, created_at FROM memories ORDER BY id").fetchall()
    for row in rows:
        payload = _safe_memory_payload(row[1])
        principal_id = _nonempty_text(payload.get("principal_id")) or "local-user"
        workspace_id = _nonempty_text(payload.get("workspace_id")) or "default"
        domain_scope = _nonempty_text(payload.get("domain_scope")) or "general"
        try:
            version = max(1, int(payload.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        supersedes = _nonempty_text(payload.get("supersedes"))
        if supersedes == str(row[0]) or supersedes not in memory_ids:
            supersedes = ""
        conflict_status = _nonempty_text(payload.get("conflict_status")).casefold()
        if conflict_status not in {"none", "conflicting", "resolved", "superseded"}:
            conflict_status = "none"
        created_at = _nonempty_text(row[2]) or _now_iso()
        conn.execute(
            """
            INSERT INTO memory_namespace (
                memory_id, principal_id, workspace_id, domain_scope, version, supersedes,
                conflict_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO NOTHING
            """,
            (
                str(row[0]),
                principal_id,
                workspace_id,
                domain_scope,
                version,
                supersedes or None,
                conflict_status,
                created_at,
                created_at,
            ),
        )


def backfill_missing_memory_metadata(conn: sqlite3.Connection) -> None:
    """Conservatively normalize legacy rows written after the schema migration ran."""
    _backfill_memory_quarantine(conn)
    _backfill_memory_namespace(conn)


def _validate_memory_namespace_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "memory_namespace": {
                "memory_id",
                "principal_id",
                "workspace_id",
                "domain_scope",
                "version",
                "supersedes",
                "conflict_status",
                "created_at",
                "updated_at",
            }
        },
    )
    _require_primary_key_columns(conn, {"memory_namespace": ("memory_id",)})
    _require_not_null_columns(
        conn,
        {
            "memory_namespace": {
                "principal_id",
                "workspace_id",
                "domain_scope",
                "version",
                "conflict_status",
                "created_at",
                "updated_at",
            }
        },
    )
    _require_index_columns(
        conn,
        {
            "idx_memory_namespace_recall": (
                "principal_id",
                "workspace_id",
                "domain_scope",
                "conflict_status",
                "memory_id",
            ),
            "idx_memory_namespace_lineage": ("supersedes", "version", "memory_id"),
        },
    )
    _require_foreign_key(
        conn,
        table="memory_namespace",
        from_column="memory_id",
        target_table="memories",
        target_column="id",
        on_delete="CASCADE",
    )
    _require_foreign_key(
        conn,
        table="memory_namespace",
        from_column="supersedes",
        target_table="memories",
        target_column="id",
        on_delete="SET NULL",
    )
    missing = conn.execute(
        """
        SELECT COUNT(*)
        FROM memories AS memory
        LEFT JOIN memory_namespace AS namespace ON namespace.memory_id = memory.id
        WHERE namespace.memory_id IS NULL
        """
    ).fetchone()
    if missing is not None and int(missing[0]) != 0:
        raise RuntimeError("memory namespace migration left memories without normalized namespace rows")


_MEMORY_ACTIVE_SUCCESSOR_TRIGGERS = {
    "trg_memory_quarantine_active_successor_insert",
    "trg_memory_quarantine_active_successor_update",
    "trg_memory_quarantine_active_successor_delete",
    "trg_memory_namespace_active_successor_insert",
    "trg_memory_namespace_active_successor_update",
    "trg_memory_namespace_active_successor_delete",
}


def _memory_active_successor_guard(conn: sqlite3.Connection) -> None:
    duplicate_parents = conn.execute(migration_sql.MEMORY_ACTIVE_SUCCESSOR_DUPLICATES).fetchall()
    for row in duplicate_parents:
        conn.execute(
            migration_sql.MEMORY_MARK_ACTIVE_SUCCESSOR_CONFLICTS,
            (_now_iso(), str(row[0])),
        )

    _execute_migration_script(conn, migration_sql.MEMORY_ACTIVE_SUCCESSOR_GUARD)


def _validate_memory_active_successor_guard(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {"memory_active_successors": {"parent_memory_id", "successor_memory_id"}},
    )
    _require_primary_key_columns(conn, {"memory_active_successors": ("parent_memory_id",)})
    _require_not_null_columns(
        conn,
        {"memory_active_successors": {"parent_memory_id", "successor_memory_id"}},
    )
    _require_unique_index_columns(conn, {"memory_active_successors": {("successor_memory_id",)}})
    _require_foreign_key(
        conn,
        table="memory_active_successors",
        from_column="parent_memory_id",
        target_table="memories",
        target_column="id",
        on_delete="CASCADE",
    )
    _require_foreign_key(
        conn,
        table="memory_active_successors",
        from_column="successor_memory_id",
        target_table="memories",
        target_column="id",
        on_delete="CASCADE",
    )
    triggers = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE '%active_successor%'"
        ).fetchall()
    }
    missing_triggers = sorted(_MEMORY_ACTIVE_SUCCESSOR_TRIGGERS - triggers)
    if missing_triggers:
        raise RuntimeError("memory active-successor migration left missing triggers: " + ", ".join(missing_triggers))
    missing = conn.execute(
        """
        SELECT COUNT(*)
        FROM memory_namespace AS scope
        JOIN memory_quarantine AS quarantine ON quarantine.memory_id = scope.memory_id
        LEFT JOIN memory_active_successors AS active
          ON active.parent_memory_id = scope.supersedes
         AND active.successor_memory_id = scope.memory_id
        WHERE scope.supersedes IS NOT NULL
          AND scope.conflict_status IN ('none', 'resolved')
          AND quarantine.state = 'active'
          AND active.parent_memory_id IS NULL
        """
    ).fetchone()
    stale = conn.execute(
        """
        SELECT COUNT(*)
        FROM memory_active_successors AS active
        LEFT JOIN memory_namespace AS scope
          ON scope.memory_id = active.successor_memory_id
         AND scope.supersedes = active.parent_memory_id
        LEFT JOIN memory_quarantine AS quarantine
          ON quarantine.memory_id = active.successor_memory_id
        WHERE scope.memory_id IS NULL
           OR scope.conflict_status NOT IN ('none', 'resolved')
           OR quarantine.memory_id IS NULL
           OR quarantine.state != 'active'
        """
    ).fetchone()
    if (missing is not None and int(missing[0]) != 0) or (stale is not None and int(stale[0]) != 0):
        raise RuntimeError("memory active-successor guard is inconsistent with normalized lifecycle state")


def _execute_migration_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute a static SQL script without sqlite3.executescript's implicit commit."""

    pending: list[str] = []
    for line in script.splitlines():
        pending.append(line)
        statement = "\n".join(pending).strip()
        if not statement or not sqlite3.complete_statement(statement):
            continue
        conn.execute(statement)
        pending.clear()
    if "\n".join(pending).strip():
        raise RuntimeError("schema migration contains an incomplete SQL statement")


def _sensitive_record_integrity_foundation(conn: sqlite3.Connection) -> None:
    # This is versioned so a recorded migration cannot silently recreate a
    # deleted proof table and trust freshly generated digests.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensitive_record_integrity (
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            digest TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (table_name, record_id)
        )
        """
    )


def _validate_sensitive_record_integrity_foundation(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "sensitive_record_integrity": {
                "table_name",
                "record_id",
                "version",
                "digest",
                "updated_at",
            }
        },
    )
    primary_key = {
        str(row[1])
        for row in conn.execute('PRAGMA table_info("sensitive_record_integrity")').fetchall()
        if int(row[5] or 0) > 0
    }
    if primary_key != {"table_name", "record_id"}:
        raise RuntimeError("schema migration left sensitive_record_integrity without the composite primary key")


def _sensitive_integrity_bootstrap_anchor(conn: sqlite3.Connection) -> None:
    # The anchor is deliberately separate from the HMAC proof rows.  A
    # missing proof must never look like a brand-new database after startup.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensitive_integrity_bootstrap_anchor (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL CHECK (state IN ('pending', 'complete')),
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sensitive_integrity_bootstrap_anchor (id, state, updated_at)
        VALUES (1, 'pending', ?)
        """,
        (_now_iso(),),
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sensitive_record_presence (
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (table_name, record_id)
        )
        """
    )


def _validate_sensitive_integrity_bootstrap_anchor(conn: sqlite3.Connection) -> None:
    _require_table_columns(
        conn,
        {
            "sensitive_integrity_bootstrap_anchor": {"id", "state", "updated_at"},
            "sensitive_record_presence": {"table_name", "record_id", "created_at"},
        },
    )
    _require_primary_key_columns(
        conn,
        {
            "sensitive_integrity_bootstrap_anchor": ("id",),
            "sensitive_record_presence": ("table_name", "record_id"),
        },
    )
    anchor = conn.execute("SELECT state FROM sensitive_integrity_bootstrap_anchor WHERE id = 1").fetchone()
    if anchor is None or str(anchor[0]) not in {"pending", "complete"}:
        raise RuntimeError("schema migration left sensitive integrity bootstrap anchor unavailable")


MIGRATIONS: tuple[SchemaMigration, ...] = (
    SchemaMigration(1, "automation_foundation", _automation_foundation, _validate_automation_foundation),
    SchemaMigration(2, "mobile_identity_foundation", _mobile_identity_foundation, _validate_mobile_identity_foundation),
    SchemaMigration(
        3,
        "automation_file_trigger_foundation",
        _automation_file_trigger_foundation,
        _validate_automation_file_trigger_foundation,
    ),
    SchemaMigration(
        4, "memory_quarantine_foundation", _memory_quarantine_foundation, _validate_memory_quarantine_foundation
    ),
    SchemaMigration(
        5, "memory_namespace_foundation", _memory_namespace_foundation, _validate_memory_namespace_foundation
    ),
    SchemaMigration(
        6,
        "memory_active_successor_guard",
        _memory_active_successor_guard,
        _validate_memory_active_successor_guard,
    ),
    SchemaMigration(
        7,
        "sensitive_record_integrity_foundation",
        _sensitive_record_integrity_foundation,
        _validate_sensitive_record_integrity_foundation,
    ),
    SchemaMigration(
        8,
        "sensitive_integrity_bootstrap_anchor",
        _sensitive_integrity_bootstrap_anchor,
        _validate_sensitive_integrity_bootstrap_anchor,
    ),
)


def apply_schema_migrations(conn: sqlite3.Connection) -> list[int]:
    if conn.in_transaction:
        raise RuntimeError("schema migrations require a connection without an active transaction")
    _validate_migration_registry()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied_rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    applied = {int(row[0]): str(row[1]) for row in applied_rows}
    known = {migration.version: migration.name for migration in MIGRATIONS}
    unknown_versions = sorted(set(applied) - set(known))
    if unknown_versions:
        versions = ", ".join(str(version) for version in unknown_versions)
        raise RuntimeError(f"database contains unknown schema migration versions: {versions}")
    for version, applied_name in applied.items():
        if applied_name != known[version]:
            raise RuntimeError(
                f"schema migration {version} name mismatch: database={applied_name!r}, code={known[version]!r}"
            )
    completed: list[int] = []
    for migration in MIGRATIONS:
        if migration.version in applied:
            if migration.validate is not None:
                migration.validate(conn)
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration.apply(conn)
            if migration.validate is not None:
                migration.validate(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, _now_iso()),
            )
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
            completed.append(migration.version)
    return completed


def _validate_migration_registry() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    if any(version < 1 for version in versions):
        raise RuntimeError("schema migration versions must be positive")
    if versions != sorted(set(versions)):
        raise RuntimeError("schema migration versions must be unique and strictly increasing")
    names = [migration.name.strip() for migration in MIGRATIONS]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("schema migration names must be non-empty and unique")
