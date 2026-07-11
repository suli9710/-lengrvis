from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.automation import intent_capsule, run_budget, store
from app.automation.models import (
    AutomationRun,
    AutomationRunItem,
    AutomationStatus,
    AutomationTrigger,
    BudgetConsumeRequest,
    ConnectorStep,
    ExecutionException,
    RunBudgetLimits,
    TriggerEventStatus,
)
from app.core.errors import SecurityError
from app.core.paths import resolve_authorized
from app.core.schemas import ContentEnvelope
from app.llm.registry import get_effective_settings
from app.policy.redaction import redact_run_payload

router = APIRouter()


class StrictAutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateVersionRequest(StrictAutomationRequest):
    goal_template: str = Field(min_length=1, max_length=16000)
    variable_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[ConnectorStep] = Field(default_factory=list)
    semantic_locators: dict[str, Any] = Field(default_factory=dict)
    fallback_locators: dict[str, Any] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    connector_versions: dict[str, str] = Field(default_factory=dict)
    provenance: list[ContentEnvelope] = Field(default_factory=list)


class CreateTemplateRequest(TemplateVersionRequest):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class CreateTemplateVersionRequest(TemplateVersionRequest):
    pass


class CreateGrantRequest(StrictAutomationRequest):
    app_id: str = Field(min_length=1, max_length=256)
    capabilities: list[str] = Field(min_length=1)
    data_scopes: list[str] = Field(default_factory=list)
    days: int = Field(default=30, ge=1, le=30)
    app_identity_fingerprint: str = Field(default="", max_length=512)

    @field_validator("capabilities")
    @classmethod
    def require_capabilities(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip() for item in value if str(item).strip()})
        if not normalized:
            raise ValueError("application grant requires at least one capability")
        return normalized


class IssueIntentRequest(StrictAutomationRequest):
    task_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1, max_length=16000)
    plan_revision: int = Field(ge=1)
    allowed_tools: list[str] = Field(min_length=1)
    resource_scope: list[str] = Field(default_factory=list)
    data_egress_scope: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1, max_length=256)
    ttl_seconds: int = Field(default=intent_capsule.DEFAULT_INTENT_TTL_SECONDS, ge=60, le=3600)

    @field_validator("allowed_tools")
    @classmethod
    def require_allowed_tools(cls, value: list[str]) -> list[str]:
        normalized = sorted({str(item).strip() for item in value if str(item).strip()})
        if not normalized:
            raise ValueError("intent capsule requires at least one allowed tool")
        return normalized


class VerifyIntentRequest(StrictAutomationRequest):
    token: str = Field(min_length=1, max_length=65536)
    task_id: str = Field(min_length=1)
    user_goal: str = Field(min_length=1, max_length=16000)
    plan_revision: int = Field(ge=1)
    policy_version: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(default="", max_length=256)
    resource: str = Field(default="", max_length=4096)
    data_egress: str = Field(default="", max_length=512)


class CreateRunRequest(StrictAutomationRequest):
    template_id: str = Field(min_length=1)
    template_version: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=8, max_length=256)
    task_id: str = ""
    trigger_id: str = ""
    input_values: dict[str, Any] = Field(default_factory=dict)


class CreateTriggerRequest(StrictAutomationRequest):
    template_id: str = Field(min_length=1)
    kind: Literal["directory"] = "directory"
    directory: str = Field(min_length=1, max_length=4096)
    suffixes: list[str] = Field(default_factory=lambda: [".csv", ".xlsx"])
    events: list[Literal["created", "modified", "moved"]] = Field(
        default_factory=lambda: ["created", "moved"], min_length=1
    )
    debounce_seconds: float = Field(default=2.0, ge=0.25, le=120)
    stable_seconds: float = Field(default=2.0, ge=0.5, le=300)
    enabled: bool = True

    @field_validator("suffixes")
    @classmethod
    def normalize_suffixes(cls, value: list[str]) -> list[str]:
        normalized: set[str] = set()
        for item in value:
            suffix = str(item).strip().lower()
            if not suffix or suffix == ".":
                continue
            normalized.add(suffix if suffix.startswith(".") else f".{suffix}")
        if not normalized:
            raise ValueError("automation trigger requires at least one suffix")
        return sorted(normalized)


