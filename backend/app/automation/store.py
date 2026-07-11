from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.automation.models import (
    ApplicationGrant,
    AutomationRun,
    AutomationRunItem,
    AutomationTemplate,
    AutomationTemplateVersion,
    AutomationTrigger,
    AutomationTriggerEvent,
    ConnectorStep,
    ExceptionStatus,
    ExecutionException,
    GrantStatus,
    TriggerEventStatus,
)
from app.core import audit, db
from app.core.content_provenance import stable_content_hash
from app.core.schemas import ContentEnvelope, now_iso


def create_template(
    *,
    name: str,
    goal_template: str,
    description: str = "",
    variable_schema: dict[str, Any] | None = None,
    steps: list[ConnectorStep] | None = None,
    semantic_locators: dict[str, Any] | None = None,
    fallback_locators: dict[str, Any] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    connector_versions: dict[str, str] | None = None,
    provenance: list[ContentEnvelope] | None = None,
) -> tuple[AutomationTemplate, AutomationTemplateVersion]:
    db.init_db()
    template = AutomationTemplate(name=name, description=description)
    version = _template_version(
        template.id,
        1,
        goal_template=goal_template,
        variable_schema=variable_schema,
        steps=steps,
        semantic_locators=semantic_locators,
        fallback_locators=fallback_locators,
        assertions=assertions,
        connector_versions=connector_versions,
        provenance=provenance,
    )
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _write_template(conn, template)
        _write_template_version(conn, version)
    audit.record(
        "automation.template.created",
        "AutomationTemplateService",
        {"template_id": template.id, "version": 1, "content_hash": version.content_hash},
    )
    return template, version


def add_template_version(
    template_id: str,
    *,
    goal_template: str,
    variable_schema: dict[str, Any] | None = None,
    steps: list[ConnectorStep] | None = None,
    semantic_locators: dict[str, Any] | None = None,
    fallback_locators: dict[str, Any] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    connector_versions: dict[str, str] | None = None,
    provenance: list[ContentEnvelope] | None = None,
) -> AutomationTemplateVersion:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        template = _template_from_row(
            conn.execute("SELECT data FROM automation_templates WHERE id = ?", (template_id,)).fetchone()
        )
        if template is None:
            raise KeyError(template_id)
        version_number = template.current_version + 1
        version = _template_version(
            template_id,
            version_number,
            goal_template=goal_template,
            variable_schema=variable_schema,
            steps=steps,
            semantic_locators=semantic_locators,
            fallback_locators=fallback_locators,
            assertions=assertions,
            connector_versions=connector_versions,
            provenance=provenance,
        )
        template.current_version = version_number
        template.updated_at = now_iso()
        _write_template(conn, template)
        _write_template_version(conn, version)
    return version


def list_templates(*, limit: int = 200) -> list[AutomationTemplate]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM automation_templates ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [AutomationTemplate.model_validate_json(row["data"]) for row in rows]


def get_template(template_id: str) -> AutomationTemplate | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM automation_templates WHERE id = ?", (template_id,)).fetchone()
    return _template_from_row(row)


def get_template_version(template_id: str, version: int | None = None) -> AutomationTemplateVersion | None:
    db.init_db()
    template = get_template(template_id)
    if template is None:
        return None
    version_number = int(version or template.current_version)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT data FROM automation_template_versions WHERE template_id = ? AND version = ?",
            (template_id, version_number),
        ).fetchone()
    return AutomationTemplateVersion.model_validate_json(row["data"]) if row else None


def create_trigger(trigger: AutomationTrigger) -> AutomationTrigger:
    db.init_db()
    if get_template(trigger.template_id) is None:
        raise KeyError(trigger.template_id)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO automation_triggers (id, template_id, kind, enabled, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trigger.id,
                trigger.template_id,
                trigger.kind,
                1 if trigger.enabled else 0,
                trigger.model_dump_json(),
                trigger.created_at,
                trigger.updated_at,
            ),
        )
    return trigger


