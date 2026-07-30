from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.automation import store
from app.automation.models import (
    ApplicationGrant,
    AutomationRun,
    AutomationTrigger,
    ConnectorStep,
    GrantStatus,
)
from app.automation.store import (
    add_template_version,
    create_application_grant,
    create_automation_run,
    create_template,
    create_trigger,
    get_template,
    get_template_version,
    list_application_grants,
)
from app.core import db, db_migrations
from app.main import create_app


def test_schema_migration_creates_versioned_automation_tables() -> None:
    db.init_db(force=True)
    expected = {
        "automation_templates",
        "automation_template_versions",
        "automation_triggers",
        "application_grants",
        "automation_runs",
        "automation_run_items",
        "execution_exceptions",
        "intent_capsules",
        "run_budget_ledgers",
        "device_credentials",
        "token_families",
        "mobile_refresh_tokens",
        "automation_trigger_events",
        "memory_quarantine",
        "memory_namespace",
        "memory_active_successors",
        "sensitive_record_integrity",
        "sensitive_integrity_bootstrap_anchor",
        "sensitive_record_presence",
    }
    with db.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        migrations = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()

    assert expected <= tables
    assert [(row["version"], row["name"]) for row in migrations] == [
        (1, "automation_foundation"),
        (2, "mobile_identity_foundation"),
        (3, "automation_file_trigger_foundation"),
        (4, "memory_quarantine_foundation"),
        (5, "memory_namespace_foundation"),
        (6, "memory_active_successor_guard"),
        (7, "sensitive_record_integrity_foundation"),
        (8, "sensitive_integrity_bootstrap_anchor"),
    ]


def test_template_versions_are_immutable_and_content_hashed() -> None:
    template, first = create_template(
        name="表格到网页",
        goal_template="读取 {{file}} 并填写网页",
        steps=[ConnectorStep(id="extract", connector="spreadsheet", action="extract")],
    )
    second = add_template_version(
        template.id,
        goal_template="读取 {{file}}，核对并填写网页",
        steps=[ConnectorStep(id="extract", connector="spreadsheet", action="extract")],
    )

    assert first.version == 1
    assert second.version == 2
    assert first.content_hash != second.content_hash
    assert get_template(template.id).current_version == 2  # type: ignore[union-attr]
    assert get_template_version(template.id, 1).content_hash == first.content_hash  # type: ignore[union-attr]


def test_template_version_load_fails_closed_on_content_hash_mismatch() -> None:
    template, version = create_template(name="完整性", goal_template="原始目标")
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM automation_template_versions WHERE id = ?", (version.id,)).fetchone()
        payload = json.loads(row["data"])
        payload["goal_template"] = "被篡改的目标"
        conn.execute(
            "UPDATE automation_template_versions SET data = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), version.id),
        )

    with pytest.raises(ValueError, match="content hash does not match"):
        get_template_version(template.id, 1)


def test_template_version_api_does_not_require_or_accept_template_name() -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/automation/templates",
        json={"name": "表格核对", "goal_template": "读取 {{file}}"},
    )
    assert created.status_code == 201
    template_id = created.json()["template"]["id"]

    version = client.post(
        f"/api/automation/templates/{template_id}/versions",
        json={"goal_template": "读取并核对 {{file}}"},
    )
    assert version.status_code == 201
    assert version.json()["version"] == 2

    unexpected_name = client.post(
        f"/api/automation/templates/{template_id}/versions",
        json={"name": "不应被忽略", "goal_template": "再次核对 {{file}}"},
    )
    assert unexpected_name.status_code == 422


def test_template_step_graph_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="contains a cycle"):
        create_template(
            name="循环模板",
            goal_template="不能执行",
            steps=[
                ConnectorStep(id="first", connector="spreadsheet", action="read", depends_on=["second"]),
                ConnectorStep(id="second", connector="browser", action="fill", depends_on=["first"]),
            ],
        )


def test_trigger_normalization_and_run_idempotency() -> None:
    template, _version = create_template(name="导入", goal_template="导入 {{file}}")
    trigger = create_trigger(
        AutomationTrigger(
            template_id=template.id,
            directory="D:/incoming",
            suffixes=["CSV", ".XLSX"],
        )
    )
    first = create_automation_run(
        AutomationRun(
            template_id=template.id,
            template_version=1,
            idempotency_key="file-hash-12345",
        )
    )
    second = create_automation_run(
        AutomationRun(
            template_id=template.id,
            template_version=1,
            idempotency_key="file-hash-12345",
        )
    )

    assert trigger.suffixes == [".csv", ".xlsx"]
    assert first.id == second.id


def test_run_idempotency_key_is_bound_to_request_content() -> None:
    template, _version = create_template(name="导入", goal_template="导入 {{file}}")
    create_automation_run(
        AutomationRun(
            template_id=template.id,
            template_version=1,
            idempotency_key="file-hash-bound",
            input_values={"file": "first.csv"},
        )
    )

    with pytest.raises(ValueError, match="already bound"):
        create_automation_run(
            AutomationRun(
                template_id=template.id,
                template_version=1,
                idempotency_key="file-hash-bound",
                input_values={"file": "second.csv"},
            )
        )


