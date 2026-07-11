from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]
    validate: Callable[[sqlite3.Connection], None] | None = None


@dataclass(frozen=True, slots=True)
class IndexShape:
    columns: tuple[str, ...]
    descending: tuple[bool, ...] = ()
    unique: bool = False
    partial: bool = False
    where_sql: str = ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _automation_foundation(conn: sqlite3.Connection) -> None:
    _execute_migration_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS automation_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            current_version INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_templates_updated
            ON automation_templates(updated_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS automation_template_versions (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(template_id, version),
            FOREIGN KEY(template_id) REFERENCES automation_templates(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_template_versions_template
            ON automation_template_versions(template_id, version DESC);

        CREATE TABLE IF NOT EXISTS automation_triggers (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(template_id) REFERENCES automation_templates(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_triggers_template_enabled
            ON automation_triggers(template_id, enabled, updated_at);

        CREATE TABLE IF NOT EXISTS application_grants (
            id TEXT PRIMARY KEY,
            app_id TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_application_grants_app_status_expiry
            ON application_grants(app_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS automation_runs (
            id TEXT PRIMARY KEY,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            task_id TEXT,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_runs_template_status
            ON automation_runs(template_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS automation_run_items (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, item_key),
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_automation_run_items_run_status
            ON automation_run_items(run_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS execution_exceptions (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            item_id TEXT,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_execution_exceptions_run_status
            ON execution_exceptions(run_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS intent_capsules (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            plan_revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_intent_capsules_task_status_expiry
            ON intent_capsules(task_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS run_budget_ledgers (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_run_budget_ledgers_status_updated
            ON run_budget_ledgers(status, updated_at);
        """,
    )


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
    _execute_migration_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS device_credentials (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            credential_type TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_device_credentials_device_status
            ON device_credentials(device_id, status, updated_at);

        CREATE TABLE IF NOT EXISTS token_families (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            credential_id TEXT NOT NULL,
            status TEXT NOT NULL,
            current_generation INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            reuse_detected_at TEXT,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE,
            FOREIGN KEY(credential_id) REFERENCES device_credentials(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_token_families_device_status_expiry
            ON token_families(device_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
            id TEXT PRIMARY KEY,
            family_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            secret_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            used_at TEXT,
            replaced_by_id TEXT,
            FOREIGN KEY(family_id) REFERENCES token_families(id) ON DELETE CASCADE,
            FOREIGN KEY(device_id) REFERENCES mobile_devices(id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mobile_refresh_tokens_family_generation
            ON mobile_refresh_tokens(family_id, generation);
        CREATE INDEX IF NOT EXISTS idx_mobile_refresh_tokens_device_status
            ON mobile_refresh_tokens(device_id, status, updated_at);
        """,
    )


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
    _execute_migration_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS automation_trigger_events (
            id TEXT PRIMARY KEY,
            trigger_id TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            run_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE,
            FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_trigger_events_trigger_status
            ON automation_trigger_events(trigger_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_automation_trigger_events_run
            ON automation_trigger_events(run_id)
            WHERE run_id IS NOT NULL;
        """,
    )


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
    _execute_migration_script(
        conn,
        """
        CREATE TABLE IF NOT EXISTS memory_quarantine (
            memory_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('quarantined', 'active', 'revoked')),
            source TEXT NOT NULL,
            user_confirmed INTEGER NOT NULL CHECK (user_confirmed IN (0, 1)),
            expires_at TEXT,
            reviewed_at TEXT,
            reviewed_by TEXT,
            provenance_source_kind TEXT,
            provenance_source_id TEXT,
            provenance_origin TEXT,
            provenance_content_hash TEXT,
            provenance_trust_level TEXT,
            provenance_taint_flags TEXT,
            provenance_observed_at TEXT,
            provenance_task_scope TEXT,
            provenance_user_confirmed INTEGER CHECK (provenance_user_confirmed IN (0, 1)),
            provenance_sanitizers_applied TEXT,
            provenance_integrity_hmac TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memory_quarantine_state_expiry
            ON memory_quarantine(state, expires_at, memory_id);
        CREATE INDEX IF NOT EXISTS idx_memory_quarantine_source_confirmation
            ON memory_quarantine(source, user_confirmed, memory_id);
        """,
    )
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


def _require_table_columns(conn: sqlite3.Connection, requirements: dict[str, set[str]]) -> None:
    for table, required_columns in requirements.items():
        columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        missing = sorted(required_columns - columns)
        if missing:
            raise RuntimeError(f"schema migration left {table} without required columns: {', '.join(missing)}")


def _require_primary_key_columns(conn: sqlite3.Connection, requirements: dict[str, tuple[str, ...]]) -> None:
    for table, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = tuple(
            str(row[1]) for row in sorted((row for row in rows if int(row[5]) > 0), key=lambda row: int(row[5]))
        )
        if columns != required_columns:
            expected = ", ".join(required_columns)
            actual = ", ".join(columns) or "missing"
            raise RuntimeError(f"schema migration left {table} primary key as {actual}; expected {expected}")


def _require_not_null_columns(conn: sqlite3.Connection, requirements: dict[str, set[str]]) -> None:
    for table, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        not_null = {str(row[1]) for row in rows if int(row[3]) == 1}
        missing = sorted(required_columns - not_null)
        if missing:
            raise RuntimeError(f"schema migration left {table} nullable critical columns: {', '.join(missing)}")


def _require_index_columns(conn: sqlite3.Connection, requirements: dict[str, tuple[str, ...]]) -> None:
    for index, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA index_info("{index}")').fetchall()
        columns = tuple(str(row[2]) for row in sorted(rows, key=lambda row: int(row[0])))
        if columns != required_columns:
            expected = ", ".join(required_columns)
            actual = ", ".join(columns) or "missing"
            raise RuntimeError(f"schema migration left {index} with columns {actual}; expected {expected}")


def _require_named_index_shapes(
    conn: sqlite3.Connection,
    requirements: dict[str, dict[str, IndexShape]],
) -> None:
    for table, table_requirements in requirements.items():
        listed = {str(row[1]): row for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()}
        for index, requirement in table_requirements.items():
            row = listed.get(index)
            if row is None:
                raise RuntimeError(f"schema migration left {table} without required index {index}")
            if bool(row[2]) != requirement.unique:
                expected = "unique" if requirement.unique else "non-unique"
                raise RuntimeError(f"schema migration left {index} with the wrong uniqueness; expected {expected}")
            if bool(row[4]) != requirement.partial:
                expected = "partial" if requirement.partial else "non-partial"
                raise RuntimeError(f"schema migration left {index} with the wrong predicate mode; expected {expected}")
            key_rows = [
                index_row
                for index_row in conn.execute(f'PRAGMA index_xinfo("{index}")').fetchall()
                if int(index_row[5]) == 1
            ]
            key_rows.sort(key=lambda index_row: int(index_row[0]))
            columns = tuple(str(index_row[2]) for index_row in key_rows)
            descending = tuple(bool(index_row[3]) for index_row in key_rows)
            expected_descending = requirement.descending or (False,) * len(requirement.columns)
            if columns != requirement.columns or descending != expected_descending:
                expected = _render_index_columns(requirement.columns, expected_descending)
                actual = _render_index_columns(columns, descending) if columns else "missing"
                raise RuntimeError(f"schema migration left {index} with key {actual}; expected {expected}")
            if requirement.where_sql:
                sql_row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (index,),
                ).fetchone()
                normalized_sql = _normalize_sql(sql_row[0] if sql_row is not None else "")
                if _normalize_sql(requirement.where_sql) not in normalized_sql:
                    raise RuntimeError(
                        f"schema migration left {index} without required predicate {requirement.where_sql}"
                    )


def _require_unique_index_columns(
    conn: sqlite3.Connection,
    requirements: dict[str, set[tuple[str, ...]]],
) -> None:
    for table, required_indexes in requirements.items():
        available: set[tuple[str, ...]] = set()
        for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
            if not bool(row[2]) or bool(row[4]):
                continue
            index = str(row[1])
            columns = tuple(
                str(index_row[2])
                for index_row in sorted(
                    conn.execute(f'PRAGMA index_info("{index}")').fetchall(),
                    key=lambda index_row: int(index_row[0]),
                )
            )
            available.add(columns)
        for required_columns in required_indexes:
            if required_columns not in available:
                rendered = ", ".join(required_columns)
                raise RuntimeError(f"schema migration left {table} without UNIQUE index on ({rendered})")


def _require_foreign_key_sets(
    conn: sqlite3.Connection,
    requirements: dict[str, set[tuple[str, str, str, str]]],
) -> None:
    for table, required_foreign_keys in requirements.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        }
        if actual != required_foreign_keys:
            expected = ", ".join(_render_foreign_key(item) for item in sorted(required_foreign_keys))
            found = ", ".join(_render_foreign_key(item) for item in sorted(actual)) or "none"
            raise RuntimeError(f"schema migration left {table} foreign keys as {found}; expected {expected}")


def _render_index_columns(columns: tuple[str, ...], descending: tuple[bool, ...]) -> str:
    return ", ".join(
        f"{column} DESC" if is_descending else column for column, is_descending in zip(columns, descending, strict=True)
    )


def _render_foreign_key(value: tuple[str, str, str, str]) -> str:
    from_column, target_table, target_column, on_delete = value
    return f"{from_column}->{target_table}.{target_column} ON DELETE {on_delete}"


def _normalize_sql(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _require_foreign_key(
    conn: sqlite3.Connection,
    *,
    table: str,
    from_column: str,
    target_table: str,
    target_column: str,
    on_delete: str,
) -> None:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    expected = (from_column, target_table, target_column, on_delete.upper())
    actual = {(str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper()) for row in rows}
    if expected not in actual:
        raise RuntimeError(
            f"schema migration left {table} without {from_column} -> {target_table}.{target_column} "
            f"ON DELETE {on_delete.upper()}"
        )


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
