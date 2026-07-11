from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import StepPhase
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel, SafetyVerdict


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


DEFAULT_APPROVAL_TTL_SECONDS = 15 * 60
APPROVAL_TTL_SECONDS_BY_RISK: dict[str, int] = {
    RiskLevel.R0_READ_ONLY.value: 15 * 60,
    RiskLevel.R1_OPEN_ONLY.value: 15 * 60,
    RiskLevel.R2_REVERSIBLE_MODIFY.value: 10 * 60,
    RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value: 5 * 60,
    RiskLevel.R4_FORBIDDEN_OR_HANDOFF.value: 60,
}


def _parse_iso_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def approval_ttl_seconds(risk_level: RiskLevel | str | None) -> int:
    normalized = str(getattr(risk_level, "value", risk_level) or "").strip()
    return APPROVAL_TTL_SECONDS_BY_RISK.get(normalized, DEFAULT_APPROVAL_TTL_SECONDS)


def approval_expiry_iso(
    created_at: str,
    ttl_seconds: int | None = None,
    *,
    risk_level: RiskLevel | str | None = None,
) -> str:
    created = _parse_iso_datetime(created_at)
    if created is None:
        return str(created_at or "")
    effective_ttl = approval_ttl_seconds(risk_level) if ttl_seconds is None else max(1, int(ttl_seconds))
    return (created + timedelta(seconds=effective_ttl)).isoformat()


