"""Persistent personal matters and user-owned autonomy boundaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.office_models import OfficeArtifact
from app.core.schemas import new_id, now_iso


class MatterStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EVENT = "waiting_event"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_MATTER_STATUSES = {MatterStatus.COMPLETED, MatterStatus.FAILED, MatterStatus.CANCELLED}
ScopeAction = Literal["observe", "file_modify", "ui_edit", "browser_prepare"]


def _timestamp(value: str) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Dates must include their timezone.")
        return parsed.isoformat()
    return value


class AutonomyScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("scope"))
    version: int = Field(default=1, ge=1)
    label: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    directories: list[str] = Field(default_factory=list, max_length=32)
    applications: list[str] = Field(default_factory=list, max_length=32)
    origins: list[str] = Field(default_factory=list, max_length=32)
    allowed_actions: list[ScopeAction] = Field(default_factory=lambda: ["observe"], max_length=4)
    account_id: str = Field(default="", max_length=256)
    authorization: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("origins")
    @classmethod
    def exact_origins(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            parts = urlsplit(value.strip())
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or "*" in value
            ):
                raise ValueError("Web scopes require exact HTTP(S) origins without credentials, paths or wildcards.")
            host = parts.hostname.lower()
            host = f"[{host}]" if ":" in host else host
            port = parts.port
            authority = host if port is None or port == (443 if parts.scheme == "https" else 80) else f"{host}:{port}"
            result.append(f"{parts.scheme}://{authority}")
        return list(dict.fromkeys(result))


class MatterInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("interaction"))
    kind: Literal["information", "choose_matter", "choose_dependency", "approval", "takeover", "reconcile"] = (
        "information"
    )
    message: str = Field(min_length=1, max_length=3000)
    fields: list[str] = Field(default_factory=list, max_length=16)
    candidates: list[str] = Field(default_factory=list, max_length=20)
    approval_id: str = ""


class WaitCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file_exists", "file_changed", "time", "browser_change", "provider", "user", "desktop_idle"]
    subject: str = Field(default="", max_length=2048)
    baseline: str = Field(default="", max_length=256)
    due_at: str = ""
    _validate_due = field_validator("due_at")(_timestamp)

    @model_validator(mode="after")
    def valid_wait(self):
        if self.kind in {"time", "provider"} and not self.due_at:
            raise ValueError("A timed wait requires a concrete due_at.")
        if self.kind in {"file_exists", "file_changed", "browser_change"} and not self.subject:
            raise ValueError("A source wait requires its exact subject.")
        return self


class OutcomeCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=160)
    selector: str = Field(default="", max_length=2048)
    path: list[str | int] = Field(default_factory=list, max_length=16)
    operator: Literal["equals", "nonempty"] = "equals"
    expected: str | int | float | bool | None = None

    @field_validator("expected")
    @classmethod
    def finite_bounded_value(cls, value):
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("Expected numbers must be finite.")
        if isinstance(value, str) and len(value) > 65536:
            raise ValueError("Expected text exceeds the observation limit.")
        return value


class CompletionCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "file_exists",
        "file_text",
        "file_equal",
        "file_json",
        "ui_text",
        "browser_text",
        "browser_fields",
        "execution_evidence",
        "office_artifacts",
    ]
    subject: str = Field(default="", max_length=2048)
    reference: str = Field(default="", max_length=2048)
    expected: str | None = Field(default=None, max_length=65536)
    selector: dict[str, Any] = Field(default_factory=dict)
    checks: list[OutcomeCheck] = Field(default_factory=list, max_length=32)
    artifact_kinds: list[Literal["xlsx", "docx"]] = Field(default_factory=list, max_length=2)
    require_open: bool = False

    @model_validator(mode="after")
    def structured_checks(self):
        if self.kind in {"file_json", "browser_fields"}:
            if not self.checks or len({check.label for check in self.checks}) != len(self.checks):
                raise ValueError("Structured outcomes require uniquely labeled checks.")
            if self.kind == "browser_fields" and any(not check.selector for check in self.checks):
                raise ValueError("Browser outcomes require exact field selectors.")
        return self


class ButlerMatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("matter"))
    version: int = Field(default=1, ge=1)
    goal: str = Field(min_length=1, max_length=12000)
    source: Literal["user", "event", "schedule"] = "user"
    source_key: str = Field(default="", max_length=256)
    source_evidence: dict[str, Any] = Field(default_factory=dict)
    status: MatterStatus = MatterStatus.QUEUED
    mode: Literal["efficiency", "privacy", "hybrid"] = "efficiency"
    priority: int = Field(default=50, ge=0, le=100)
    priority_explicit: bool = False
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    dependency_revisions: dict[str, int] = Field(default_factory=dict)
    outcome_revision: int = 1
    outcome_revoked: bool = False
    coordination_suggestions: list[str] = Field(default_factory=list, max_length=16)
    pending_notifications: dict[str, dict[str, str]] = Field(default_factory=dict)
    memory_inputs: list[str] = Field(default_factory=list)
    memory_observation_keys: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    due_at: str = ""
    _validate_due = field_validator("due_at")(_timestamp)
    constraints: dict[str, Any] = Field(default_factory=dict)
    answers: dict[str, Any] = Field(default_factory=dict)
    scope_versions: dict[str, int] = Field(default_factory=dict)
    completion_conditions: list[CompletionCondition] = Field(default_factory=list, max_length=16)
    interaction: MatterInteraction | None = None
    waiting: WaitCondition | None = None
    task_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    active_task_id: str = ""
    active_run_id: str = ""
    execution_ticket: str = ""
    completion_evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[OfficeArtifact] = Field(default_factory=list, max_length=16)
    progress: str = "等待处理"
    no_progress_count: int = 0
    last_progress_key: str = ""
    continuation_count: int = 0
    last_notified_key: str = ""
    notified_keys: list[str] = Field(default_factory=list, max_length=64)
    resume_after_global_pause: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class MatterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    interaction_id: str = Field(min_length=1, max_length=256)
    message: str = Field(default="", max_length=12000)
    answers: dict[str, Any] = Field(default_factory=dict)


class ButlerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=12000)
    mode: Literal["efficiency", "privacy", "hybrid"] = "efficiency"
    matter_id: str = ""


class MatterArrangement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    depends_on: list[str] | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=0, le=100)
    due_at: str | None = None

    @field_validator("due_at")
    @classmethod
    def valid_due(cls, value):
        return _timestamp(value) if value is not None else value


class MemoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=2, max_length=600)
    evidence: str = Field(min_length=2, max_length=1200)
    kind: Literal["fact", "preference"] = "fact"
    basis: Literal["user_statement", "inference"] = "user_statement"
    reason: str = Field(min_length=1, max_length=300)
    supersedes: str = Field(default="", max_length=256)
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


class MatterDecision(BaseModel):
    """Model proposals contain no grants, approval decisions or execution evidence."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["execute", "ask", "wait", "ignore", "remember"]
    goal: str = ""
    reason: str = ""
    priority: int | None = Field(default=None, ge=0, le=100)
    coordination_suggestions: list[str] = Field(default_factory=list, max_length=16)
    memories: list[MemoryProposal] = Field(default_factory=list, max_length=3)
    due_at: str = ""
    _validate_due = field_validator("due_at")(_timestamp)
    missing_fields: list[str] = Field(default_factory=list, max_length=16)
    constraints: dict[str, Any] = Field(default_factory=dict)
    completion_conditions: list[CompletionCondition] = Field(default_factory=list, max_length=16)
    waiting: WaitCondition | None = None
