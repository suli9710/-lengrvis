from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import StepPhase
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel, SafetyVerdict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


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
    "rolled_back": (TaskPhase.FAILED, ExecutionStage.IDLE),
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
    ROLLED_BACK = TaskPhase.FAILED


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


class ChatRequest(BaseModel):
    message: str
    mode: str = "efficiency"


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
    message: str
    mode: str = "efficiency"
    engine: RunEngine = RunEngine.AUTO


class RunCreateResponse(BaseModel):
    run_id: str
    engine: RunEngine
    phase: RunPhase


class RunStateResponse(BaseModel):
    run_id: str
    engine: RunEngine
    phase: RunPhase
    task_id: str | None = None
    message: str = ""
    mode: str = "efficiency"
    requested_engine: RunEngine = RunEngine.AUTO
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


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
    status: str = "created"
    dry_run: bool = True
    created_at: str = Field(default_factory=now_iso)


class ToolResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("result"))
    tool_call_id: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    rollback_info: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
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
    tool_version: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: str = Field(default_factory=now_iso)
    decided_at: str | None = None
    consumed_at: str | None = None


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("audit"))
    task_id: str | None = None
    event_type: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


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


class Memory(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    kind: str = "fact"
    content: str
    tags: list[str] = Field(default_factory=list)
    task_id: str = ""
    source: str = "user"
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
