from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.content_provenance import stable_content_hash
from app.core.schemas import ContentEnvelope, new_id, now_iso

MAX_APPLICATION_GRANT_DAYS = 30


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class AutomationModel(BaseModel):
    """Strict base for persisted and API-facing automation contracts."""

    model_config = ConfigDict(extra="forbid")


class AutomationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExceptionStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class GrantStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class TriggerEventStatus(StrEnum):
    OBSERVED = "observed"
    RUN_CREATED = "run_created"
    FAILED = "failed"


class ConnectorStep(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("connector_step"), min_length=1)
    connector: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_observation: str = ""
    writes_state: bool = False
    external_send: bool = False


class AutomationTemplate(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_template"))
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    current_version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class AutomationTemplateVersion(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_version"))
    template_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    goal_template: str = Field(min_length=1, max_length=16000)
    variable_schema: dict[str, Any] = Field(default_factory=dict)
    steps: list[ConnectorStep] = Field(default_factory=list)
    semantic_locators: dict[str, Any] = Field(default_factory=dict)
    fallback_locators: dict[str, Any] = Field(default_factory=dict)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    connector_versions: dict[str, str] = Field(default_factory=dict)
    provenance: list[ContentEnvelope] = Field(default_factory=list)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: str = Field(default_factory=now_iso)

    @model_validator(mode="after")
    def validate_step_graph(self) -> AutomationTemplateVersion:
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("automation template contains duplicate step ids")
        known = set(step_ids)
        for step in self.steps:
            missing = sorted(set(step.depends_on) - known)
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps: {', '.join(missing)}")
        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {step.id: set(step.depends_on) for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("automation template step graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)

        expected_hash = stable_content_hash(
            {
                "goal_template": self.goal_template,
                "variable_schema": self.variable_schema,
                "steps": [step.model_dump(mode="json") for step in self.steps],
                "semantic_locators": self.semantic_locators,
                "fallback_locators": self.fallback_locators,
                "assertions": self.assertions,
                "connector_versions": self.connector_versions,
                "provenance": [item.model_dump(mode="json") for item in self.provenance],
            }
        )
        if self.content_hash != expected_hash:
            raise ValueError("automation template version content hash does not match payload")
        return self


class AutomationTrigger(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_trigger"))
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
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

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


class AutomationTriggerEvent(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_trigger_event"))
    trigger_id: str = Field(min_length=1)
    path: str = Field(min_length=1, max_length=4096)
    action: str = Field(default="upsert", min_length=1, max_length=32)
    content_hash: str = Field(min_length=1, max_length=128)
    event_key: str = Field(min_length=1, max_length=256)
    status: TriggerEventStatus = TriggerEventStatus.OBSERVED
    run_id: str = ""
    attempts: int = Field(default=0, ge=0, le=100)
    last_error_code: str = Field(default="", max_length=128)
    observed_at: str = Field(default_factory=now_iso)
    stable_at: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ApplicationGrant(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("application_grant"))
    app_id: str = Field(min_length=1, max_length=256)
    capabilities: list[str] = Field(min_length=1)
    data_scopes: list[str] = Field(default_factory=list)
    status: GrantStatus = GrantStatus.ACTIVE
    issued_at: str = Field(default_factory=now_iso)
    expires_at: str
    revoked_at: str = ""
    app_identity_fingerprint: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("capabilities", "data_scopes")
    @classmethod
    def normalize_scopes(cls, value: list[str]) -> list[str]:
        return sorted({str(item).strip() for item in value if str(item).strip()})

    @model_validator(mode="after")
    def enforce_max_duration(self) -> ApplicationGrant:
        if not self.capabilities:
            raise ValueError("application grant requires at least one capability")
        issued = parse_utc(self.issued_at)
        expires = parse_utc(self.expires_at)
        if expires <= issued:
            raise ValueError("application grant expiry must be after issuance")
        if expires - issued > timedelta(days=MAX_APPLICATION_GRANT_DAYS):
            raise ValueError("application grant cannot exceed 30 days")
        if self.status == GrantStatus.ACTIVE and self.revoked_at:
            raise ValueError("active application grant cannot have a revocation timestamp")
        if self.status == GrantStatus.REVOKED and not self.revoked_at:
            raise ValueError("revoked application grant requires a revocation timestamp")
        return self

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return parse_utc(self.expires_at) <= current

    def permits_consideration(self, capability: str, *, now: datetime | None = None) -> bool:
        return (
            self.status == GrantStatus.ACTIVE
            and not self.revoked_at
            and not self.is_expired(now=now)
            and capability in self.capabilities
        )


class AutomationRun(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_run"))
    template_id: str = Field(min_length=1)
    template_version: int = Field(ge=1)
    task_id: str = ""
    status: AutomationStatus = AutomationStatus.DRAFT
    idempotency_key: str = Field(min_length=8, max_length=256)
    trigger_id: str = ""
    input_values: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class AutomationRunItem(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("automation_item"))
    run_id: str = Field(min_length=1)
    item_key: str = Field(min_length=1, max_length=512)
    status: AutomationStatus = AutomationStatus.DRAFT
    source: ContentEnvelope | None = None
    input_values: dict[str, Any] = Field(default_factory=dict)
    output_values: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ExecutionException(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("execution_exception"))
    run_id: str = Field(min_length=1)
    item_id: str = ""
    category: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)
    safe_context: dict[str, Any] = Field(default_factory=dict)
    resolution_options: list[dict[str, Any]] = Field(default_factory=list)
    requires_desktop: bool = False
    status: ExceptionStatus = ExceptionStatus.OPEN
    resolution: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class IntentCapsule(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("intent"))
    task_id: str = Field(min_length=1)
    user_goal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_revision: int = Field(ge=1)
    allowed_tools: list[str] = Field(default_factory=list)
    resource_scope: list[str] = Field(default_factory=list)
    data_egress_scope: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1, max_length=256)
    expires_at: str
    nonce: str = Field(min_length=16, max_length=256)
    status: Literal["active", "revoked", "expired"] = "active"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("allowed_tools", "resource_scope", "data_egress_scope")
    @classmethod
    def normalize_boundaries(cls, value: list[str]) -> list[str]:
        return sorted({str(item).strip() for item in value if str(item).strip()})

    @model_validator(mode="after")
    def require_allowed_tools(self) -> IntentCapsule:
        if not self.allowed_tools:
            raise ValueError("intent capsule requires at least one allowed tool")
        return self


class SignedIntentCapsule(AutomationModel):
    capsule: IntentCapsule
    token: str


class RunBudgetLimits(AutomationModel):
    max_tool_calls: int = Field(default=50, ge=1, le=10000)
    max_writes: int = Field(default=20, ge=0, le=10000)
    max_external_sends: int = Field(default=10, ge=0, le=10000)
    max_recipients: int = Field(default=10, ge=0, le=10000)
    max_domains: int = Field(default=5, ge=0, le=1000)
    max_ui_inputs: int = Field(default=100, ge=0, le=100000)
    max_retries: int = Field(default=5, ge=0, le=1000)
    max_subprocesses: int = Field(default=0, ge=0, le=1000)
    max_parallel_fanout: int = Field(default=4, ge=1, le=128)
    max_wall_clock_seconds: int = Field(default=900, ge=1, le=86400)
    max_duplicate_actions: int = Field(default=1, ge=0, le=100)


class RunBudgetUsage(AutomationModel):
    tool_calls: int = 0
    writes: int = 0
    external_sends: int = 0
    ui_inputs: int = 0
    retries: int = 0
    subprocesses: int = 0
    max_parallel_fanout_seen: int = 0
    recipients: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    duplicate_actions: dict[str, int] = Field(default_factory=dict)


class RunBudgetLedger(AutomationModel):
    id: str = Field(default_factory=lambda: new_id("budget"))
    run_id: str = Field(min_length=1)
    status: Literal["active", "soft_exceeded", "hard_stopped"] = "active"
    limits: RunBudgetLimits = Field(default_factory=RunBudgetLimits)
    usage: RunBudgetUsage = Field(default_factory=RunBudgetUsage)
    version: int = Field(default=1, ge=1)
    hard_stop_reason: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class BudgetConsumeRequest(AutomationModel):
    kind: Literal["tool_call", "write", "external_send", "ui_input", "retry", "subprocess", "parallel"]
    amount: int = Field(default=1, ge=1, le=10000)
    recipient: str = ""
    domain: str = ""
    action_fingerprint: str = Field(default="", max_length=256)
    parallel_fanout: int = Field(default=0, ge=0, le=10000)


class BudgetDecision(AutomationModel):
    allowed: bool
    soft_exceeded: bool = False
    hard_exceeded: bool = False
    reason: str = ""
    ledger: RunBudgetLedger