class CreateBudgetRequest(StrictAutomationRequest):
    limits: RunBudgetLimits | None = None


class UpsertRunItemRequest(StrictAutomationRequest):
    item_key: str = Field(min_length=1, max_length=512)
    status: AutomationStatus = AutomationStatus.DRAFT
    source: ContentEnvelope | None = None
    input_values: dict[str, Any] = Field(default_factory=dict)
    output_values: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)


class CreateExecutionExceptionRequest(StrictAutomationRequest):
    run_id: str = Field(min_length=1)
    item_id: str = ""
    category: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)
    safe_context: dict[str, Any] = Field(default_factory=dict)
    resolution_options: list[dict[str, Any]] = Field(default_factory=list)
    requires_desktop: bool = False


class ResolveExceptionRequest(StrictAutomationRequest):
    resolution: dict[str, Any] = Field(default_factory=dict)


@router.get("/automation/templates")
def list_automation_templates(limit: int = Query(default=200, ge=1, le=500)) -> list:
    return store.list_templates(limit=limit)


@router.post("/automation/templates", status_code=201)
def create_automation_template(payload: CreateTemplateRequest) -> dict[str, Any]:
    try:
        template, version = store.create_template(**payload.model_dump())
    except ValueError:
        raise HTTPException(status_code=422, detail="Automation template validation failed") from None
    return {"template": template, "version": version}