def test_application_grant_is_not_execution_approval_and_is_capped_at_30_days() -> None:
    grant = create_application_grant(
        app_id="browser.managed",
        capabilities=["fill", "submit"],
        days=30,
    )

    assert grant.permits_consideration("submit") is True
    assert not hasattr(grant, "approved")

    issued = datetime.now(UTC)
    with pytest.raises(ValueError, match="cannot exceed 30 days"):
        ApplicationGrant(
            app_id="browser.managed",
            capabilities=["submit"],
            issued_at=issued.isoformat(),
            expires_at=(issued + timedelta(days=31)).isoformat(),
        )

    with pytest.raises(ValueError, match="between 1 and 30 days"):
        create_application_grant(app_id="browser.managed", capabilities=["submit"], days=31)


def test_application_grant_expiry_is_persisted_and_returned_consistently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant = create_application_grant(app_id="browser.managed", capabilities=["fill"], days=1)
    future = datetime.now(UTC) + timedelta(days=2)

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return future if tz is not None else future.replace(tzinfo=None)

    monkeypatch.setattr(store, "datetime", FutureDateTime)

    listed = list_application_grants(app_id="browser.managed")
    loaded = store.get_application_grant(grant.id)

    assert listed[0].status == GrantStatus.EXPIRED
    assert loaded is not None and loaded.status == GrantStatus.EXPIRED
    assert loaded.permits_consideration("fill", now=future) is False
    with db.connect() as conn:
        row = conn.execute("SELECT status, data FROM application_grants WHERE id = ?", (grant.id,)).fetchone()
    assert row["status"] == GrantStatus.EXPIRED
    assert json.loads(row["data"])["status"] == GrantStatus.EXPIRED


def test_automation_run_response_redacts_sensitive_input_values() -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/automation/templates",
        json={"name": "敏感表单", "goal_template": "填写表单"},
    )
    template_id = created.json()["template"]["id"]

    response = client.post(
        "/api/automation/runs",
        json={
            "template_id": template_id,
            "idempotency_key": "sensitive-run-123",
            "input_values": {
                "password": "never-return-this",
                "contact": "alice@example.com",
                "safe_label": "quarterly-import",
            },
        },
    )

    assert response.status_code == 201
    values = response.json()["run"]["input_values"]
    assert values["password"] == "***"
    assert values["contact"] == "[REDACTED_EMAIL]"
    assert values["safe_label"] == "quarterly-import"
    assert "never-return-this" not in response.text


def test_budget_route_tightens_existing_ledger_and_rejects_expansion() -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/automation/templates",
        json={"name": "预算模板", "goal_template": "执行一次"},
    )
    template_id = created.json()["template"]["id"]
    run_response = client.post(
        "/api/automation/runs",
        json={"template_id": template_id, "idempotency_key": "budget-route-123"},
    )
    run_id = run_response.json()["run"]["id"]

    tightened = client.post(
        f"/api/automation/runs/{run_id}/budget",
        json={"limits": {"max_tool_calls": 5}},
    )
    expanded = client.post(
        f"/api/automation/runs/{run_id}/budget",
        json={"limits": {"max_tool_calls": 6}},
    )

    assert tightened.status_code == 200
    assert tightened.json()["limits"]["max_tool_calls"] == 5
    assert expanded.status_code == 409


def test_execution_exception_response_is_redacted_and_resolution_is_single_assignment() -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/automation/templates",
        json={"name": "异常模板", "goal_template": "执行一次"},
    )
    run_response = client.post(
        "/api/automation/runs",
        json={
            "template_id": created.json()["template"]["id"],
            "idempotency_key": "exception-route-" + str(123),
        },
    )
    run_id = run_response.json()["run"]["id"]

    exception_response = client.post(
        "/api/automation/exceptions",
        json={
            "run_id": run_id,
            "category": "field_mapping",
            "summary": "需要人工确认 alice@example.com",
            "safe_context": {"password": "do-not-return"},
        },
    )
    exception_id = exception_response.json()["id"]
    resolved = client.post(
        f"/api/automation/exceptions/{exception_id}/resolve",
        json={"resolution": {"password": "still-do-not-return", "choice": "candidate-a"}},
    )
    conflicting = client.post(
        f"/api/automation/exceptions/{exception_id}/resolve",
        json={"resolution": {"choice": "candidate-b"}},
    )

    assert exception_response.status_code == 201
    assert exception_response.json()["safe_context"]["password"] == "***"
    assert "alice@example.com" not in exception_response.text
    assert resolved.status_code == 200
    assert resolved.json()["resolution"]["password"] == "***"
    assert "still-do-not-return" not in resolved.text
    assert conflicting.status_code == 409


def test_schema_migration_rolls_back_partial_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_partial_write(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE partial_automation_state (id TEXT PRIMARY KEY)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        db_migrations,
        "MIGRATIONS",
        (db_migrations.SchemaMigration(1, "partial_failure", fail_after_partial_write),),
    )
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(RuntimeError, match="injected migration failure"):
            db_migrations.apply_schema_migrations(conn)
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'partial_automation_state'"
        ).fetchone()
        applied = conn.execute("SELECT version FROM schema_migrations").fetchall()
    finally:
        conn.close()

    assert table is None
    assert applied == []