LEGACY_TASK_STATUS_MAP: dict[str, tuple[TaskPhase, ExecutionStage]] = {
    "created": (TaskPhase.CREATED, ExecutionStage.IDLE),
    "planning": (TaskPhase.PLANNING, ExecutionStage.IDLE),
    "reviewing_plan": (TaskPhase.PLAN_REVIEW, ExecutionStage.IDLE),
    "agent_consultation": (TaskPhase.CONSULTATION, ExecutionStage.IDLE),
    "plan_final_review": (TaskPhase.PLAN_REVIEW, ExecutionStage.IDLE),
    "waiting_user_approval": (TaskPhase.EXECUTION, ExecutionStage.AWAITING_APPROVAL),
    "executing_step": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "reviewing_tool_call": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "executing_tool": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "recording_observation": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "agent_discussion": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "reviewing_next_step": (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    "final_review": (TaskPhase.FINAL_REVIEW, ExecutionStage.IDLE),
    "completed": (TaskPhase.COMPLETED, ExecutionStage.IDLE),
    "denied": (TaskPhase.CANCELLED, ExecutionStage.IDLE),
    "failed": (TaskPhase.FAILED, ExecutionStage.IDLE),
    "paused": (TaskPhase.EXECUTION, ExecutionStage.PAUSED),
    "cancelled": (TaskPhase.CANCELLED, ExecutionStage.IDLE),
    "rolled_back": (TaskPhase.ROLLED_BACK, ExecutionStage.IDLE),
    "repair_required": (TaskPhase.REPAIR_REQUIRED, ExecutionStage.IDLE),
}


class TaskStatus:
    """Legacy constant facade backed by TaskPhase.

    Kept for older orchestrator call sites while the public Task model stores
    TaskPhase in ``status``.
    """

    CREATED = TaskPhase.CREATED
    PLANNING = TaskPhase.PLANNING
    REVIEWING_PLAN = TaskPhase.PLAN_REVIEW
    AGENT_CONSULTATION = TaskPhase.CONSULTATION
    PLAN_FINAL_REVIEW = TaskPhase.PLAN_REVIEW
    EXECUTION = TaskPhase.EXECUTION
    WAITING_USER_APPROVAL = "waiting_user_approval"
    EXECUTING_STEP = "executing_step"
    REVIEWING_TOOL_CALL = "reviewing_tool_call"
    EXECUTING_TOOL = "executing_tool"
    RECORDING_OBSERVATION = "recording_observation"
    AGENT_DISCUSSION = "agent_discussion"
    REVIEWING_NEXT_STEP = "reviewing_next_step"
    FINAL_REVIEW = TaskPhase.FINAL_REVIEW
    COMPLETED = TaskPhase.COMPLETED
    DENIED = TaskPhase.CANCELLED
    FAILED = TaskPhase.FAILED
    PAUSED = "paused"
    CANCELLED = TaskPhase.CANCELLED
    ROLLED_BACK = TaskPhase.ROLLED_BACK
    REPAIR_REQUIRED = TaskPhase.REPAIR_REQUIRED


class StepStatus(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    DENIED = "denied"
    WAITING_USER_APPROVAL = "waiting_user_approval"


class MessageType(StrEnum):
    PROPOSAL = "proposal"
    CRITIQUE = "critique"
    OBSERVATION = "observation"
    REVIEW = "review"
    REVISION = "revision"
    FINAL = "final"
    NOTIFICATION = "notification"


class OpenAIMessageRole(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryState(StrEnum):
    QUARANTINED = "quarantined"
    ACTIVE = "active"
    REVOKED = "revoked"


class MemoryConflictStatus(StrEnum):
    NONE = "none"
    CONFLICTING = "conflicting"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


MAX_USER_MESSAGE_CHARS = 16000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_USER_MESSAGE_CHARS)
    mode: str = Field(default="efficiency", max_length=64)


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("chat"))
    role: OpenAIMessageRole
    author: str
    content: str
    created_at: str = Field(default_factory=now_iso)
    status: str = "sent"


class ChatResponse(BaseModel):
    task_id: str | None = None
    status: TaskPhase | None = None
    message: str
    delegated: bool = False
    agent: str = "SupervisorAgent"


class RunEngine(StrEnum):
    AUTO = "auto"
    OS = "os"
    DEVELOPER = "developer"


class RunPhase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"

    @property
    def event_name(self) -> str:
        if self == RunPhase.AWAITING_APPROVAL:
            return "run.waiting_approval"
        if self == RunPhase.CANCELLED:
            return "run.cancelled"
        return f"run.{self.value}"


class Run(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    message: str
    mode: str = "efficiency"
    requested_engine: RunEngine = RunEngine.AUTO
    engine: RunEngine = RunEngine.AUTO
    phase: RunPhase = RunPhase.CREATED
    task_id: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class RunEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("runevt"))
    run_id: str
    name: str
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class RunCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_USER_MESSAGE_CHARS)
    mode: str = Field(default="efficiency", max_length=64)
    engine: RunEngine = RunEngine.AUTO
    agent_hint: str = Field(default="", max_length=128)


class RunCreateResponse(BaseModel):
    run_id: str
    engine: RunEngine
    phase: RunPhase
    engine_route_rule: str = ""
    engine_capabilities: dict[str, Any] = Field(default_factory=dict)


