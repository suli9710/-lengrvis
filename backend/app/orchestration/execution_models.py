from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EngineName = Literal["os", "developer"]
EngineSelection = Literal["auto", "os", "developer"]

# RunState is persisted in the ``runs.state`` JSON column.  Keep the
# checkpoint version separate from the database schema version: a run can be
# resumed after the application has upgraded while the surrounding database
# remains unchanged.  The in-memory model intentionally accepts only the
# current version; persisted legacy payloads are normalised by run_service
# before validation.
RUN_STATE_SCHEMA_VERSION = 3
CURRENT_RUN_STATE_SCHEMA_VERSION = RUN_STATE_SCHEMA_VERSION
MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION = RUN_STATE_SCHEMA_VERSION - 2


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


TERMINAL_RUN_PHASES: frozenset[RunPhase] = frozenset(
    {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.DENIED, RunPhase.CANCELLED}
)
NON_EXECUTABLE_RUN_PHASES: frozenset[RunPhase] = frozenset(
    {*TERMINAL_RUN_PHASES, RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED}
)

EngineRouteRule = Literal[
    "explicit_override",
    "developer_write_os",
    "developer_write_enabled",
    "developer_isolation_fallback",
    "developer_write_isolation_fallback",
    "developer_read_only_isolation_fallback",
    "system_diagnostics",
    "developer_read_only",
    "os_goal",
    "ambiguous_fallback",
]

RunContinuationKind = Literal["", "approval_remaining_steps"]
APPROVAL_REMAINING_STEPS_SUMMARY = "Approved modifying operation completed; continuing remaining plan steps."


class RunObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: int = 0
    source: str = ""
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class LargeResultRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    path: str = ""
    original_size: int = 0
    preview: str = ""
    has_more: bool = False


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``Literal`` makes accidental construction of an old/future in-memory
    # checkpoint fail closed.  The persistence boundary performs explicit
    # migrations for supported historical versions first.
    schema_version: Literal[RUN_STATE_SCHEMA_VERSION] = RUN_STATE_SCHEMA_VERSION
    run_id: str
    engine: EngineName
    phase: RunPhase = RunPhase.CREATED
    turn_count: int = 0
    transition_reason: str = ""
    current_plan: dict[str, Any] = Field(default_factory=dict)
    observations: list[RunObservation] = Field(default_factory=list)
    large_result_refs: list[LargeResultRef] = Field(default_factory=list)
    recovery_count_by_step: dict[str, int] = Field(default_factory=dict)
    goal: str = ""
    mode: str = "privacy"
    task_id: str = ""
    paused: bool = False
    route_rule: EngineRouteRule = "ambiguous_fallback"
    continuation_kind: RunContinuationKind = ""

    @field_validator("turn_count")
    @classmethod
    def validate_turn_count(cls, value: int) -> int:
        return max(0, value)


class EngineRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_engine: EngineSelection = "auto"
    selected_engine: EngineName
    reason: str
    rule: EngineRouteRule = "ambiguous_fallback"


class EngineTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: RunState
    finished: bool = False
    message: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    event_type: str
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