def test_schema_migration_rejects_recorded_name_mismatch() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'unexpected_name', 'now')")
        with pytest.raises(RuntimeError, match="name mismatch"):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


def test_schema_migration_revalidates_already_applied_schema() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'automation_foundation', 'now')"
        )
        with pytest.raises(RuntimeError, match="automation_templates without required columns"):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("table", "legacy_schema", "expected_error"),
    [
        (
            "automation_template_versions",
            """
            CREATE TABLE automation_template_versions (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(template_id) REFERENCES automation_templates(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_automation_template_versions_template
                ON automation_template_versions(template_id, version DESC);
            """,
            "automation_template_versions without UNIQUE index on",
        ),
        (
            "automation_runs",
            """
            CREATE TABLE automation_runs (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                task_id TEXT,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_automation_runs_template_status
                ON automation_runs(template_id, status, updated_at);
            """,
            "automation_runs without UNIQUE index on",
        ),
        (
            "automation_run_items",
            """
            CREATE TABLE automation_run_items (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                status TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_automation_run_items_run_status
                ON automation_run_items(run_id, status, updated_at);
            """,
            "automation_run_items without UNIQUE index on",
        ),
        (
            "automation_trigger_events",
            """
            CREATE TABLE automation_trigger_events (
                id TEXT PRIMARY KEY,
                trigger_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                status TEXT NOT NULL,
                run_id TEXT,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE,
                FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE SET NULL
            );
            CREATE INDEX idx_automation_trigger_events_trigger_status
                ON automation_trigger_events(trigger_id, status, updated_at);
            CREATE INDEX idx_automation_trigger_events_run
                ON automation_trigger_events(run_id) WHERE run_id IS NOT NULL;
            """,
            "automation_trigger_events without UNIQUE index on",
        ),
        (
            "mobile_refresh_tokens",
            """
            CREATE TABLE mobile_refresh_tokens (
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
            CREATE INDEX idx_mobile_refresh_tokens_family_generation
                ON mobile_refresh_tokens(family_id, generation);
            CREATE INDEX idx_mobile_refresh_tokens_device_status
                ON mobile_refresh_tokens(device_id, status, updated_at);
            """,
            "idx_mobile_refresh_tokens_family_generation with the wrong uniqueness",
        ),
    ],
)
def test_schema_migration_rejects_legacy_tables_without_required_uniqueness(
    table: str,
    legacy_schema: str,
    expected_error: str,
) -> None:
    conn = _clean_migration_connection()
    try:
        _replace_empty_migration_table(conn, table, legacy_schema)
        with pytest.raises(RuntimeError, match=expected_error):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


def test_schema_migration_rejects_missing_required_index() -> None:
    conn = _clean_migration_connection()
    try:
        conn.execute("DROP INDEX idx_automation_runs_template_status")
        with pytest.raises(RuntimeError, match="without required index idx_automation_runs_template_status"):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


def test_schema_migration_rejects_wrong_partial_index_predicate() -> None:
    conn = _clean_migration_connection()
    try:
        conn.execute("DROP INDEX idx_automation_trigger_events_run")
        conn.execute(
            """
            CREATE INDEX idx_automation_trigger_events_run
            ON automation_trigger_events(run_id)
            WHERE run_id IS NULL
            """
        )
        with pytest.raises(RuntimeError, match="without required predicate WHERE run_id IS NOT NULL"):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


def test_schema_migration_rejects_wrong_foreign_key_delete_action() -> None:
    conn = _clean_migration_connection()
    try:
        _replace_empty_migration_table(
            conn,
            "automation_trigger_events",
            """
            CREATE TABLE automation_trigger_events (
                id TEXT PRIMARY KEY,
                trigger_id TEXT NOT NULL,
                event_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                run_id TEXT,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(trigger_id) REFERENCES automation_triggers(id) ON DELETE CASCADE,
                FOREIGN KEY(run_id) REFERENCES automation_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_automation_trigger_events_trigger_status
                ON automation_trigger_events(trigger_id, status, updated_at);
            CREATE INDEX idx_automation_trigger_events_run
                ON automation_trigger_events(run_id) WHERE run_id IS NOT NULL;
            """,
        )
        with pytest.raises(RuntimeError, match="automation_trigger_events foreign keys"):
            db_migrations.apply_schema_migrations(conn)
    finally:
        conn.close()


def _clean_migration_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE mobile_devices (id TEXT PRIMARY KEY)")
    conn.execute(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            task_id TEXT,
            embedding BLOB,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """
    )
    db_migrations.apply_schema_migrations(conn)
    return conn


def _replace_empty_migration_table(conn: sqlite3.Connection, table: str, replacement_schema: str) -> None:
    assert table in {
        "automation_template_versions",
        "automation_runs",
        "automation_run_items",
        "automation_trigger_events",
        "mobile_refresh_tokens",
    }
    assert conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0  # noqa: S608
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(f'DROP TABLE "{table}"')
        conn.executescript(replacement_schema)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