class RunStateResponse(BaseModel):
    run_id: str
    engine: RunEngine
    phase: RunPhase
    task_id: str | None = None
    message: str = ""
    mode: str = "efficiency"
    requested_engine: RunEngine = RunEngine.AUTO
    engine_route_rule: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    engine_capabilities: dict[str, Any] = Field(default_factory=dict)
    completion_evidence: dict[str, Any] = Field(default_factory=dict)
    result_quality: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    task_id: str = ""
    order: int = 0
    agent_name: str
    tool_name: str
    description: str
    args: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str = ""
    risk_level: RiskLevel = RiskLevel.R0_READ_ONLY
    requires_approval: bool = False
    status: StepStatus = StepStatus.PENDING
    step_phase: StepPhase = StepPhase.PENDING
    depends_on: list[str] = Field(default_factory=list)
    rollback_strategy: str = ""
    tool_effects: list[str] = Field(default_factory=list)
    resource_kinds: list[str] = Field(default_factory=list)
    trust_tier: str = ""
    deferred_tool: bool = False
    model_action: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    task_id: str = ""
    version: int = 1
    goal: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    global_risk_level: RiskLevel = RiskLevel.R0_READ_ONLY
    requires_user_approval: bool = False
    created_by_agent: str = "PlannerAgent"
    review_status: str = "pending"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    user_goal: str
    status: TaskPhase = TaskPhase.CREATED
    phase: TaskPhase = TaskPhase.CREATED
    execution_stage: ExecutionStage = ExecutionStage.IDLE
    mode: str = "efficiency"
    final_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def normalize_status_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        raw_status = normalized.get("status")
        raw_phase = normalized.get("phase")
        raw_stage = normalized.get("execution_stage")

        status_text = str(raw_status.value if isinstance(raw_status, StrEnum) else raw_status or "").strip()
        phase_text = str(raw_phase.value if isinstance(raw_phase, StrEnum) else raw_phase or "").strip()

        rollback = (normalized.get("metadata") or {}).get("rollback")
        if status_text == TaskPhase.FAILED.value and isinstance(rollback, dict) and rollback:
            rollback_phase = (
                TaskPhase.ROLLED_BACK
                if str(rollback.get("state") or "").strip().lower() == "succeeded"
                else TaskPhase.REPAIR_REQUIRED
            )
            normalized["status"] = rollback_phase
            normalized["phase"] = rollback_phase
            normalized["execution_stage"] = ExecutionStage.IDLE
            return normalized

        mapped = LEGACY_TASK_STATUS_MAP.get(status_text)
        if mapped is not None:
            phase, stage = mapped
            normalized["status"] = phase
            normalized["phase"] = raw_phase or phase
            normalized["execution_stage"] = raw_stage or stage
            return normalized

        if not status_text and phase_text:
            normalized["status"] = raw_phase
            normalized["phase"] = raw_phase
            return normalized

        if status_text:
            normalized["phase"] = raw_phase or raw_status
        return normalized


class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    task_id: str
    step_id: str | None = None
    role: OpenAIMessageRole = OpenAIMessageRole.ASSISTANT
    name: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    from_agent: str
    to_agent: str | None = None
    message_type: MessageType
    content: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def fill_openai_compat_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        from_agent = str(normalized.get("from_agent") or normalized.get("name") or "")
        metadata = dict(normalized.get("metadata") or {})

        if "role" not in normalized or not normalized.get("role"):
            normalized["role"] = (
                OpenAIMessageRole.USER.value
                if from_agent.lower() in {"user", "human"}
                else OpenAIMessageRole.ASSISTANT.value
            )

        if not normalized.get("name") and from_agent and normalized.get("role") != OpenAIMessageRole.TOOL.value:
            normalized["name"] = from_agent

        for key in ("from_agent", "to_agent", "message_type", "step_id"):
            if normalized.get(key) is not None:
                metadata.setdefault(key, normalized.get(key))

        if normalized.get("structured_payload"):
            metadata.setdefault("structured_payload", normalized["structured_payload"])

        normalized["metadata"] = metadata
        if not normalized.get("from_agent"):
            normalized["from_agent"] = str(
                metadata.get("from_agent") or normalized.get("name") or normalized.get("role") or "assistant"
            )
        if not normalized.get("message_type"):
            normalized["message_type"] = str(metadata.get("message_type") or MessageType.OBSERVATION.value)
        if "structured_payload" not in normalized:
            payload = metadata.get("structured_payload")
            normalized["structured_payload"] = payload if isinstance(payload, dict) else {}

        return normalized

    def to_openai_dict(self, *, include_legacy: bool = True) -> dict[str, Any]:
        message: dict[str, Any] = {
            "id": self.id,
            "role": self.role.value,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": {
                **self.metadata,
                "task_id": self.task_id,
                "step_id": self.step_id,
                "from_agent": self.from_agent,
                "to_agent": self.to_agent,
                "message_type": self.message_type.value,
            },
        }
        if self.name and self.role != OpenAIMessageRole.TOOL:
            message["name"] = self.name
        if self.tool_calls:
            message["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id

        if include_legacy:
            message.update(
                {
                    "task_id": self.task_id,
                    "step_id": self.step_id,
                    "from_agent": self.from_agent,
                    "to_agent": self.to_agent,
                    "message_type": self.message_type.value,
                    "structured_payload": self.structured_payload,
                }
            )
        return message


class SafetyReview(BaseModel):
    id: str = Field(default_factory=lambda: new_id("review"))
    task_id: str
    step_id: str | None = None
    target_type: str
    verdict: SafetyVerdict
    risk_level: RiskLevel
    reasons: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    user_confirmation_message: str = ""
    safe_alternative: str = ""
    created_at: str = Field(default_factory=now_iso)


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tool"))
    task_id: str
    step_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    execution_key: str = Field(default_factory=lambda: new_id("exec"))
    plan_revision: int = 0
    approval_id: str = ""
    status: str = "created"
    dry_run: bool = True
    started_at: str = ""
    committed_at: str = ""
    outcome_unknown_at: str = ""
    created_at: str = Field(default_factory=now_iso)


class ContentEnvelope(BaseModel):
    """Provenance and taint metadata that stays attached to derived content."""

    model_config = ConfigDict(extra="forbid")

    source_kind: str
    source_id: str = ""
    origin: str = ""
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trust_level: Literal["untrusted", "unknown", "internal", "user_confirmed", "trusted"] = "unknown"
    taint_flags: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=now_iso)
    task_scope: str = ""
    user_confirmed: bool = False
    sanitizers_applied: list[str] = Field(default_factory=list)
    integrity_hmac: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")


class ToolResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("result"))
    tool_call_id: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    rollback_info: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    content_envelope: ContentEnvelope | None = None
    created_at: str = Field(default_factory=now_iso)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    task_id: str
    step_id: str | None = None
    approval_type: str = "tool_call"
    message: str
    diff_preview: dict[str, Any] = Field(default_factory=dict)
    tool_name: str = ""
    risk_level: str = ""
    args_binding_hmac: str = ""
    preview_hmac: str = ""
    settings_fingerprint: str = ""
    permission_policy_version: str = ""
    policy_mode: str = "default"
    permission_mode: str = "default"
    tool_version: str = ""
    tool_trust_tier: str = ""
    tool_effects: list[str] = Field(default_factory=list)
    resource_kinds: list[str] = Field(default_factory=list)
    dry_run_summary: str = ""
    model_action: dict[str, Any] = Field(default_factory=dict)
    runtime_control_fields: dict[str, Any] = Field(default_factory=dict)
    runtime_fields: dict[str, Any] = Field(default_factory=dict)
    engineering_boundary: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    source_device_id: str = ""
    source_grant_id: str = ""
    allowed_device_ids: list[str] = Field(default_factory=list)
    required_mobile_scopes: list[str] = Field(default_factory=list)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = Field(default_factory=now_iso)
    expires_at: str = ""
    decided_at: str | None = None
    authorized_at: str | None = None
    auth_context: dict[str, Any] = Field(default_factory=dict)
    consumed_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_boundary_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        created_at = str(normalized.get("created_at") or now_iso())
        normalized["created_at"] = created_at
        if not str(normalized.get("expires_at") or "").strip():
            normalized["expires_at"] = approval_expiry_iso(
                created_at,
                risk_level=normalized.get("risk_level"),
            )
        if "policy_mode" not in normalized and "permission_mode" in normalized:
            normalized["policy_mode"] = normalized.get("permission_mode")
        if "permission_mode" not in normalized and "policy_mode" in normalized:
            normalized["permission_mode"] = normalized.get("policy_mode")
        if "runtime_control_fields" not in normalized and "runtime_fields" in normalized:
            normalized["runtime_control_fields"] = normalized.get("runtime_fields")
        if "runtime_fields" not in normalized and "runtime_control_fields" in normalized:
            normalized["runtime_fields"] = normalized.get("runtime_control_fields")
        return normalized


