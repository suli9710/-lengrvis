from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import ContentEnvelope, ToolResult


class ConnectorPhase(StrEnum):
    PROBE = "probe"
    OBSERVE = "observe"
    PREVIEW = "preview"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECOVER = "recover"
    HANDOFF = "handoff"


class ConnectorOutcome(StrEnum):
    READY = "ready"
    OBSERVED = "observed"
    PREVIEWED = "previewed"
    EXECUTED = "executed"
    VERIFIED = "verified"
    RECOVERED = "recovered"
    HANDOFF_REQUIRED = "handoff_required"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ConnectorDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    connector_id: str
    version: str
    display_name: str
    description: str = ""
    formats: list[str] = Field(default_factory=list)
    semantic_actions: list[str] = Field(default_factory=list)
    application_families: list[str] = Field(default_factory=list)
    tool_mappings: dict[str, list[str]] = Field(default_factory=dict)


class ConnectorContext(BaseModel):
    task_id: str
    run_id: str = ""
    intent_capsule_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def runtime_context(self) -> dict[str, Any]:
        return {
            **self.metadata,
            "task_id": self.task_id,
            "automation_run_id": self.run_id,
            "intent_capsule_id": self.intent_capsule_id,
        }


class ConnectorRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    context: ConnectorContext
    content_envelopes: list[ContentEnvelope] = Field(default_factory=list)


class ToolInvocation(BaseModel):
    id: str = Field(default_factory=lambda: f"connector_call_{uuid4().hex}")
    connector_id: str
    connector_version: str
    phase: ConnectorPhase
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    task_scope: str
    has_side_effect: bool = False
    input_envelopes: list[ContentEnvelope] = Field(default_factory=list)


class ToolInvocationResult(BaseModel):
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    rollback_info: dict[str, Any] = Field(default_factory=dict)
    content_envelope: ContentEnvelope | None = None

    @classmethod
    def from_value(cls, value: ToolInvocationResult | ToolResult | dict[str, Any]) -> ToolInvocationResult:
        if isinstance(value, cls):
            return value
        if isinstance(value, ToolResult):
            return cls(
                ok=value.ok,
                output=value.output,
                error=value.error,
                changed_paths=value.changed_paths,
                rollback_info=value.rollback_info,
                content_envelope=value.content_envelope,
            )
        payload = dict(value)
        known_result = any(
            key in payload for key in ("output", "error", "changed_paths", "rollback_info", "content_envelope")
        )
        output = dict(payload.get("output") or {}) if known_result else dict(payload)
        return cls(
            ok=bool(payload.get("ok", True)),
            output=output,
            error=str(payload.get("error") or ""),
            changed_paths=[str(item) for item in payload.get("changed_paths") or []],
            rollback_info=dict(payload.get("rollback_info") or {}),
            content_envelope=payload.get("content_envelope"),
        )


@runtime_checkable
class ToolInvoker(Protocol):
    """Execution boundary supplied by orchestration and backed by ToolRuntime."""

    async def invoke(self, invocation: ToolInvocation) -> ToolInvocationResult | ToolResult | dict[str, Any]: ...


class ConnectorHandoff(BaseModel):
    reason_code: str
    message: str
    required_actor: str = "user"
    resume_phase: ConnectorPhase = ConnectorPhase.OBSERVE
    guidance: list[str] = Field(default_factory=list)


class ConnectorResult(BaseModel):
    connector_id: str
    connector_version: str
    phase: ConnectorPhase
    outcome: ConnectorOutcome
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    content_envelope: ContentEnvelope
    planned_invocations: list[ToolInvocation] = Field(default_factory=list)
    executed_invocations: list[ToolInvocation] = Field(default_factory=list)
    handoff: ConnectorHandoff | None = None
    error: str = ""