def list_triggers(*, template_id: str = "", limit: int = 200) -> list[AutomationTrigger]:
    db.init_db()
    query = "SELECT data FROM automation_triggers"
    args: list[Any] = []
    if template_id:
        query += " WHERE template_id = ?"
        args.append(template_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    with db.connect() as conn:
        rows = conn.execute(query, tuple(args)).fetchall()
    return [AutomationTrigger.model_validate_json(row["data"]) for row in rows]


def create_application_grant(
    *,
    app_id: str,
    capabilities: list[str],
    data_scopes: list[str] | None = None,
    days: int = 30,
    app_identity_fingerprint: str = "",
) -> ApplicationGrant:
    if not 1 <= int(days) <= 30:
        raise ValueError("application grant duration must be between 1 and 30 days")
    issued = datetime.now(UTC)
    grant = ApplicationGrant(
        app_id=app_id,
        capabilities=capabilities,
        data_scopes=data_scopes or [],
        issued_at=issued.isoformat(),
        expires_at=(issued + timedelta(days=int(days))).isoformat(),
        app_identity_fingerprint=app_identity_fingerprint,
    )
    _write_grant(grant)
    audit.record(
        "automation.grant.created",
        "AutomationAuthorizationService",
        {
            "grant_id": grant.id,
            "app_id": grant.app_id,
            "capabilities": grant.capabilities,
            "expires_at": grant.expires_at,
            "execution_authorized": False,
        },
    )
    return grant


def list_application_grants(*, app_id: str = "", limit: int = 200) -> list[ApplicationGrant]:
    db.init_db()
    query = "SELECT data FROM application_grants"
    args: list[Any] = []
    if app_id:
        query += " WHERE app_id = ?"
        args.append(app_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    expired: list[ApplicationGrant] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(query, tuple(args)).fetchall()
        grants = [ApplicationGrant.model_validate_json(row["data"]) for row in rows]
        current = datetime.now(UTC)
        for grant in grants:
            if _expire_grant(grant, now=current):
                _write_grant(grant, conn=conn)
                expired.append(grant)
    for grant in expired:
        _audit_grant_expired(grant)
    return grants


def revoke_application_grant(grant_id: str) -> ApplicationGrant | None:
    db.init_db()
    revoked = False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM application_grants WHERE id = ?", (grant_id,)).fetchone()
        if row is None:
            return None
        grant = ApplicationGrant.model_validate_json(row["data"])
        if grant.status != GrantStatus.REVOKED:
            grant.status = GrantStatus.REVOKED
            grant.revoked_at = now_iso()
            grant.updated_at = grant.revoked_at
            _write_grant(grant, conn=conn)
            revoked = True
    if revoked:
        audit.record(
            "automation.grant.revoked",
            "AutomationAuthorizationService",
            {"grant_id": grant.id, "app_id": grant.app_id},
        )
    return grant


def get_application_grant(grant_id: str) -> ApplicationGrant | None:
    db.init_db()
    expired = False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM application_grants WHERE id = ?", (grant_id,)).fetchone()
        if row is None:
            return None
        grant = ApplicationGrant.model_validate_json(row["data"])
        expired = _expire_grant(grant, now=datetime.now(UTC))
        if expired:
            _write_grant(grant, conn=conn)
    if expired:
        _audit_grant_expired(grant)
    return grant


def create_automation_run(run: AutomationRun) -> AutomationRun:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT data FROM automation_runs WHERE idempotency_key = ?", (run.idempotency_key,)
        ).fetchone()
        if existing:
            existing_run = AutomationRun.model_validate_json(existing["data"])
            if _run_idempotency_binding(existing_run) != _run_idempotency_binding(run):
                raise ValueError("idempotency key is already bound to another automation run")
            return existing_run
        version_exists = conn.execute(
            "SELECT 1 FROM automation_template_versions WHERE template_id = ? AND version = ?",
            (run.template_id, run.template_version),
        ).fetchone()
        if version_exists is None:
            raise KeyError((run.template_id, run.template_version))
        conn.execute(
            """
            INSERT INTO automation_runs (
                id, template_id, template_version, task_id, status, idempotency_key, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.template_id,
                run.template_version,
                run.task_id or None,
                run.status,
                run.idempotency_key,
                run.model_dump_json(),
                run.created_at,
                run.updated_at,
            ),
        )
    return run


def get_automation_run(run_id: str) -> AutomationRun | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    return AutomationRun.model_validate_json(row["data"]) if row else None


def list_automation_runs(
    *,
    status: str = "",
    trigger_id: str = "",
    limit: int = 200,
) -> list[AutomationRun]:
    db.init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    query = "SELECT data FROM automation_runs"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    bounded_limit = max(1, min(int(limit or 200), 500))
    params.append(500 if trigger_id else bounded_limit)
    with db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    runs = [AutomationRun.model_validate_json(row["data"]) for row in rows]
    return [run for run in runs if not trigger_id or run.trigger_id == trigger_id][:bounded_limit]


def list_run_items(run_id: str, *, limit: int = 500) -> list[AutomationRunItem]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT data FROM automation_run_items WHERE run_id = ? ORDER BY created_at, id LIMIT ?",
            (run_id, max(1, min(int(limit or 500), 1000))),
        ).fetchall()
    return [AutomationRunItem.model_validate_json(row["data"]) for row in rows]


def create_or_get_trigger_event(event: AutomationTriggerEvent) -> tuple[AutomationTriggerEvent, bool]:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM automation_trigger_events WHERE event_key = ?",
            (event.event_key,),
        ).fetchone()
        if row is not None:
            existing = AutomationTriggerEvent.model_validate_json(row["data"])
            if (
                existing.trigger_id != event.trigger_id
                or existing.path != event.path
                or existing.content_hash != event.content_hash
            ):
                raise ValueError("automation trigger event key is already bound to different content")
            return existing, False
        conn.execute(
            """
            INSERT INTO automation_trigger_events (
                id, trigger_id, event_key, status, run_id, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.trigger_id,
                event.event_key,
                event.status,
                event.run_id or None,
                event.model_dump_json(),
                event.created_at,
                event.updated_at,
            ),
        )
    return event, True


def get_trigger_event(event_id: str) -> AutomationTriggerEvent | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM automation_trigger_events WHERE id = ?", (event_id,)).fetchone()
    return AutomationTriggerEvent.model_validate_json(row["data"]) if row else None


def list_trigger_events(
    *,
    trigger_id: str = "",
    statuses: set[TriggerEventStatus] | None = None,
    limit: int = 200,
) -> list[AutomationTriggerEvent]:
    db.init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if trigger_id:
        clauses.append("trigger_id = ?")
        params.append(trigger_id)
    if statuses:
        ordered = sorted(status.value for status in statuses)
        clauses.append(f"status IN ({','.join('?' for _ in ordered)})")
        params.extend(ordered)
    query = "SELECT data FROM automation_trigger_events"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at, id LIMIT ?"
    params.append(max(1, min(int(limit or 200), 1000)))
    with db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [AutomationTriggerEvent.model_validate_json(row["data"]) for row in rows]


def update_trigger_event(
    event_id: str,
    *,
    status: TriggerEventStatus,
    run_id: str = "",
    last_error_code: str = "",
    stable_at: str = "",
    increment_attempts: bool = False,
) -> AutomationTriggerEvent | None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM automation_trigger_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        event = AutomationTriggerEvent.model_validate_json(row["data"])
        event.status = status
        event.run_id = run_id or event.run_id
        event.last_error_code = str(last_error_code or "")
        event.stable_at = stable_at or event.stable_at
        if increment_attempts:
            event.attempts += 1
        event.updated_at = now_iso()
        conn.execute(
            """
            UPDATE automation_trigger_events
            SET status = ?, run_id = ?, data = ?, updated_at = ?
            WHERE id = ?
            """,
            (event.status, event.run_id or None, event.model_dump_json(), event.updated_at, event.id),
        )
    return event


def upsert_run_item(item: AutomationRunItem) -> AutomationRunItem:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, created_at FROM automation_run_items WHERE run_id = ? AND item_key = ?",
            (item.run_id, item.item_key),
        ).fetchone()
        if existing is not None:
            item.id = str(existing["id"])
            item.created_at = str(existing["created_at"])
            item.updated_at = now_iso()
        conn.execute(
            """
            INSERT INTO automation_run_items (id, run_id, item_key, status, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, item_key) DO UPDATE SET
                status=excluded.status,
                data=excluded.data,
                updated_at=excluded.updated_at
            """,
            (
                item.id,
                item.run_id,
                item.item_key,
                item.status,
                item.model_dump_json(),
                item.created_at,
                item.updated_at,
            ),
        )
    return item