def approval_is_expired(approval: Approval | dict[str, Any], *, at: str | datetime | None = None) -> bool:
    expires_at = approval.expires_at if isinstance(approval, Approval) else str(approval.get("expires_at") or "")
    expiry = _parse_iso_datetime(expires_at)
    if expiry is None:
        return True
    if isinstance(at, datetime):
        current = at if at.tzinfo is not None else at.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
    elif isinstance(at, str):
        current = _parse_iso_datetime(at)
        if current is None:
            return True
    else:
        current = datetime.now(UTC)
    return expiry <= current


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("audit"))
    task_id: str | None = None
    event_type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    sequence: int = 0
    prev_hash: str = ""
    event_hash: str = ""
    hmac: str = ""
    created_at: str = Field(default_factory=now_iso)


class AuditChainVerification(BaseModel):
    ok: bool
    checked: int = 0
    last_event_id: str | None = None
    last_sequence: int = 0
    last_hash: str = ""
    failure_index: int | None = None
    failure_event_id: str | None = None
    failure_sequence: int | None = None
    failure_reason: str = ""
    failures: list[dict[str, Any]] = Field(default_factory=list)


class IndexedFile(BaseModel):
    id: str = Field(default_factory=lambda: new_id("file"))
    path: str
    normalized_path: str
    name: str
    extension: str
    size: int
    sha256: str
    created_at: str = ""
    modified_at: str = ""
    indexed_at: str = Field(default_factory=now_iso)
    mime_type: str = ""
    is_authorized: bool = True


class DocumentChunk(BaseModel):
    id: str = Field(default_factory=lambda: new_id("chunk"))
    file_id: str
    chunk_index: int
    text: str
    page: int | None = None
    sheet: str | None = None
    slide: int | None = None
    token_count: int = 0
    embedding_id: str | None = None


class ScheduledTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("schedule"))
    cron: str
    goal: str
    mode: str = "efficiency"
    enabled: bool = True
    last_run_at: str = ""
    next_run_at: str = ""
    last_status: str = ""
    last_task_id: str = ""
    note: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class WakeupStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


class Wakeup(BaseModel):
    id: str = Field(default_factory=lambda: new_id("wakeup"))
    source: str = "schedule"
    source_id: str = ""
    source_device_id: str = ""
    source_grant_id: str = ""
    allowed_device_ids: list[str] = Field(default_factory=list)
    title: str = ""
    body: str = ""
    goal: str = ""
    mode: str = "efficiency"
    status: WakeupStatus = WakeupStatus.PENDING
    run_id: str = ""
    error: str = ""
    due_at: str = Field(default_factory=now_iso)
    decided_at: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    principal_id: str = "local-user"
    workspace_id: str = "default"
    domain_scope: str = "general"
    kind: str = "fact"
    version: int = Field(default=1, ge=1)
    supersedes: str = ""
    conflict_status: MemoryConflictStatus = MemoryConflictStatus.NONE
    content: str
    tags: list[str] = Field(default_factory=list)
    task_id: str = ""
    source: str = "user"
    state: MemoryState = MemoryState.ACTIVE
    user_confirmed: bool = False
    expires_at: str = ""
    reviewed_at: str = ""
    reviewed_by: str = ""
    content_envelope: ContentEnvelope | None = None
    use_count: int = 0
    last_used_at: str = ""
    embedding_dim: int = 0
    created_at: str = Field(default_factory=now_iso)


class LocalLLMHealth(BaseModel):
    available: bool
    selected_backend: dict[str, Any] | None = None
    probe_order: list[str] = Field(default_factory=list)
    error: str = ""


class AgentAction(BaseModel):
    kind: str = "propose_tool"  # propose_tool | request_revision | done
    tool_name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    follow_up_question: str = ""