@router.get("/automation/templates/{template_id}")
def get_automation_template(template_id: str, version: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    template = store.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Automation template not found")
    template_version = store.get_template_version(template_id, version)
    if template_version is None:
        raise HTTPException(status_code=404, detail="Automation template version not found")
    return {"template": template, "version": template_version}


@router.post("/automation/templates/{template_id}/versions", status_code=201)
def create_automation_template_version(template_id: str, payload: CreateTemplateVersionRequest):
    try:
        return store.add_template_version(
            template_id,
            **payload.model_dump(),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation template not found") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Automation template validation failed") from None


@router.get("/automation/triggers")
def list_automation_triggers(
    template_id: str = "", limit: int = Query(default=200, ge=1, le=500)
) -> list[AutomationTrigger]:
    return store.list_triggers(template_id=template_id, limit=limit)


@router.post("/automation/triggers", status_code=201)
def create_automation_trigger(payload: CreateTriggerRequest) -> AutomationTrigger:
    try:
        authorized_directory = resolve_authorized(
            payload.directory,
            get_effective_settings().allowed_directories,
        )
    except (SecurityError, OSError, ValueError):
        raise HTTPException(status_code=403, detail="Automation trigger directory is not authorized") from None
    if not authorized_directory.is_dir():
        raise HTTPException(status_code=422, detail="Automation trigger directory must already exist")
    try:
        return store.create_trigger(
            AutomationTrigger(**payload.model_copy(update={"directory": str(authorized_directory)}).model_dump())
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Automation template not found") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="Automation trigger validation failed") from None


@router.get("/automation/grants")
def list_automation_grants(app_id: str = "", limit: int = Query(default=200, ge=1, le=500)) -> list:
    return store.list_application_grants(app_id=app_id, limit=limit)


@router.post("/automation/grants", status_code=201)
def create_automation_grant(payload: CreateGrantRequest) -> dict[str, Any]:
    grant = store.create_application_grant(**payload.model_dump())
    return {
        "grant": grant,
        "execution_authorized": False,
        "next_requirement": "Issue and validate a task-scoped IntentCapsule for every run.",
    }


@router.delete("/automation/grants/{grant_id}")
def revoke_automation_grant(grant_id: str):
    grant = store.revoke_application_grant(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Application grant not found")
    return grant


@router.post("/automation/intent-capsules", status_code=201)
def create_intent_capsule(payload: IssueIntentRequest):
    return intent_capsule.issue_intent_capsule(**payload.model_dump())


@router.post("/automation/intent-capsules/verify")
def verify_intent_capsule(payload: VerifyIntentRequest):
    try:
        capsule = intent_capsule.verify_intent_capsule(**payload.model_dump())
    except intent_capsule.IntentCapsuleError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "capsule": capsule}


@router.delete("/automation/intent-capsules/{capsule_id}")
def revoke_intent_capsule(capsule_id: str):
    capsule = intent_capsule.revoke_intent_capsule(capsule_id)
    if capsule is None:
        raise HTTPException(status_code=404, detail="Intent capsule not found")
    return capsule


@router.post("/automation/runs", status_code=201)
def create_automation_run(payload: CreateRunRequest) -> dict[str, Any]:
    template = store.get_template(payload.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Automation template not found")
    version = payload.template_version or template.current_version
    if store.get_template_version(payload.template_id, version) is None:
        raise HTTPException(status_code=404, detail="Automation template version not found")
    try:
        run = store.create_automation_run(
            AutomationRun(
                template_id=payload.template_id,
                template_version=version,
                task_id=payload.task_id,
                trigger_id=payload.trigger_id,
                input_values=payload.input_values,
                idempotency_key=payload.idempotency_key,
                status=AutomationStatus.DRAFT,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    budget = run_budget.create_run_budget(run.id)
    return {"run": _redacted_model_payload(run), "budget": budget}


@router.get("/automation/runs")
def list_automation_runs(
    status: str = "",
    trigger_id: str = "",
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    return [
        _redacted_model_payload(run)
        for run in store.list_automation_runs(status=status, trigger_id=trigger_id, limit=limit)
    ]


@router.get("/automation/runs/{run_id}")
def get_automation_run(run_id: str) -> dict[str, Any]:
    run = store.get_automation_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    items = store.list_run_items(run_id)
    exceptions = store.list_execution_exceptions(run_id=run_id)
    return {
        "run": _redacted_model_payload(run),
        "items": [_redacted_model_payload(item) for item in items],
        "exceptions": [_redacted_model_payload(item) for item in exceptions],
    }


@router.get("/automation/trigger-events")
def list_automation_trigger_events(
    trigger_id: str = "",
    status: TriggerEventStatus | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    statuses = {status} if status is not None else None
    return [
        _redacted_model_payload(event)
        for event in store.list_trigger_events(trigger_id=trigger_id, statuses=statuses, limit=limit)
    ]


@router.post("/automation/runs/{run_id}/items", status_code=201)
def upsert_automation_run_item(run_id: str, payload: UpsertRunItemRequest):
    if store.get_automation_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    item = store.upsert_run_item(AutomationRunItem(run_id=run_id, **payload.model_dump()))
    return _redacted_model_payload(item)


@router.get("/automation/runs/{run_id}/budget")
def get_automation_run_budget(run_id: str):
    ledger = run_budget.get_run_budget(run_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="Run budget not found")
    return ledger


@router.post("/automation/runs/{run_id}/budget")
def create_automation_run_budget(run_id: str, payload: CreateBudgetRequest):
    if store.get_automation_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    existing = run_budget.get_run_budget(run_id)
    if existing is not None:
        if payload.limits is None:
            return existing
        try:
            return run_budget.tighten_run_budget(run_id, payload.limits)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return run_budget.create_run_budget(run_id, limits=payload.limits)


@router.post("/automation/runs/{run_id}/budget/consume")
def consume_automation_run_budget(run_id: str, payload: BudgetConsumeRequest):
    try:
        return run_budget.consume_run_budget(run_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run budget not found") from None


@router.post("/automation/exceptions", status_code=201)
def create_execution_exception(payload: CreateExecutionExceptionRequest):
    if store.get_automation_run(payload.run_id) is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    exception = store.create_execution_exception(ExecutionException(**payload.model_dump()))
    return _redacted_model_payload(exception)


@router.post("/automation/exceptions/{exception_id}/resolve")
def resolve_execution_exception(exception_id: str, payload: ResolveExceptionRequest):
    try:
        exception = store.resolve_execution_exception(exception_id, payload.resolution)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if exception is None:
        raise HTTPException(status_code=404, detail="Execution exception not found")
    return _redacted_model_payload(exception)


def _redacted_model_payload(model: BaseModel) -> dict[str, Any]:
    payload = redact_run_payload(model.model_dump(mode="json"))
    return payload if isinstance(payload, dict) else {}