def create_execution_exception(exception: ExecutionException) -> ExecutionException:
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO execution_exceptions (
                id, run_id, item_id, category, status, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                exception.id,
                exception.run_id,
                exception.item_id or None,
                exception.category,
                exception.status,
                exception.model_dump_json(),
                exception.created_at,
                exception.updated_at,
            ),
        )
    return exception


def list_execution_exceptions(
    *,
    run_id: str = "",
    status: str = "",
    limit: int = 500,
) -> list[ExecutionException]:
    db.init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    query = "SELECT data FROM execution_exceptions"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 500), 1000)))
    with db.connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [ExecutionException.model_validate_json(row["data"]) for row in rows]


def resolve_execution_exception(exception_id: str, resolution: dict[str, Any]) -> ExecutionException | None:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM execution_exceptions WHERE id = ?", (exception_id,)).fetchone()
        if row is None:
            return None
        exception = ExecutionException.model_validate_json(row["data"])
        if exception.status == ExceptionStatus.RESOLVED:
            if exception.resolution != resolution:
                raise ValueError("execution exception is already resolved")
            return exception
        if exception.status != ExceptionStatus.OPEN:
            raise ValueError(f"execution exception status is {exception.status}")
        exception.status = ExceptionStatus.RESOLVED
        exception.resolution = dict(resolution)
        exception.updated_at = now_iso()
        conn.execute(
            "UPDATE execution_exceptions SET status = ?, data = ?, updated_at = ? WHERE id = ?",
            (exception.status, exception.model_dump_json(), exception.updated_at, exception.id),
        )
    return exception


def _template_version(
    template_id: str,
    version: int,
    *,
    goal_template: str,
    variable_schema: dict[str, Any] | None,
    steps: list[ConnectorStep] | None,
    semantic_locators: dict[str, Any] | None,
    fallback_locators: dict[str, Any] | None,
    assertions: list[dict[str, Any]] | None,
    connector_versions: dict[str, str] | None,
    provenance: list[ContentEnvelope] | None,
) -> AutomationTemplateVersion:
    payload = {
        "goal_template": goal_template,
        "variable_schema": variable_schema or {},
        "steps": [step.model_dump(mode="json") for step in (steps or [])],
        "semantic_locators": semantic_locators or {},
        "fallback_locators": fallback_locators or {},
        "assertions": assertions or [],
        "connector_versions": connector_versions or {},
        "provenance": [item.model_dump(mode="json") for item in (provenance or [])],
    }
    return AutomationTemplateVersion(
        template_id=template_id,
        version=version,
        content_hash=stable_content_hash(payload),
        **payload,
    )


def _write_template(conn, template: AutomationTemplate) -> None:
    conn.execute(
        """
        INSERT INTO automation_templates (id, name, enabled, current_version, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            enabled=excluded.enabled,
            current_version=excluded.current_version,
            data=excluded.data,
            updated_at=excluded.updated_at
        """,
        (
            template.id,
            template.name,
            1 if template.enabled else 0,
            template.current_version,
            template.model_dump_json(),
            template.created_at,
            template.updated_at,
        ),
    )


def _write_template_version(conn, version: AutomationTemplateVersion) -> None:
    conn.execute(
        """
        INSERT INTO automation_template_versions (id, template_id, version, content_hash, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            version.id,
            version.template_id,
            version.version,
            version.content_hash,
            version.model_dump_json(),
            version.created_at,
        ),
    )


def _write_grant(grant: ApplicationGrant, *, conn=None) -> None:
    if conn is None:
        db.init_db()
        with db.connect() as connection:
            _write_grant(grant, conn=connection)
        return
    conn.execute(
        """
        INSERT INTO application_grants (id, app_id, status, expires_at, data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            status=excluded.status,
            expires_at=excluded.expires_at,
            data=excluded.data,
            updated_at=excluded.updated_at
        """,
        (
            grant.id,
            grant.app_id,
            grant.status,
            grant.expires_at,
            grant.model_dump_json(),
            grant.created_at,
            grant.updated_at,
        ),
    )


def _expire_grant(grant: ApplicationGrant, *, now: datetime) -> bool:
    if grant.status != GrantStatus.ACTIVE or grant.revoked_at or not grant.is_expired(now=now):
        return False
    grant.status = GrantStatus.EXPIRED
    grant.updated_at = now.isoformat()
    return True


def _audit_grant_expired(grant: ApplicationGrant) -> None:
    audit.record(
        "automation.grant.expired",
        "AutomationAuthorizationService",
        {"grant_id": grant.id, "app_id": grant.app_id, "expires_at": grant.expires_at},
    )


def _template_from_row(row) -> AutomationTemplate | None:
    return AutomationTemplate.model_validate_json(row["data"]) if row else None


def _run_idempotency_binding(run: AutomationRun) -> str:
    return stable_content_hash(
        {
            "template_id": run.template_id,
            "template_version": run.template_version,
            "task_id": run.task_id,
            "trigger_id": run.trigger_id,
            "input_values": run.input_values,
        }
    )
